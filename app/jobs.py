"""持久化整本书分析任务，支持暂停、恢复、重试和应用重启续跑。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.consolidation import build_analysis_context, consolidate_book
from app.control_plane import complete_run_manifest, create_run_manifest, render_prompt_bundle, stable_hash
from app.cost_control import (
    ADAPTIVE_MAX_COST_USD,
    ADAPTIVE_MAX_INPUT_TOKENS,
    ADAPTIVE_MAX_OUTPUT_TOKENS,
    adaptive_budget_limits,
    actual_usage_fits_budget,
    estimate_job_usage,
    estimate_segment_tokens,
    next_call_fits_budget,
    request_hash,
)
from app.db import connect, transaction
from app.models import ExtractionResult
from app.pipeline import persist_extraction
from app.pricing import PricingSnapshot, calculate_cost_usd, pricing_for
from app.providers import ProviderError, ProviderResponse, create_provider
from app.prompts import SYSTEM_PROMPT
from app.quality import build_quality_report
from app.quality_harness import run_quality_harness
from app.review import GLOBAL_REVIEW_VERSION, review_book


PROMPT_VERSION = "extract-v6-causal-memory-atlas"
ACTIVE_STATUSES = {"queued", "running", "paused"}


def _now() -> str:
    """返回带时区的统一时间文本。"""

    return datetime.now(timezone.utc).isoformat()


def _refresh_job(connection: sqlite3.Connection, job_id: int) -> sqlite3.Row:
    """从片段结果重新计算任务进度，避免异常退出造成计数漂移。"""

    totals = connection.execute(
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            COALESCE(SUM(accepted_facts), 0) AS accepted,
            COALESCE(SUM(rejected_facts), 0) AS rejected,
            COALESCE(SUM(input_tokens), 0) AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COALESCE(SUM(cache_hit_input_tokens), 0) AS cache_hit_input_tokens,
            COALESCE(SUM(cache_miss_input_tokens), 0) AS cache_miss_input_tokens
        FROM analysis_job_segments WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    review_usage = connection.execute(
        """
        SELECT input_tokens, output_tokens, cache_hit_input_tokens, cache_miss_input_tokens
        FROM analysis_job_review_usage WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    review_input = int(review_usage["input_tokens"]) if review_usage is not None else 0
    review_output = int(review_usage["output_tokens"]) if review_usage is not None else 0
    review_hit = int(review_usage["cache_hit_input_tokens"]) if review_usage is not None else 0
    review_miss = int(review_usage["cache_miss_input_tokens"]) if review_usage is not None else 0
    quality_usage = connection.execute(
        """
        SELECT input_tokens, output_tokens, cache_hit_input_tokens, cache_miss_input_tokens
        FROM analysis_job_quality_usage WHERE job_id = ?
        """,
        (job_id,),
    ).fetchone()
    quality_input = int(quality_usage["input_tokens"]) if quality_usage is not None else 0
    quality_output = int(quality_usage["output_tokens"]) if quality_usage is not None else 0
    quality_hit = int(quality_usage["cache_hit_input_tokens"]) if quality_usage is not None else 0
    quality_miss = int(quality_usage["cache_miss_input_tokens"]) if quality_usage is not None else 0
    job_before = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    if job_before is None:
        raise ValueError("分析任务不存在。")
    snapshot = PricingSnapshot(
        cache_hit_input_usd_per_million=job_before["cache_hit_input_usd_per_million"],
        cache_miss_input_usd_per_million=job_before["cache_miss_input_usd_per_million"],
        output_usd_per_million=job_before["output_usd_per_million"],
        source=str(job_before["pricing_source"]),
        effective_date=str(job_before["pricing_effective_date"]),
    )
    total_hit = int(totals["cache_hit_input_tokens"] or 0) + review_hit + quality_hit
    total_miss = int(totals["cache_miss_input_tokens"] or 0) + review_miss + quality_miss
    total_output = int(totals["output_tokens"] or 0) + review_output + quality_output
    estimated_cost = calculate_cost_usd(total_hit, total_miss, total_output, snapshot)
    ledger_cost = connection.execute(
        """
        SELECT COUNT(*) AS calls,
            COALESCE(SUM(CASE WHEN estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END), 0) AS priced_calls,
            COALESCE(SUM(estimated_cost_usd), 0) AS total_cost
        FROM model_call_ledger WHERE job_id = ? AND status IN ('completed', 'cache_reused', 'failed')
        """,
        (job_id,),
    ).fetchone()
    if int(ledger_cost["calls"] or 0) > 0 and int(ledger_cost["calls"] or 0) == int(ledger_cost["priced_calls"] or 0):
        estimated_cost = round(float(ledger_cost["total_cost"] or 0), 8)
    connection.execute(
        """
        UPDATE analysis_jobs SET
            total_segments = ?, completed_segments = ?, failed_segments = ?,
            accepted_facts = ?, rejected_facts = ?, input_tokens = ?, output_tokens = ?,
            cache_hit_input_tokens = ?, cache_miss_input_tokens = ?, estimated_cost_usd = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            totals["total"] or 0,
            totals["completed"] or 0,
            totals["failed"] or 0,
            totals["accepted"] or 0,
            totals["rejected"] or 0,
            (totals["input_tokens"] or 0) + review_input + quality_input,
            total_output,
            total_hit,
            total_miss,
            estimated_cost,
            job_id,
        ),
    )
    job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError("分析任务不存在。")
    if job["run_manifest_id"] is not None:
        connection.execute(
            """
            UPDATE model_call_ledger SET
                run_manifest_id = ?,
                prompt_hash = COALESCE(NULLIF(prompt_hash, ''), (
                    SELECT prompt_hash FROM run_manifests WHERE id = ?
                )),
                auth_mode = COALESCE(NULLIF(auth_mode, 'api'), (
                    SELECT auth_mode FROM run_manifests WHERE id = ?
                ))
            WHERE job_id = ?
            """,
            (job["run_manifest_id"], job["run_manifest_id"], job["run_manifest_id"], job_id),
        )
    return job


def refresh_job_metrics(settings: Settings, job_id: int) -> dict[str, Any]:
    """在专项复审后重新汇总任务用量和成本。"""

    with transaction(settings.database_path) as connection:
        return dict(_refresh_job(connection, job_id))


def create_job(
    settings: Settings,
    book_id: int,
    provider_name: str,
    start_segment: int,
    end_segment: int | None,
    max_retries: int,
    reanalyze: bool,
    max_cost_usd: float = 0.5,
    max_input_tokens: int = 500_000,
    max_output_tokens: int = 120_000,
    review_mode: str = "local",
    budget_mode: str = "adaptive",
) -> dict[str, Any]:
    """创建任务并只加入尚未由当前提示词完成的片段。"""

    provider = create_provider(settings, provider_name, book_id)
    prompt_version = provider.prompt_version("extraction", PROMPT_VERSION, SYSTEM_PROMPT)
    pricing = pricing_for(provider.name, provider.model)
    with transaction(settings.database_path) as connection:
        book = connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if book is None:
            raise ValueError("找不到这本书。")
        last_ordinal = max(0, int(book["segment_count"]) - 1)
        end = last_ordinal if end_segment is None else min(end_segment, last_ordinal)
        if start_segment > end:
            raise ValueError("分析起点已经超过书籍末尾。")
        estimate = estimate_job_usage(connection, book_id, start_segment, end, pricing, review_mode)
        active = connection.execute(
            """
            SELECT id FROM analysis_jobs
            WHERE book_id = ? AND status IN ('queued', 'running', 'paused')
            ORDER BY id DESC LIMIT 1
            """,
            (book_id,),
        ).fetchone()
        if active is not None:
            raise ValueError(f"这本书已有未结束任务：{active['id']}。")
        budget_adjustments = 0
        budget_status = "within_budget"
        if budget_mode == "adaptive":
            adapted_input = min(ADAPTIVE_MAX_INPUT_TOKENS, max(max_input_tokens, int(estimate.input_tokens * 1.2)))
            adapted_output = min(ADAPTIVE_MAX_OUTPUT_TOKENS, max(max_output_tokens, int(estimate.output_tokens * 1.2)))
            adapted_cost = max_cost_usd
            if estimate.estimated_cost_usd is not None:
                adapted_cost = min(ADAPTIVE_MAX_COST_USD, max(max_cost_usd, estimate.estimated_cost_usd * 1.2))
            if (adapted_cost, adapted_input, adapted_output) != (max_cost_usd, max_input_tokens, max_output_tokens):
                budget_adjustments = 1
                budget_status = "auto_expanded"
                max_cost_usd, max_input_tokens, max_output_tokens = adapted_cost, adapted_input, adapted_output
        cursor = connection.execute(
            """
            INSERT INTO analysis_jobs(
                book_id, provider, model, status, start_segment, end_segment,
                max_retries, prompt_version, cache_hit_input_usd_per_million,
                cache_miss_input_usd_per_million, output_usd_per_million,
                pricing_source, pricing_effective_date, max_cost_usd,
                max_input_tokens, max_output_tokens, estimated_before_start_usd,
                review_mode, budget_status, budget_mode, budget_adjustments
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, provider.name, provider.model, start_segment, end, max_retries,
                prompt_version, pricing.cache_hit_input_usd_per_million,
                pricing.cache_miss_input_usd_per_million, pricing.output_usd_per_million,
                pricing.source, pricing.effective_date,
                max_cost_usd, max_input_tokens, max_output_tokens,
                estimate.estimated_cost_usd, review_mode, budget_status, budget_mode, budget_adjustments,
            ),
        )
        job_id = int(cursor.lastrowid)
        rendered_prompt = render_prompt_bundle(connection, book_id, "extraction")
        manifest_id = create_run_manifest(
            connection,
            book_id=book_id,
            job_id=job_id,
            run_kind="book_analysis",
            provider=provider.name,
            model=provider.model,
            auth_mode=provider.auth_mode,
            prompt=rendered_prompt,
            input_scope={"start_segment": start_segment, "end_segment": end},
            input_hash=stable_hash(book["source_hash"], start_segment, end),
        )
        connection.execute(
            "UPDATE analysis_jobs SET run_manifest_id = ? WHERE id = ?",
            (manifest_id, job_id),
        )
        if reanalyze:
            segments = connection.execute(
                """
                SELECT id, ordinal FROM segments
                WHERE book_id = ? AND ordinal BETWEEN ? AND ? ORDER BY ordinal
                """,
                (book_id, start_segment, end),
            ).fetchall()
        else:
            segments = connection.execute(
                """
                SELECT s.id, s.ordinal FROM segments s
                WHERE s.book_id = ? AND s.ordinal BETWEEN ? AND ?
                  AND NOT EXISTS (
                    SELECT 1 FROM segment_results r
                    WHERE r.book_id = s.book_id AND r.segment_id = s.id AND r.prompt_version = ?
                  )
                ORDER BY s.ordinal
                """,
                (book_id, start_segment, end, prompt_version),
            ).fetchall()
        connection.executemany(
            "INSERT INTO analysis_job_segments(job_id, segment_id, ordinal) VALUES (?, ?, ?)",
            [(job_id, int(segment["id"]), int(segment["ordinal"])) for segment in segments],
        )
        budget_reasons: list[str] = []
        if estimate.input_tokens > max_input_tokens:
            budget_reasons.append("输入令牌预估超过上限")
        if estimate.output_tokens > max_output_tokens:
            budget_reasons.append("输出令牌预估超过上限")
        if estimate.estimated_cost_usd is not None and estimate.estimated_cost_usd > max_cost_usd:
            budget_reasons.append("金额预估超过上限")
        if budget_reasons and segments:
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'paused', budget_status = 'blocked', error = ?
                WHERE id = ?
                """,
                ("；".join(budget_reasons) + "。已达到全局紧急保护线，请确认后继续。", job_id),
            )
        if not segments and provider.name == "mock":
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'completed', completed_at = ?,
                    error = '所选片段已经完成，无需重复付费分析' WHERE id = ?
                """,
                (_now(), job_id),
            )
        elif not segments:
            connection.execute(
                "UPDATE analysis_jobs SET error = '片段已经完成，正在检查全书整理结果' WHERE id = ?",
                (job_id,),
            )
        job = _refresh_job(connection, job_id)
    return dict(job)


def estimate_job(
    settings: Settings,
    book_id: int,
    provider_name: str,
    start_segment: int,
    end_segment: int | None,
    review_mode: str,
) -> dict[str, Any]:
    """在创建任务前返回不产生模型调用的保守用量预估。"""

    provider = create_provider(settings, provider_name, book_id)
    pricing = pricing_for(provider.name, provider.model)
    with connect(settings.database_path) as connection:
        book = connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if book is None:
            raise ValueError("找不到这本书。")
        last_ordinal = max(0, int(book["segment_count"]) - 1)
        end = last_ordinal if end_segment is None else min(end_segment, last_ordinal)
        if start_segment > end:
            raise ValueError("分析起点已经超过书籍末尾。")
        estimate = estimate_job_usage(connection, book_id, start_segment, end, pricing, review_mode)
    return {
        "provider": provider.name,
        "model": provider.model,
        "start_segment": start_segment,
        "end_segment": end,
        "segment_count": estimate.segment_count,
        "estimated_input_tokens": estimate.input_tokens,
        "estimated_output_tokens": estimate.output_tokens,
        "estimated_cost_usd": estimate.estimated_cost_usd,
        "pricing_available": pricing.available,
        "pricing_source": pricing.source,
        "review_mode": review_mode,
        "estimate_kind": "conservative_upper_bound",
    }


def list_jobs(settings: Settings, book_id: int | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """列出任务和实时进度。"""

    with connect(settings.database_path) as connection:
        if book_id is None:
            result = connection.execute(
                "SELECT * FROM analysis_jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            result = connection.execute(
                "SELECT * FROM analysis_jobs WHERE book_id = ? ORDER BY id DESC LIMIT ?",
                (book_id, limit),
            ).fetchall()
    return [dict(item) for item in result]


def get_job(settings: Settings, job_id: int) -> dict[str, Any]:
    """返回单个任务和当前处理片段。"""

    with connect(settings.database_path) as connection:
        job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError("分析任务不存在。")
        current = connection.execute(
            """
            SELECT js.*, s.chapter_title FROM analysis_job_segments js
            JOIN segments s ON s.id = js.segment_id
            WHERE js.job_id = ? AND js.status IN ('running', 'pending')
            ORDER BY CASE js.status WHEN 'running' THEN 0 ELSE 1 END, js.ordinal LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    result = dict(job)
    result["current_segment"] = dict(current) if current is not None else None
    return result


def control_job(settings: Settings, job_id: int, action: str) -> dict[str, Any]:
    """变更任务状态，取消任务后保留已经完成的事实。"""

    with transaction(settings.database_path) as connection:
        job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError("分析任务不存在。")
        if action == "pause":
            if job["status"] not in {"queued", "running"}:
                raise ValueError("当前任务无法暂停。")
            status = "paused"
        elif action == "resume":
            if job["status"] != "paused":
                raise ValueError("只有暂停任务可以继续。")
            status = "queued"
        elif action == "cancel":
            if job["status"] not in ACTIVE_STATUSES:
                raise ValueError("当前任务已经结束。")
            status = "cancelled"
        elif action == "retry":
            if job["status"] != "failed":
                raise ValueError("只有失败任务可以重试。")
            connection.execute(
                """
                UPDATE analysis_job_segments SET status = 'pending', attempts = 0, error = '', updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ? AND status = 'failed'
                """,
                (job_id,),
            )
            status = "queued"
        else:
            raise ValueError("未知任务操作。")
        connection.execute(
            """
            UPDATE analysis_jobs SET status = ?, error = '',
                completed_at = CASE WHEN ? = 'retry' THEN NULL ELSE completed_at END,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (status, action, job_id),
        )
        updated = _refresh_job(connection, job_id)
        if action == "cancel" and updated["run_manifest_id"] is not None:
            complete_run_manifest(
                connection,
                int(updated["run_manifest_id"]),
                status="cancelled",
                input_tokens=int(updated["input_tokens"] or 0),
                output_tokens=int(updated["output_tokens"] or 0),
                estimated_cost_usd=updated["estimated_cost_usd"],
                validation={"completed_segments": int(updated["completed_segments"] or 0)},
            )
    return dict(updated)


def update_job_budget(
    settings: Settings,
    job_id: int,
    max_cost_usd: float,
    max_input_tokens: int,
    max_output_tokens: int,
    budget_mode: str = "adaptive",
) -> dict[str, Any]:
    """更新任务预算和自动适配策略；暂停状态保持不变。"""

    with transaction(settings.database_path) as connection:
        job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError("分析任务不存在。")
        connection.execute(
            """
            UPDATE analysis_jobs SET max_cost_usd = ?, max_input_tokens = ?, max_output_tokens = ?,
                budget_mode = ?, budget_status = 'within_budget', error = CASE
                    WHEN status = 'paused' THEN '预算已更新，请确认后继续任务'
                    ELSE error END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (max_cost_usd, max_input_tokens, max_output_tokens, budget_mode, job_id),
        )
        updated = _refresh_job(connection, job_id)
    return dict(updated)


def _auto_expand_job_budget(
    connection: sqlite3.Connection,
    job: sqlite3.Row,
    *,
    required_input_tokens: int,
    required_output_tokens: int,
) -> sqlite3.Row | None:
    """在紧急保护线内自动扩大累计用量范围，并记录扩展次数。"""

    expansion = adaptive_budget_limits(
        job,
        required_input_tokens=required_input_tokens,
        required_output_tokens=required_output_tokens,
    )
    if expansion is None:
        return None
    max_cost_usd, max_input_tokens, max_output_tokens, _ = expansion
    changed = (
        max_cost_usd > float(job["max_cost_usd"])
        or max_input_tokens > int(job["max_input_tokens"])
        or max_output_tokens > int(job["max_output_tokens"])
    )
    if changed:
        connection.execute(
            """
            UPDATE analysis_jobs SET max_cost_usd = ?, max_input_tokens = ?, max_output_tokens = ?,
                budget_status = 'auto_expanded', budget_adjustments = budget_adjustments + 1,
                error = '系统已依据实际用量自动扩展任务范围，分析继续运行',
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (max_cost_usd, max_input_tokens, max_output_tokens, int(job["id"])),
        )
    return connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (int(job["id"]),)).fetchone()


class AnalysisJobManager:
    """单进程后台执行器，顺序调用模型以控制花费和限流风险。"""

    def __init__(self, settings_provider: Callable[[], Settings]) -> None:
        self._settings_provider = settings_provider
        self._wake = asyncio.Event()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """启动后台循环。"""

        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="novel-analysis-worker")

    async def stop(self) -> None:
        """等待当前请求结束，再安全停止后台循环。"""

        self._stopping = True
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except TimeoutError:
                self._task.cancel()
            self._task = None

    def wake(self) -> None:
        """通知执行器立即检查新任务。"""

        self._wake.set()

    async def _run(self) -> None:
        """持续领取最早的等待任务。"""

        while not self._stopping:
            settings = self._settings_provider()
            with connect(settings.database_path) as connection:
                job = connection.execute(
                    "SELECT id FROM analysis_jobs WHERE status = 'queued' ORDER BY created_at, id LIMIT 1"
                ).fetchone()
            if job is None:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=1.0)
                except TimeoutError:
                    pass
                continue
            await self._process_job(int(job["id"]))

    async def _process_job(self, job_id: int) -> None:
        """逐片段处理一个任务，每成功一段就提交数据库。"""

        settings = self._settings_provider()
        with transaction(settings.database_path) as connection:
            job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None or job["status"] != "queued":
                return
            connection.execute(
                "UPDATE analysis_jobs SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (job_id,),
            )
        try:
            provider = create_provider(settings, str(job["provider"]), int(job["book_id"]))
            job_prompt_version = str(job["prompt_version"])
        except ProviderError as exc:
            self._fail_job(settings, job_id, str(exc))
            return

        while not self._stopping:
            with transaction(settings.database_path) as connection:
                current_job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
                if current_job is None or current_job["status"] != "running":
                    return
                item = connection.execute(
                    """
                    SELECT js.*, s.* FROM analysis_job_segments js
                    JOIN segments s ON s.id = js.segment_id
                    WHERE js.job_id = ? AND js.status = 'pending'
                    ORDER BY js.ordinal LIMIT 1
                    """,
                    (job_id,),
                ).fetchone()
                if item is None:
                    failed = connection.execute(
                        "SELECT COUNT(*) FROM analysis_job_segments WHERE job_id = ? AND status = 'failed'",
                        (job_id,),
                    ).fetchone()[0]
                    if failed:
                        connection.execute(
                            """
                            UPDATE analysis_jobs SET status = 'failed', completed_at = ?,
                                updated_at = CURRENT_TIMESTAMP WHERE id = ?
                            """,
                            (_now(), job_id),
                        )
                        _refresh_job(connection, job_id)
                        return
                    connection.execute(
                        """
                        UPDATE analysis_jobs SET error = '片段抽取完成，正在整理跨章节人物、时间和世界信息',
                            updated_at = CURRENT_TIMESTAMP WHERE id = ?
                        """,
                        (job_id,),
                    )
                    should_finish = True
                else:
                    should_finish = False
                    connection.execute(
                        """
                        UPDATE analysis_job_segments SET status = 'running', attempts = attempts + 1,
                            updated_at = CURRENT_TIMESTAMP WHERE id = ?
                        """,
                        (item["id"],),
                    )
            if should_finish:
                await self._finish_with_global_review(settings, provider, int(job["book_id"]), job_id)
                return

            response = None
            model_call_id: int | None = None
            try:
                with connect(settings.database_path) as connection:
                    context = build_analysis_context(connection, int(item["book_id"]), int(item["ordinal"]))
                    cache_key = request_hash(
                        provider.name,
                        provider.model,
                        job_prompt_version,
                        str(item["chapter_title"]),
                        int(item["ordinal"]),
                        str(item["text"]),
                        context,
                    )
                    cached = connection.execute(
                        "SELECT response_json FROM extraction_cache WHERE cache_key = ?",
                        (cache_key,),
                    ).fetchone()
                    current_job = connection.execute(
                        "SELECT * FROM analysis_jobs WHERE id = ?",
                        (job_id,),
                    ).fetchone()
                if cached is not None:
                    response = ProviderResponse(
                        extraction=ExtractionResult.model_validate_json(str(cached["response_json"])),
                    )
                    with transaction(settings.database_path) as connection:
                        connection.execute(
                            """
                            UPDATE analysis_jobs SET cache_reused_segments = cache_reused_segments + 1
                            WHERE id = ?
                            """,
                            (job_id,),
                        )
                        call_cursor = connection.execute(
                            """
                            INSERT INTO model_call_ledger(
                                book_id, job_id, purpose, provider, model, prompt_version,
                                request_hash, status, cache_hit, estimated_cost_usd
                            ) VALUES (?, ?, 'segment_extraction', ?, ?, ?, ?, 'cache_reused', 1, 0)
                            """,
                            (item["book_id"], job_id, provider.name, provider.model, job_prompt_version, cache_key),
                        )
                        model_call_id = int(call_cursor.lastrowid)
                else:
                    estimated_input, estimated_output = estimate_segment_tokens(str(item["text"]), len(context))
                    if current_job is None:
                        return
                    fits, reason, projected_cost = next_call_fits_budget(
                        current_job,
                        estimated_input,
                        estimated_output,
                    )
                    if not fits:
                        with transaction(settings.database_path) as connection:
                            latest_job = connection.execute(
                                "SELECT * FROM analysis_jobs WHERE id = ?", (job_id,),
                            ).fetchone()
                            expanded_job = _auto_expand_job_budget(
                                connection,
                                latest_job,
                                required_input_tokens=int(latest_job["input_tokens"] or 0) + estimated_input,
                                required_output_tokens=int(latest_job["output_tokens"] or 0) + estimated_output,
                            )
                            if expanded_job is not None:
                                current_job = expanded_job
                                fits, reason, projected_cost = next_call_fits_budget(
                                    current_job,
                                    estimated_input,
                                    estimated_output,
                                )
                            if not fits:
                                connection.execute(
                                    """
                                    UPDATE analysis_job_segments SET status = 'pending', error = ?,
                                        updated_at = CURRENT_TIMESTAMP WHERE id = ?
                                    """,
                                    (reason, item["id"]),
                                )
                                connection.execute(
                                    """
                                    UPDATE analysis_jobs SET status = 'paused', budget_status = 'blocked',
                                        error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                                    """,
                                    (
                                        f"{reason}；预计累计费用：{projected_cost:.6f} 美元。"
                                        if projected_cost is not None else reason,
                                        job_id,
                                    ),
                                )
                                _refresh_job(connection, job_id)
                        if not fits:
                            return
                    try:
                        response = await provider.extract(
                            str(item["chapter_title"]), int(item["ordinal"]), str(item["text"]), context
                        )
                    except Exception as exc:
                        failure_usage = getattr(exc, "usage", None)
                        with transaction(settings.database_path) as connection:
                            connection.execute(
                                """
                                INSERT INTO model_call_ledger(
                                    book_id, job_id, purpose, provider, model, prompt_version,
                                    request_hash, status, input_tokens, output_tokens,
                                    cache_hit_input_tokens, cache_miss_input_tokens, error
                                ) VALUES (?, ?, 'segment_extraction', ?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?)
                                """,
                                (
                                    item["book_id"], job_id, provider.name, provider.model, job_prompt_version,
                                    cache_key, int(getattr(failure_usage, "input_tokens", 0)),
                                    int(getattr(failure_usage, "output_tokens", 0)),
                                    int(getattr(failure_usage, "cache_hit_input_tokens", 0)),
                                    int(getattr(failure_usage, "cache_miss_input_tokens", 0)), str(exc)[:500],
                                ),
                            )
                        raise
                    with transaction(settings.database_path) as connection:
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO extraction_cache(
                                cache_key, provider, model, prompt_version, response_json,
                                input_tokens, output_tokens
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                cache_key, provider.name, provider.model, job_prompt_version,
                                response.extraction.model_dump_json(), response.input_tokens,
                                response.output_tokens,
                            ),
                        )
                        snapshot = pricing_for(provider.name, provider.model)
                        call_cost = calculate_cost_usd(
                            response.cache_hit_input_tokens,
                            response.cache_miss_input_tokens,
                            response.output_tokens,
                            snapshot,
                        )
                        call_cursor = connection.execute(
                            """
                            INSERT INTO model_call_ledger(
                                book_id, job_id, purpose, provider, model, prompt_version,
                                request_hash, status, input_tokens, output_tokens,
                                cache_hit_input_tokens, cache_miss_input_tokens, estimated_cost_usd
                            ) VALUES (?, ?, 'segment_extraction', ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)
                            """,
                            (
                                item["book_id"], job_id, provider.name, provider.model, job_prompt_version,
                                cache_key, response.input_tokens, response.output_tokens,
                                response.cache_hit_input_tokens, response.cache_miss_input_tokens, call_cost,
                            ),
                        )
                        model_call_id = int(call_cursor.lastrowid)
                with transaction(settings.database_path) as connection:
                    source_segment = dict(item)
                    source_segment["id"] = int(item["segment_id"])
                    try:
                        stats = persist_extraction(
                            connection,
                            int(item["book_id"]),
                            source_segment,
                            response.extraction,
                        )
                    except sqlite3.IntegrityError as exc:
                        raise RuntimeError(f"保存片段结构时数据库约束失败：{exc}") from exc
                    try:
                        consolidate_book(connection, int(item["book_id"]), int(item["ordinal"]))
                    except sqlite3.IntegrityError as exc:
                        raise RuntimeError(f"跨章节整理时数据库约束失败：{exc}") from exc
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO segment_results(
                            book_id, segment_id, provider, model, prompt_version,
                            input_tokens, output_tokens, cache_hit_input_tokens,
                            cache_miss_input_tokens, job_id, run_manifest_id, model_call_id, completed_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["book_id"], item["segment_id"], provider.name, provider.model,
                            job_prompt_version, response.input_tokens, response.output_tokens,
                            response.cache_hit_input_tokens, response.cache_miss_input_tokens,
                            job_id, current_job["run_manifest_id"], model_call_id, _now(),
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE evidence SET run_manifest_id = COALESCE(run_manifest_id, ?),
                            model_call_id = COALESCE(model_call_id, ?)
                        WHERE book_id = ? AND segment_id = ?
                        """,
                        (current_job["run_manifest_id"], model_call_id, item["book_id"], item["segment_id"]),
                    )
                    connection.execute(
                        """
                            UPDATE analysis_job_segments SET status = 'completed', accepted_facts = ?,
                            rejected_facts = ?, input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,
                            cache_hit_input_tokens = cache_hit_input_tokens + ?,
                            cache_miss_input_tokens = cache_miss_input_tokens + ?, error = '',
                            updated_at = CURRENT_TIMESTAMP WHERE id = ?
                        """,
                        (
                            stats.accepted, stats.rejected_without_evidence + response.structural_rejections,
                            response.input_tokens, response.output_tokens,
                            response.cache_hit_input_tokens, response.cache_miss_input_tokens,
                            item["id"],
                        ),
                    )
                    refreshed_job = _refresh_job(connection, job_id)
                    actual_fits, actual_reason = actual_usage_fits_budget(
                        refreshed_job,
                        input_tokens=int(refreshed_job["input_tokens"] or 0),
                        output_tokens=int(refreshed_job["output_tokens"] or 0),
                    )
                    if not actual_fits:
                        expanded_job = _auto_expand_job_budget(
                            connection,
                            refreshed_job,
                            required_input_tokens=int(refreshed_job["input_tokens"] or 0),
                            required_output_tokens=int(refreshed_job["output_tokens"] or 0),
                        )
                        if expanded_job is None:
                            connection.execute(
                                """
                                UPDATE analysis_jobs SET status = 'paused', budget_status = 'blocked',
                                    error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                                """,
                                (
                                    f"{actual_reason}。本次片段结果和费用已经保存；"
                                    "任务达到全局紧急保护线，请确认后继续。",
                                    job_id,
                                ),
                            )
            except Exception as exc:
                error = str(exc)[:600]
                with transaction(settings.database_path) as connection:
                    failure_usage = getattr(exc, "usage", None)
                    if failure_usage is None and response is not None:
                        failure_usage = response
                    if failure_usage is not None:
                        connection.execute(
                            """
                            UPDATE analysis_job_segments SET
                                input_tokens = input_tokens + ?, output_tokens = output_tokens + ?,
                                cache_hit_input_tokens = cache_hit_input_tokens + ?,
                                cache_miss_input_tokens = cache_miss_input_tokens + ?
                            WHERE id = ?
                            """,
                            (
                                failure_usage.input_tokens, failure_usage.output_tokens,
                                failure_usage.cache_hit_input_tokens, failure_usage.cache_miss_input_tokens,
                                item["id"],
                            ),
                        )
                    attempt = connection.execute(
                        "SELECT attempts FROM analysis_job_segments WHERE id = ?",
                        (item["id"],),
                    ).fetchone()["attempts"]
                    max_retries = connection.execute(
                        "SELECT max_retries FROM analysis_jobs WHERE id = ?",
                        (job_id,),
                    ).fetchone()["max_retries"]
                    terminal = int(attempt) >= int(max_retries)
                    connection.execute(
                        """
                        UPDATE analysis_job_segments SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        ("failed" if terminal else "pending", error, item["id"]),
                    )
                    _refresh_job(connection, job_id)
                if terminal:
                    self._fail_job(settings, job_id, f"片段 {item['ordinal'] + 1}：{error}")
                    return
                await asyncio.sleep(min(8, 2 ** max(0, int(attempt) - 1)))

    async def _finish_with_global_review(
        self,
        settings: Settings,
        provider: Any,
        book_id: int,
        job_id: int,
    ) -> None:
        """完成低频全书整理；整理失败时保留片段抽取结果并显示警告。"""

        warning = ""
        usage = {
            "batches": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hit_input_tokens": 0,
            "cache_miss_input_tokens": 0,
        }
        with connect(settings.database_path) as connection:
            job = connection.execute("SELECT review_mode FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
        if job is not None and job["review_mode"] == "full":
            try:
                usage = await review_book(settings, provider, book_id)
            except Exception as exc:
                warning = f"片段分析已完成；全书整理失败：{str(exc)[:420]}"
        else:
            with transaction(settings.database_path) as connection:
                consolidate_book(connection, book_id, 1_000_000)
            warning = "片段分析和本地全书整理已完成；未调用付费全书复审"
        # 无论后续批次是否失败，都从已落盘批次重新汇总费用，已完成调用不能从账单中消失。
        if job is not None and job["review_mode"] == "full":
            with connect(settings.database_path) as connection:
                review_totals = connection.execute(
                    """
                    SELECT COUNT(*) AS batches, COALESCE(SUM(b.input_tokens), 0) AS input_tokens,
                        COALESCE(SUM(b.output_tokens), 0) AS output_tokens,
                        COALESCE(SUM(b.cache_hit_input_tokens), 0) AS cache_hit_input_tokens,
                        COALESCE(SUM(b.cache_miss_input_tokens), 0) AS cache_miss_input_tokens
                    FROM global_review_batches b
                    JOIN analysis_jobs j ON j.id = ?
                    WHERE b.book_id = ? AND b.provider = ? AND b.model = ? AND b.prompt_version = ?
                      AND b.status = 'completed' AND b.completed_at >= j.created_at
                    """,
                    (job_id, book_id, provider.name, provider.model, GLOBAL_REVIEW_VERSION),
                ).fetchone()
                usage = {key: int(review_totals[key] or 0) for key in usage}
        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                INSERT INTO analysis_job_review_usage(
                    job_id, batches, input_tokens, output_tokens,
                    cache_hit_input_tokens, cache_miss_input_tokens
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    batches = excluded.batches,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    cache_hit_input_tokens = excluded.cache_hit_input_tokens,
                    cache_miss_input_tokens = excluded.cache_miss_input_tokens
                """,
                (
                    job_id, usage["batches"], usage["input_tokens"], usage["output_tokens"],
                    usage["cache_hit_input_tokens"], usage["cache_miss_input_tokens"],
                ),
            )
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'quality_checking', error = ?,
                    quality_gate_status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (warning, job_id),
            )
            _refresh_job(connection, job_id)
        harness_warning = ""
        try:
            harness_summary = await run_quality_harness(settings, provider, book_id, job_id)
            with connect(settings.database_path) as connection:
                ambiguous_count = int(connection.execute(
                    "SELECT COUNT(*) FROM entity_connectivity_reviews WHERE book_id = ? AND status = 'ambiguous'",
                    (book_id,),
                ).fetchone()[0])
            if (
                ambiguous_count
                and provider.name == "deepseek"
                and provider.model == "deepseek-v4-flash"
                and not harness_summary.get("stopped_for_budget")
            ):
                refresh_job_metrics(settings, job_id)
                strong_provider = create_provider(
                    replace(settings, deepseek_model="deepseek-v4-pro"), "deepseek", book_id
                )
                strong_summary = await run_quality_harness(
                    settings, strong_provider, book_id, job_id, include_ambiguous=True,
                )
                harness_summary["strong_model_calls"] = strong_summary.get("calls", 0)
                harness_summary["stopped_for_budget"] = strong_summary.get("stopped_for_budget", False)
            if harness_summary.get("stopped_for_budget"):
                harness_warning = "专项关系复审达到预算上限，未闭环节点已经进入可解决清单。"
        except Exception as exc:
            harness_warning = f"专项质量复审失败：{str(exc)[:420]}"
        with transaction(settings.database_path) as connection:
            _refresh_job(connection, job_id)
            book = connection.execute("SELECT segment_count FROM books WHERE id = ?", (book_id,)).fetchone()
            visible = max(0, int(book["segment_count"] or 1) - 1) if book is not None else 0
            report = build_quality_report(connection, book_id, visible)
            gate_passed = bool(report.get("quality_gate_passed")) and not harness_warning
            snapshot = connection.execute(
                """
                INSERT INTO quality_gate_snapshots(book_id, job_id, status, report_json)
                VALUES (?, ?, ?, ?)
                """,
                (book_id, job_id, "passed" if gate_passed else "needs_review", json.dumps(report, ensure_ascii=False)),
            )
            final_messages = [message for message in (warning, harness_warning) if message]
            if not gate_passed and not harness_warning:
                final_messages.append("自动质量门禁尚未通过；所有冲突均可自动重试或人工解决。")
            connection.execute(
                """
                UPDATE analysis_jobs SET status = ?, error = ?, completed_at = ?,
                    quality_gate_status = ?, quality_gate_snapshot_id = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    "completed" if gate_passed else "needs_review",
                    "；".join(final_messages), _now(),
                    "passed" if gate_passed else "needs_review", int(snapshot.lastrowid), job_id,
                ),
            )
            refreshed_job = _refresh_job(connection, job_id)
            if refreshed_job["run_manifest_id"] is not None:
                complete_run_manifest(
                    connection,
                    int(refreshed_job["run_manifest_id"]),
                    status="completed" if gate_passed else "needs_review",
                    input_tokens=int(refreshed_job["input_tokens"] or 0),
                    output_tokens=int(refreshed_job["output_tokens"] or 0),
                    estimated_cost_usd=refreshed_job["estimated_cost_usd"],
                    validation=report,
                    conflicts=[{"message": message} for message in final_messages],
                )

    def _fail_job(self, settings: Settings, job_id: int, error: str) -> None:
        """记录失败并保留所有已完成片段。"""

        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'failed', error = ?, completed_at = ?,
                    updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (error[:600], _now(), job_id),
            )
            job = _refresh_job(connection, job_id)
            if job["run_manifest_id"] is not None:
                complete_run_manifest(
                    connection,
                    int(job["run_manifest_id"]),
                    status="failed",
                    input_tokens=int(job["input_tokens"] or 0),
                    output_tokens=int(job["output_tokens"] or 0),
                    estimated_cost_usd=job["estimated_cost_usd"],
                    conflicts=[{"message": error[:600]}],
                )
