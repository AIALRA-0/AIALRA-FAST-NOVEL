"""FastAPI 入口：导入、分析、查询、证据跳转与人工审核。"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from difflib import unified_diff
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from app.config import Settings, load_settings
from app.benchmarks import evaluate_benchmarks, evaluation_progress
from app.benchmark_curation import candidate_payload, refresh_benchmark_candidates
from app.control_plane import (
    PROMPT_TASKS,
    complete_run_manifest,
    create_run_manifest,
    manifest_payload,
    parse_json,
    product_contract,
    prompt_bundle_payload,
    render_prompt_bundle,
    stable_hash,
    suite_version,
)
from app.cost_control import estimate_segment_tokens, request_hash
from app.db import connect, initialize, transaction
from app.importers import ImportErrorDetail, parse_book
from app.consolidation import build_analysis_context, consolidate_book, register_entity_keys
from app.atlas import LAYOUT_VERSION, build_map_layout_snapshot
from app.knowledge import (
    concept_payload,
    facet_payload,
    knowledge_claim_payload,
    record_revision,
    revision_payload,
    sync_knowledge_projection,
)
from app.narrative import narrative_memory_payload
from app.narrative_partition import (
    get_narrative_structure,
    merge_story_worlds,
    move_narrative_unit,
    rebuild_narrative_structure,
    split_story_world,
)
from app.review_tasks import list_review_tasks, resolve_review_task, sync_review_tasks
from app.systems import story_knowledge_context, systems_payload
from app.jobs import (
    PROMPT_VERSION,
    AnalysisJobManager,
    control_job,
    create_job,
    estimate_job,
    get_job,
    list_jobs,
    refresh_job_metrics,
    update_job_budget,
)
from app.library import list_book_updates, preview_book_update, resolve_book_update
from app.relations import normalize_relation_semantics
from app.models import (
    AnalysisBudgetPatch,
    BenchmarkCandidateResolve,
    BenchmarkCaseCreate,
    BenchmarkCasePatch,
    BenchmarkSecondReview,
    CollaborationItemCreate,
    CollaborationItemPatch,
    AnalysisJobAction,
    AnalysisJobRequest,
    AnalyzeRequest,
    BookPatch,
    BookSettingsPatch,
    BookUpdateResolution,
    NarrativeRebuildRequest,
    StoryWorldMergeRequest,
    StoryWorldSplitRequest,
    NarrativeUnitMoveRequest,
    ReviewTaskPatch,
    ClaimPatch,
    ConnectivityLinkCreate,
    ConnectivityReviewPatch,
    ConceptCreate,
    ConceptPatch,
    ContradictionPatch,
    EventLocationReviewPatch,
    ExternalFactCreate,
    ExternalFactPatch,
    EntityMergeRequest,
    MergeCandidatePatch,
    ModelRaceRequest,
    ModelRoutePatch,
    LibraryFolderRequest,
    KnowledgeClaimCreate,
    KnowledgeClaimPatch,
    KnowledgeCompleteRequest,
    ProviderKeyRequest,
    PromptDraftCreate,
    PromptTrialRequest,
    RecordDraftRequest,
    RelationshipLayoutPatch,
    RecordPatch,
    TimeConflictPatch,
    DomainRuleCreate,
    DomainRulePatch,
    WorldNoteCreate,
    SystemCreate,
    SystemPatch,
    SystemNodeCreate,
    SystemNodePatch,
    SystemRelationCreate,
    SystemRelationPatch,
    UiIssueCreate,
    UiIssuePatch,
)
from app.pipeline import add_evidence, analyze_book, find_quote, recover_cached_extractions
from app.pricing import calculate_cost_usd, pricing_for
from app.providers import ProviderError, codex_cli_status, create_provider
from app.providers import probe_provider as probe_model_provider
from app.prompts import build_user_prompt
from app.quality import build_quality_report
from app.quality_harness import refresh_local_reviews, run_quality_harness
from app.reading_window import ReadingWindow, mark_context_only, resolve_reading_window
from app.semantic import (
    cluster_for_entity,
    conservatively_close_conflicts,
    merge_identity_clusters,
    recompute_chronology_dag,
    rebuild_derived_journey,
    select_main_subject,
    undo_identity_decision,
)
from app.secrets import delete_provider_secret, save_provider_secret
from app.seed import seed_demo


ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
STATIC_DIR = ROOT / "static"
APP_VERSION = "2.9.7-rc.3"
BUILD_COMMIT = os.getenv("NOVEL_BUILD_COMMIT", "local")
BUILD_TIME = os.getenv("NOVEL_BUILD_TIME", "local")
DATABASE_SCHEMA_VERSION = "2.9.8"
settings: Settings = load_settings()
job_manager: AnalysisJobManager | None = None
derived_view_lock = threading.RLock()
provider_probe_cache: dict[str, dict[str, Any]] = {}
if not settings.database_path.is_absolute():
    settings = Settings(
        **{**settings.__dict__, "database_path": ROOT / settings.database_path}
    )

@asynccontextmanager
async def lifespan(_: FastAPI):
    """初始化数据库，并启动可恢复的整本书后台任务。"""

    global job_manager
    initialize(settings.database_path)
    seed_demo(settings.database_path)
    with transaction(settings.database_path) as connection:
        book_ids = connection.execute("SELECT id, segment_count FROM books").fetchall()
        for item in book_ids:
            structure = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM events WHERE book_id = ?) AS events,
                    (SELECT COUNT(*) FROM events WHERE book_id = ? AND location_entity_id IS NOT NULL) AS located,
                    (SELECT COUNT(*) FROM place_relations WHERE book_id = ?) AS place_relations,
                    (SELECT COUNT(*) FROM journey_legs WHERE book_id = ?) AS journey_legs,
                    (SELECT COUNT(*) FROM model_call_ledger WHERE book_id = ?
                        AND purpose = 'segment_extraction' AND status = 'completed') AS cached_calls
                """,
                (item["id"], item["id"], item["id"], item["id"], item["id"]),
            ).fetchone()
            if (
                structure is not None
                and int(structure["cached_calls"] or 0) > 0
                and connection.execute(
                    "SELECT 1 FROM maintenance_runs WHERE book_id = ? AND repair_key = 'cached-structure-v1'",
                    (item["id"],),
                ).fetchone() is None
                and int(structure["events"] or 0) >= 5
                and (
                    int(structure["located"] or 0) * 2 < int(structure["events"] or 0)
                    or int(structure["place_relations"] or 0) + int(structure["journey_legs"] or 0) == 0
                )
            ):
                repair_result = recover_cached_extractions(connection, int(item["id"]))
                connection.execute(
                    "INSERT OR REPLACE INTO maintenance_runs(book_id, repair_key, result_json) VALUES (?, 'cached-structure-v1', ?)",
                    (item["id"], json.dumps(repair_result, ensure_ascii=False)),
                )
            consolidate_book(connection, int(item["id"]), max(0, int(item["segment_count"]) - 1))
            refresh_local_reviews(connection, int(item["id"]))
    job_manager = AnalysisJobManager(lambda: settings)
    job_manager.start()
    try:
        yield
    finally:
        await job_manager.stop()
        job_manager = None


app = FastAPI(title="小说证据图谱", version=APP_VERSION, lifespan=lifespan)


def rows(items: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """把 SQLite 行转换为可序列化对象。"""

    return [dict(item) for item in items]


def ensure_book(connection: sqlite3.Connection, book_id: int) -> sqlite3.Row:
    """读取书籍或返回清晰的 404。"""

    book = connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        raise HTTPException(status_code=404, detail="找不到这本书。")
    return book


def _benchmark_expected(
    case_type: str,
    expected: dict[str, Any],
    segment_count: int,
) -> dict[str, Any]:
    """校验金标准期望值，避免无效用例让准确率门禁失真。"""

    def text_field(name: str) -> str:
        value = str(expected.get(name, "")).strip()
        if not value:
            raise HTTPException(status_code=422, detail=f"金标准缺少“{name}”。")
        return value

    def segment_field(name: str) -> int:
        value = expected.get(name)
        if isinstance(value, bool):
            raise HTTPException(status_code=422, detail=f"金标准“{name}”必须是章节序号。")
        try:
            ordinal = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"金标准“{name}”必须是章节序号。") from exc
        if ordinal < 0 or ordinal >= segment_count:
            raise HTTPException(status_code=422, detail="金标准引用的章节超出本书范围。")
        return ordinal

    if case_type in {"identity_same", "identity_distinct"}:
        left = text_field("left")
        right = text_field("right")
        if left == right:
            raise HTTPException(status_code=422, detail="金标准中的两个名称不能相同。")
        return {"left": left, "right": right}
    if case_type == "event_present":
        return {"title": text_field("title"), "max_segment": segment_field("max_segment")}
    if case_type == "event_before":
        earlier = text_field("earlier")
        later = text_field("later")
        if earlier == later:
            raise HTTPException(status_code=422, detail="前后两个事件不能相同。")
        return {"earlier": earlier, "later": later}
    if case_type == "main_subject":
        return {"name": text_field("name")}
    if case_type == "journey_start":
        return {"max_segment": segment_field("max_segment")}
    if case_type in {"segment_accounting", "fact_evidence", "quote_integrity"}:
        value = expected.get("percent")
        if isinstance(value, bool):
            raise HTTPException(status_code=422, detail="完整性金标准必须填写百分比。")
        try:
            percent = float(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="完整性金标准必须填写百分比。") from exc
        if percent < 0 or percent > 100:
            raise HTTPException(status_code=422, detail="完整性金标准百分比必须介于 0 和 100。")
        return {"percent": percent}
    raise HTTPException(status_code=422, detail="不支持的金标准类型。")


def _benchmark_case_payload(row: sqlite3.Row, *, reveal_holdout: bool = True) -> dict[str, Any]:
    """把 JSON 字段转换为前端可直接展示的金标准记录。"""

    item = dict(row)
    for field in ("expected_json", "actual_json"):
        try:
            item[field.removesuffix("_json")] = json.loads(str(item[field] or "{}"))
        except json.JSONDecodeError:
            item[field.removesuffix("_json")] = {}
        item.pop(field, None)
    item["passed"] = None if item["passed"] is None else bool(item["passed"])
    item["critical"] = bool(item["critical"])
    item["holdout"] = bool(item.get("holdout", 0))
    item["confirmed_by_user"] = bool(item.get("confirmed_by_user", 1))
    item["review_status"] = str(item.get("review_status") or "candidate")
    item["second_review_status"] = str(item.get("second_review_status") or "not_required")
    if item["review_status"] == "sealed_holdout" and not reveal_holdout:
        item["expected"] = {"withheld": True}
        item["actual"] = {"withheld": True}
        item["passed"] = None
        item["note"] = "保留测试答案已隐藏，只在正式门禁中计算。"
    return item


def _benchmark_review_audit(
    connection: sqlite3.Connection,
    book_id: int,
    source_segment: int,
    expected: dict[str, Any],
    *,
    confirmed: bool,
    holdout: bool,
    critical: bool,
    reviewer_id: str,
    reviewer_role: str,
    review_session: str,
) -> dict[str, Any]:
    """Build review provenance from the exact source text shown to the reviewer."""

    segment = connection.execute(
        "SELECT text FROM segments WHERE book_id = ? AND ordinal = ?",
        (book_id, source_segment),
    ).fetchone()
    evidence_hash = stable_hash(
        str(book_id), str(source_segment), str(segment["text"] if segment is not None else ""),
        json.dumps(expected, ensure_ascii=False, sort_keys=True),
    )
    status = "candidate"
    if confirmed:
        status = "sealed_holdout" if holdout else "confirmed_development"
    return {
        "review_status": status,
        "reviewer_id": reviewer_id.strip() if confirmed else "",
        "reviewer_role": reviewer_role.strip() if confirmed else "",
        "review_session": review_session.strip() if confirmed else "",
        "review_evidence_hash": evidence_hash if confirmed else "",
        "second_review_status": "pending" if confirmed and (critical or holdout) else "not_required",
    }


def _list_benchmark_cases(
    connection: sqlite3.Connection,
    book_id: int,
    *,
    reveal_holdout: bool = True,
) -> list[dict[str, Any]]:
    """读取金标准及其引用章节，供质量页面逐条审核。"""

    rows_found = connection.execute(
        """
        SELECT benchmark.*, segment.chapter_title AS source_chapter_title
        FROM quality_benchmark_cases benchmark
        LEFT JOIN segments segment
          ON segment.book_id = benchmark.book_id AND segment.ordinal = benchmark.source_segment
        WHERE benchmark.book_id = ?
        ORDER BY benchmark.critical DESC, benchmark.source_segment, benchmark.id
        """,
        (book_id,),
    ).fetchall()
    return [_benchmark_case_payload(row, reveal_holdout=reveal_holdout) for row in rows_found]


def describe_review_target(
    connection: sqlite3.Connection,
    book_id: int,
    target_type: str,
    target_id: int,
) -> dict[str, Any]:
    """把冲突两侧转换成用户能识别的名称和说明。"""

    if target_type == "place_relation":
        row = connection.execute(
            """
            SELECT relation.id,
                source.name || ' · ' || relation.relative_position || ' · ' || target.name AS label,
                relation.summary
            FROM place_relations relation
            JOIN entities source ON source.id = relation.source_entity_id
            JOIN entities target ON target.id = relation.target_entity_id
            WHERE relation.id = ? AND relation.book_id = ?
            """,
            (target_id, book_id),
        ).fetchone()
    else:
        table_map = {
            "entity": ("entities", "name"),
            "claim": ("claims", "predicate"),
            "event": ("events", "title"),
            "world_note": ("world_notes", "title"),
            "entry": ("entries", "name"),
        }
        table_spec = table_map.get(target_type)
        if table_spec is None:
            row = None
        else:
            table, label_column = table_spec
            row = connection.execute(
                f"SELECT id, {label_column} AS label, summary FROM {table} WHERE id = ? AND book_id = ?",
                (target_id, book_id),
            ).fetchone()
    if row is None:
        return {"type": target_type, "id": target_id, "label": f"已移除记录 #{target_id}", "summary": ""}
    return {
        "type": target_type,
        "id": target_id,
        "label": str(row["label"]),
        "summary": str(row["summary"]),
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    """提供进程健康状态。"""

    return {"status": "ok"}


@app.get("/api/providers")
def providers() -> list[dict[str, Any]]:
    """返回供应商配置、连通性和模型状态，不泄露密钥内容。"""

    codex = codex_cli_status()
    codex_cached = provider_probe_cache.get("codex_luna", {})
    entries: list[dict[str, Any]] = [
        {
            "id": "auto", "label": "自动选择已达标模型", "available": True,
            "configured": True, "reachable": True, "model_available": True,
            "status": "ready", "last_checked_at": None, "error_code": None,
            "requires_key": False, "note": "质量优先，再比较费用和速度",
        },
        {
            "id": "mock", "label": "本地演示解析器", "available": True,
            "configured": True, "reachable": True, "model_available": True,
            "status": "local", "last_checked_at": None, "error_code": None,
            "requires_key": False, "note": "功能演示，不调用云端模型",
        },
    ]
    configured_specs = (
        ("deepseek", "DeepSeek 开放平台", settings.deepseek_api_key, settings.deepseek_base_url, settings.deepseek_model, "需要 DeepSeek 开放平台密钥"),
        ("moonshot", "Moonshot 开放平台", settings.moonshot_api_key, settings.moonshot_base_url, settings.moonshot_model, "需要 Moonshot 开放平台密钥，不接受 Kimi Code 订阅凭据"),
    )
    for provider_id, label, api_key, base_url, model, note in configured_specs:
        configured = bool(api_key)
        # A previous successful probe must not survive key removal or a
        # configuration reload; an unconfigured provider is never routable.
        cached = provider_probe_cache.get(provider_id, {}) if configured else {}
        status = str(cached.get("status") or ("configured" if configured else "unconfigured")) if configured else "unconfigured"
        entries.append({
            "id": provider_id, "label": label,
            "available": configured and status not in {"auth_failed", "timeout", "service_error", "model_unavailable"},
            "configured": configured,
            "reachable": bool(cached.get("reachable")) if cached else False,
            "model_available": bool(cached.get("model_available")) if cached else False,
            "status": status,
            "last_checked_at": cached.get("last_checked_at"),
            "error_code": cached.get("error_code"),
            "requires_key": not configured,
            "note": model if configured else note,
            "model": model,
        })
    codex_configured = bool(codex.get("available"))
    codex_status = str(codex_cached.get("status") or ("connected" if codex_configured else "unconfigured"))
    codex_reachable = bool(codex_cached.get("reachable", codex_configured))
    codex_model_available = bool(codex_cached.get("model_available", codex_configured))
    entries.append({
        "id": "codex_luna", "label": "Codex Luna · 本机 ChatGPT 登录",
        "available": codex_reachable and codex_model_available, "configured": codex_configured,
        "reachable": codex_reachable, "model_available": codex_model_available,
        "status": codex_status,
        "last_checked_at": codex_cached.get("last_checked_at"),
        "error_code": codex_cached.get("error_code") or (None if codex_configured else "not_logged_in"),
        "requires_key": False, "note": codex["message"],
        "auth_mode": "chatgpt_login", "model": codex["model"], "version": codex["version"],
    })
    return entries


@app.post("/api/providers/{provider}/probe")
async def probe_model_provider_status(provider: str) -> dict[str, Any]:
    """探测模型目录和本机登录状态；不发送小说正文，也不改变自动路由资格。"""

    if provider == "deepseek":
        result = await probe_model_provider(
            provider, api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url, model=settings.deepseek_model,
        )
    elif provider == "moonshot":
        result = await probe_model_provider(
            provider, api_key=settings.moonshot_api_key,
            base_url=settings.moonshot_base_url, model=settings.moonshot_model,
        )
    elif provider == "codex_luna":
        result = await probe_model_provider(provider)
    else:
        raise HTTPException(status_code=404, detail="未知模型供应商")
    provider_probe_cache[provider] = dict(result)
    return result


@app.get("/api/providers/codex-luna/preflight")
def codex_luna_preflight() -> dict[str, Any]:
    """检查本机 CLI、ChatGPT 登录和模型配置，不产生模型调用。"""

    return codex_cli_status()


@app.post("/api/settings/provider-key")
def save_provider_key(request: ProviderKeyRequest) -> dict[str, Any]:
    """使用 Windows 当前账户加密保存开放平台密钥。"""

    global settings
    if request.provider not in {"deepseek", "moonshot"}:
        raise HTTPException(status_code=404, detail="未知模型供应商")
    if request.provider == "moonshot" and request.api_key.startswith("sk-kimi-"):
        raise HTTPException(
            status_code=422,
            detail="这是 Kimi Code 订阅密钥。整本书批处理需要 Moonshot 开放平台密钥。",
        )
    try:
        save_provider_secret(request.provider, request.api_key)
    except (RuntimeError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.provider == "deepseek":
        settings = replace(settings, deepseek_api_key=request.api_key)
    else:
        settings = replace(settings, moonshot_api_key=request.api_key)
    provider_probe_cache.pop(request.provider, None)
    return {"provider": request.provider, "configured": True}


@app.delete("/api/settings/provider-key/{provider}", status_code=204)
def remove_provider_key(provider: str) -> Response:
    """删除本机保存的供应商密钥；环境变量提供的密钥仍然有效。"""

    global settings
    if provider not in {"deepseek", "moonshot"}:
        raise HTTPException(status_code=404, detail="未知模型供应商。")
    delete_provider_secret(provider)
    if provider == "deepseek":
        settings = replace(settings, deepseek_api_key=os.getenv("DEEPSEEK_API_KEY") or None)
    else:
        settings = replace(settings, moonshot_api_key=os.getenv("MOONSHOT_API_KEY") or None)
    provider_probe_cache.pop(provider, None)
    return Response(status_code=204)


@app.get("/api/books")
def list_books() -> list[dict[str, Any]]:
    """列出本机书库和结构统计。"""

    with connect(settings.database_path) as connection:
        result = connection.execute(
            """
            SELECT b.*,
                f.name AS folder_name,
                (SELECT COUNT(*) FROM entities e WHERE e.book_id = b.id) AS entity_count,
                (SELECT COUNT(*) FROM events v WHERE v.book_id = b.id) AS event_count,
                (SELECT COUNT(DISTINCT js.ordinal) FROM analysis_job_segments js
                 JOIN analysis_jobs j ON j.id = js.job_id
                 WHERE j.book_id = b.id AND js.status IN ('completed', 'cached')) AS analyzed_segment_count
            FROM books b LEFT JOIN library_folders f ON f.id = b.folder_id
            ORDER BY COALESCE(f.sort_order, -1), COALESCE(f.name, ''), b.updated_at DESC, b.id DESC
            """
        ).fetchall()
    return rows(result)


@app.get("/api/library/folders")
def list_library_folders() -> list[dict[str, Any]]:
    """返回全部文件夹和各自直接包含的书籍数量。"""

    with connect(settings.database_path) as connection:
        result = connection.execute(
            """
            SELECT f.*,
                (SELECT COUNT(*) FROM books b WHERE b.folder_id = f.id) AS book_count,
                (SELECT COUNT(*) FROM library_folders c WHERE c.parent_id = f.id) AS child_count
            FROM library_folders f ORDER BY f.sort_order, f.name, f.id
            """
        ).fetchall()
    return rows(result)


def _ensure_folder(connection: sqlite3.Connection, folder_id: int | None) -> sqlite3.Row | None:
    """验证文件夹存在；根目录使用空值。"""

    if folder_id is None:
        return None
    folder = connection.execute("SELECT * FROM library_folders WHERE id = ?", (folder_id,)).fetchone()
    if folder is None:
        raise HTTPException(status_code=404, detail="找不到这个文件夹。")
    return folder


def _folder_name_taken(
    connection: sqlite3.Connection, name: str, parent_id: int | None, exclude_id: int | None = None
) -> bool:
    """根目录和子目录都执行同一级名称去重。"""

    row = connection.execute(
        "SELECT id FROM library_folders WHERE name = ? AND parent_id IS ? AND (? IS NULL OR id != ?)",
        (name, parent_id, exclude_id, exclude_id),
    ).fetchone()
    return row is not None


@app.post("/api/library/folders", status_code=201)
def create_library_folder(request: LibraryFolderRequest) -> dict[str, Any]:
    """创建根文件夹或子文件夹。"""

    name = request.name.strip()
    with transaction(settings.database_path) as connection:
        _ensure_folder(connection, request.parent_id)
        if _folder_name_taken(connection, name, request.parent_id):
            raise HTTPException(status_code=409, detail="同一层已经有同名文件夹。")
        cursor = connection.execute(
            "INSERT INTO library_folders(parent_id, name) VALUES (?, ?)",
            (request.parent_id, name),
        )
        folder_id = int(cursor.lastrowid)
        folder = connection.execute("SELECT * FROM library_folders WHERE id = ?", (folder_id,)).fetchone()
    return dict(folder)


@app.patch("/api/library/folders/{folder_id}")
def patch_library_folder(folder_id: int, request: LibraryFolderRequest) -> dict[str, Any]:
    """重命名或移动文件夹，并阻止形成父子循环。"""

    name = request.name.strip()
    with transaction(settings.database_path) as connection:
        folder = _ensure_folder(connection, folder_id)
        _ensure_folder(connection, request.parent_id)
        if request.parent_id == folder_id:
            raise HTTPException(status_code=422, detail="文件夹不能放进自己。")
        ancestor = request.parent_id
        while ancestor is not None:
            if ancestor == folder_id:
                raise HTTPException(status_code=422, detail="移动会形成循环目录。")
            row = connection.execute("SELECT parent_id FROM library_folders WHERE id = ?", (ancestor,)).fetchone()
            ancestor = int(row["parent_id"]) if row is not None and row["parent_id"] is not None else None
        if _folder_name_taken(connection, name, request.parent_id, folder_id):
            raise HTTPException(status_code=409, detail="同一层已经有同名文件夹。")
        connection.execute(
            "UPDATE library_folders SET name = ?, parent_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (name, request.parent_id, folder_id),
        )
        updated = connection.execute("SELECT * FROM library_folders WHERE id = ?", (folder_id,)).fetchone()
    return dict(updated)


@app.delete("/api/library/folders/{folder_id}", status_code=204)
def delete_library_folder(folder_id: int) -> Response:
    """删除文件夹，直接内容移回根目录，书籍和子文件夹均保留。"""

    with transaction(settings.database_path) as connection:
        _ensure_folder(connection, folder_id)
        connection.execute("UPDATE books SET folder_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE folder_id = ?", (folder_id,))
        connection.execute("UPDATE library_folders SET parent_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE parent_id = ?", (folder_id,))
        connection.execute("DELETE FROM library_folders WHERE id = ?", (folder_id,))
    return Response(status_code=204)


@app.patch("/api/books/{book_id}")
def patch_book(book_id: int, request: BookPatch) -> dict[str, Any]:
    """修改书名、作者或所在文件夹。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        if request.folder_id is not None:
            _ensure_folder(connection, request.folder_id)
        updates: list[str] = []
        values: list[Any] = []
        if request.title is not None:
            updates.append("title = ?")
            values.append(request.title.strip())
        if request.author is not None:
            updates.append("author = ?")
            values.append(request.author.strip())
        if request.folder_id is not None or request.move_to_root:
            updates.append("folder_id = ?")
            values.append(None if request.move_to_root else request.folder_id)
        for field_name in ("language", "report_language", "corpus_kind", "license_name", "source_url", "rights_status", "source_sha256"):
            field_value = getattr(request, field_name)
            if field_value is not None:
                updates.append(f"{field_name} = ?")
                values.append(field_value.strip())
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            values.append(book_id)
            connection.execute(f"UPDATE books SET {', '.join(updates)} WHERE id = ?", values)
        book = connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return dict(book)


@app.delete("/api/books/{book_id}", status_code=204)
def delete_book(book_id: int) -> Response:
    """删除一本书及其派生数据，其他书籍保持不变。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        connection.execute("DELETE FROM books WHERE id = ?", (book_id,))
    return Response(status_code=204)


@app.post("/api/books/import", status_code=201)
async def import_book(file: UploadFile = File(...), folder_id: int | None = Form(default=None)) -> dict[str, Any]:
    """限制体积后解析文件，并以内容哈希防止重复导入。"""

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="文件超过本地上传限制。")
    try:
        parsed = parse_book(file.filename or "novel.txt", content)
    except ImportErrorDetail as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        with transaction(settings.database_path) as connection:
            _ensure_folder(connection, folder_id)
            cursor = connection.execute(
                """
                INSERT INTO books(title, author, source_type, source_hash, original_filename, folder_id, segment_count, character_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    parsed.title,
                    parsed.author,
                    parsed.source_type,
                    parsed.source_hash,
                    parsed.original_filename,
                    folder_id,
                    len(parsed.segments),
                    parsed.character_count,
                ),
            )
            book_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (book_id, item.ordinal, item.chapter_title, item.anchor, item.text, item.char_start, item.char_end)
                    for item in parsed.segments
                ],
            )
    except sqlite3.IntegrityError as exc:
        with connect(settings.database_path) as connection:
            existing = connection.execute("SELECT id, title FROM books WHERE source_hash = ?", (parsed.source_hash,)).fetchone()
        raise HTTPException(
            status_code=409,
            detail={"message": "相同内容已经导入。", "book_id": existing["id"] if existing else None},
        ) from exc
    return {"id": book_id, "title": parsed.title, "segments": len(parsed.segments), "characters": parsed.character_count}


@app.post("/api/books/{book_id}/updates", status_code=201)
async def preview_incremental_update(
    book_id: int,
    file: UploadFile = File(...),
    mode: str = Form(default="auto"),
) -> dict[str, Any]:
    """旧章节一致时只追加新增片段；变化内容进入可处理冲突清单。"""

    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="文件超过本地上传限制。")
    try:
        parsed = parse_book(file.filename or "novel.txt", content)
        with transaction(settings.database_path) as connection:
            return preview_book_update(connection, book_id, parsed, mode)
    except ImportErrorDetail as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/updates")
def book_update_history(book_id: int) -> list[dict[str, Any]]:
    """列出增量更新、追加数量和全部待处理差异。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        return list_book_updates(connection, book_id)


@app.post("/api/book-updates/{update_id}/resolve")
def resolve_incremental_update(update_id: int, request: BookUpdateResolution) -> dict[str, Any]:
    """用户或系统处理更新冲突，旧书始终保留。"""

    try:
        with transaction(settings.database_path) as connection:
            return resolve_book_update(connection, update_id, request.action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/narrative-structure")
def narrative_structure(
    book_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Return the reversible story-unit and shared-world structure."""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        existing = connection.execute("SELECT 1 FROM narrative_units WHERE book_id = ? LIMIT 1", (book_id,)).fetchone()
        payload = get_narrative_structure(connection, book_id) if existing else rebuild_narrative_structure(connection, book_id)
        # Story scopes are evidence-backed metadata and are not chapter filters;
        # progress boundaries are the only range-sensitive part of this response.
        payload["progress_boundaries"] = [
            item for item in payload.get("progress_boundaries", [])
            if int(item.get("ordinal", 0)) <= window.through_segment
        ]
        payload["reading_window"] = window.payload()
        return payload


@app.post("/api/books/{book_id}/narrative-structure/rebuild")
def rebuild_book_narrative_structure(book_id: int, request: NarrativeRebuildRequest) -> dict[str, Any]:
    """Recompute local partition suggestions without calling a model."""

    try:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            return rebuild_narrative_structure(connection, book_id, force=request.force)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/story-worlds/merge")
def merge_book_story_worlds(book_id: int, request: StoryWorldMergeRequest) -> dict[str, Any]:
    try:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            return merge_story_worlds(connection, book_id, request.world_ids, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/story-worlds/split")
def split_book_story_world(book_id: int, request: StoryWorldSplitRequest) -> dict[str, Any]:
    try:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            return split_story_world(connection, book_id, request.unit_ids, request.name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/narrative-units/{unit_id}/move")
def move_book_narrative_unit(unit_id: int, request: NarrativeUnitMoveRequest) -> dict[str, Any]:
    try:
        with transaction(settings.database_path) as connection:
            return move_narrative_unit(connection, unit_id, request.world_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/review-tasks")
def book_review_tasks(
    book_id: int,
    include_resolved: bool = Query(default=False),
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sync_review_tasks(connection, book_id)
        return list_review_tasks(
            connection, book_id, include_resolved=include_resolved,
            through_segment=window.through_segment, from_segment=window.from_segment,
        )


@app.patch("/api/review-tasks/{task_id}")
def patch_review_task(task_id: int, request: ReviewTaskPatch) -> dict[str, Any]:
    try:
        with transaction(settings.database_path) as connection:
            return resolve_review_task(connection, task_id, request.action, request.note.strip())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/analyze")
async def run_analysis(book_id: int, request: AnalyzeRequest) -> dict[str, Any]:
    """执行有限片段分析；长篇生产队列将在后续阶段加入。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
    try:
        return await analyze_book(
            settings,
            book_id,
            request.provider,
            request.start_segment,
            request.max_segments,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/jobs", status_code=201)
def create_analysis_job(book_id: int, request: AnalysisJobRequest) -> dict[str, Any]:
    """创建整本书后台任务，每个章节完成后立即保存进度和费用。"""

    try:
        job = create_job(
            settings,
            book_id,
            request.provider,
            request.start_segment,
            request.end_segment,
            request.max_retries,
            request.reanalyze,
            request.max_cost_usd,
            request.max_input_tokens,
            request.max_output_tokens,
            request.review_mode,
            request.budget_mode,
        )
    except (ProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if job_manager is not None:
        job_manager.wake()
    return job


@app.post("/api/books/{book_id}/jobs/estimate")
def estimate_analysis_job(book_id: int, request: AnalysisJobRequest) -> dict[str, Any]:
    """创建任务前显示保守令牌与金额预估，不调用模型。"""

    try:
        return estimate_job(
            settings,
            book_id,
            request.provider,
            request.start_segment,
            request.end_segment,
            request.review_mode,
            request.reanalyze,
        )
    except (ProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/cost-forecast")
def cost_forecast(
    book_id: int,
    provider: str = Query(default="auto", pattern="^(mock|deepseek|moonshot|codex_luna|auto)$"),
    start_segment: int = Query(default=0, ge=0),
    end_segment: int | None = Query(default=None, ge=0),
    review_mode: str = Query(default="local", pattern="^(local|full)$"),
    reanalyze: bool = False,
) -> dict[str, Any]:
    """Expose the same calibrated forecast used by the analysis confirmation dialog."""

    try:
        return estimate_job(
            settings, book_id, provider, start_segment, end_segment, review_mode, reanalyze,
        )
    except (ProviderError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/jobs")
def book_jobs(book_id: int) -> list[dict[str, Any]]:
    """返回一本书最近的后台任务。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
    return list_jobs(settings, book_id)


@app.get("/api/jobs/{job_id}")
def analysis_job(job_id: int) -> dict[str, Any]:
    """返回任务实时状态和正在处理的章节。"""

    try:
        return get_job(settings, job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/api/jobs/{job_id}")
def patch_analysis_job(job_id: int, request: AnalysisJobAction) -> dict[str, Any]:
    """暂停、继续、取消或重试整本书任务。"""

    try:
        job = control_job(settings, job_id, request.action)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if request.action in {"resume", "retry"} and job_manager is not None:
        job_manager.wake()
    return job


@app.patch("/api/jobs/{job_id}/budget")
def patch_analysis_budget(job_id: int, request: AnalysisBudgetPatch) -> dict[str, Any]:
    """调整任务预算策略；暂停任务仍需用户点击继续。"""

    try:
        return update_job_budget(
            settings,
            job_id,
            request.max_cost_usd,
            request.max_input_tokens,
            request.max_output_tokens,
            request.budget_mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/books/{book_id}/benchmarks")
def list_benchmark_cases(
    book_id: int,
    reveal_holdout: bool = Query(default=False),
) -> list[dict[str, Any]]:
    """Return review records while keeping sealed answers out of ordinary APIs."""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        return _list_benchmark_cases(connection, book_id, reveal_holdout=False)


@app.get("/api/books/{book_id}/benchmark-candidates")
def list_benchmark_candidates(
    book_id: int,
    status: str = Query(default="pending", pattern="^(pending|accepted|rejected|all)$"),
) -> list[dict[str, Any]]:
    """返回待人工确认的候选；候选不会进入准确率统计。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        filters = "candidate.book_id = ?" if status == "all" else "candidate.book_id = ? AND candidate.status = ?"
        parameters: tuple[Any, ...] = (book_id,) if status == "all" else (book_id, status)
        found = connection.execute(
            f"""
            SELECT candidate.*, segment.chapter_title AS source_chapter_title
            FROM benchmark_candidates candidate
            LEFT JOIN segments segment
              ON segment.book_id = candidate.book_id AND segment.ordinal = candidate.source_segment
            WHERE {filters}
            ORDER BY candidate.critical DESC, candidate.source_segment, candidate.id
            LIMIT 120
            """,
            parameters,
        ).fetchall()
    return [candidate_payload(row) for row in found]


@app.post("/api/books/{book_id}/benchmark-candidates/refresh")
def refresh_book_benchmark_candidates(book_id: int) -> dict[str, int]:
    """从已有证据索引候选，不调用模型，不把候选标记为已确认。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        return refresh_benchmark_candidates(connection, book_id)


@app.post("/api/benchmark-candidates/{candidate_id}/resolve")
def resolve_benchmark_candidate(
    candidate_id: int,
    request: BenchmarkCandidateResolve,
) -> dict[str, Any]:
    """把人工确认的候选转为正式金标准，或保留拒绝原因。"""

    with transaction(settings.database_path) as connection:
        candidate = connection.execute(
            "SELECT * FROM benchmark_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        if candidate is None:
            raise HTTPException(status_code=404, detail="找不到这条金标准候选。")
        if str(candidate["status"]) != "pending":
            raise HTTPException(status_code=409, detail="这条候选已经处理过，不能重复计入评估集。")
        if request.action == "reject":
            connection.execute(
                """
                UPDATE benchmark_candidates SET status = 'rejected', resolution_note = ?,
                    resolved_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (request.note.strip() or "人工拒绝该候选", candidate_id),
            )
            updated = connection.execute(
                "SELECT * FROM benchmark_candidates WHERE id = ?", (candidate_id,)
            ).fetchone()
            return {"candidate": candidate_payload(updated), "benchmark": None}

        book = ensure_book(connection, int(candidate["book_id"]))
        expected = _benchmark_expected(
            str(candidate["case_type"]),
            json.loads(str(candidate["expected_json"])),
            int(book["segment_count"]),
        )
        critical = bool(candidate["critical"]) if request.critical is None else request.critical
        audit = _benchmark_review_audit(
            connection, int(candidate["book_id"]), int(candidate["source_segment"]), expected,
            confirmed=True, holdout=request.holdout, critical=critical,
            reviewer_id=request.reviewer_id, reviewer_role=request.reviewer_role,
            review_session=request.review_session,
        )
        duplicate = connection.execute(
            """
            SELECT id FROM quality_benchmark_cases
            WHERE book_id = ? AND case_type = ? AND subject = ? AND source_segment = ?
            """,
            (
                candidate["book_id"], candidate["case_type"], candidate["subject"],
                candidate["source_segment"],
            ),
        ).fetchone()
        if duplicate is None:
            benchmark_id = int(connection.execute(
                """
                INSERT INTO quality_benchmark_cases(
                    book_id, case_type, subject, expected_json, source_segment, note, critical,
                    suite_name, origin, holdout, confirmed_by_user, failure_category,
                    review_status, reviewer_id, reviewer_role, review_session,
                    review_evidence_hash, reviewed_at, second_review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'real-novel-gold', 'manual', ?, 1,
                    'candidate-review', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """,
                (
                    candidate["book_id"], candidate["case_type"], candidate["subject"],
                    json.dumps(expected, ensure_ascii=False), candidate["source_segment"],
                    request.note.strip() or str(candidate["note"]),
                    int(critical),
                    int(request.holdout),
                    audit["review_status"], audit["reviewer_id"], audit["reviewer_role"],
                    audit["review_session"], audit["review_evidence_hash"],
                    audit["second_review_status"],
                ),
            ).lastrowid)
        else:
            benchmark_id = int(duplicate["id"])
            connection.execute(
                """
                UPDATE quality_benchmark_cases
                SET expected_json = ?, note = ?, critical = ?, holdout = ?, confirmed_by_user = 1,
                    origin = 'manual', review_status = ?, reviewer_id = ?, reviewer_role = ?,
                    review_session = ?, review_evidence_hash = ?, reviewed_at = CURRENT_TIMESTAMP,
                    second_review_status = ?, second_reviewer_id = '', second_reviewed_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    json.dumps(expected, ensure_ascii=False),
                    request.note.strip() or str(candidate["note"]), int(critical), int(request.holdout),
                    audit["review_status"], audit["reviewer_id"], audit["reviewer_role"],
                    audit["review_session"], audit["review_evidence_hash"],
                    audit["second_review_status"], benchmark_id,
                ),
            )
        connection.execute(
            """
            UPDATE benchmark_candidates SET status = 'accepted', accepted_benchmark_id = ?,
                resolution_note = ?, resolved_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (benchmark_id, request.note.strip() or "人工确认并加入金标准", candidate_id),
        )
        evaluate_benchmarks(connection, int(candidate["book_id"]))
        updated = connection.execute(
            "SELECT * FROM benchmark_candidates WHERE id = ?", (candidate_id,)
        ).fetchone()
        benchmark = connection.execute(
            """
            SELECT benchmark.*, segment.chapter_title AS source_chapter_title
            FROM quality_benchmark_cases benchmark
            LEFT JOIN segments segment
              ON segment.book_id = benchmark.book_id AND segment.ordinal = benchmark.source_segment
            WHERE benchmark.id = ?
            """,
            (benchmark_id,),
        ).fetchone()
    return {"candidate": candidate_payload(updated), "benchmark": _benchmark_case_payload(benchmark, reveal_holdout=False)}


@app.post("/api/books/{book_id}/benchmarks", status_code=201)
def create_benchmark_case(book_id: int, request: BenchmarkCaseCreate) -> dict[str, Any]:
    """新增人工金标准并立刻使用本地规则复算全部用例。"""

    with transaction(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        segment_count = int(book["segment_count"])
        if request.source_segment >= segment_count:
            raise HTTPException(status_code=422, detail="金标准引用的章节超出本书范围。")
        expected = _benchmark_expected(request.case_type, request.expected, segment_count)
        duplicate = connection.execute(
            """
            SELECT id FROM quality_benchmark_cases
            WHERE book_id = ? AND case_type = ? AND subject = ? AND source_segment = ?
            """,
            (book_id, request.case_type, request.subject.strip(), request.source_segment),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="同一章节已经存在同名同类型金标准。")
        audit = _benchmark_review_audit(
            connection, book_id, request.source_segment, expected,
            confirmed=request.confirmed_by_user, holdout=request.holdout,
            critical=request.critical, reviewer_id=request.reviewer_id or "local-reviewer",
            reviewer_role=request.reviewer_role, review_session=request.review_session,
        )
        case_id = int(connection.execute(
            """
            INSERT INTO quality_benchmark_cases(
                book_id, case_type, subject, expected_json, source_segment, note, critical,
                suite_name, origin, holdout, confirmed_by_user, failure_category,
                review_status, reviewer_id, reviewer_role, review_session,
                review_evidence_hash, reviewed_at, second_review_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, ?)
            """,
            (
                book_id, request.case_type, request.subject.strip(),
                json.dumps(expected, ensure_ascii=False), request.source_segment,
                request.note.strip(), int(request.critical), request.suite_name.strip(),
                request.origin, int(request.holdout), int(request.confirmed_by_user),
                request.failure_category.strip(), audit["review_status"], audit["reviewer_id"],
                audit["reviewer_role"], audit["review_session"], audit["review_evidence_hash"],
                int(request.confirmed_by_user), audit["second_review_status"],
            ),
        ).lastrowid)
        evaluate_benchmarks(connection, book_id)
        row = connection.execute(
            """
            SELECT benchmark.*, segment.chapter_title AS source_chapter_title
            FROM quality_benchmark_cases benchmark
            LEFT JOIN segments segment
              ON segment.book_id = benchmark.book_id AND segment.ordinal = benchmark.source_segment
            WHERE benchmark.id = ?
            """,
            (case_id,),
        ).fetchone()
    return _benchmark_case_payload(row, reveal_holdout=False)


@app.patch("/api/benchmarks/{case_id}")
def patch_benchmark_case(case_id: int, request: BenchmarkCasePatch) -> dict[str, Any]:
    """修改人工金标准并立刻刷新本地准确率结果。"""

    with transaction(settings.database_path) as connection:
        existing = connection.execute(
            "SELECT * FROM quality_benchmark_cases WHERE id = ?", (case_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="找不到这条人工金标准。")
        book = ensure_book(connection, int(existing["book_id"]))
        segment_count = int(book["segment_count"])
        case_type = request.case_type or str(existing["case_type"])
        source_segment = request.source_segment if request.source_segment is not None else int(existing["source_segment"])
        if source_segment >= segment_count:
            raise HTTPException(status_code=422, detail="金标准引用的章节超出本书范围。")
        existing_expected = json.loads(str(existing["expected_json"]))
        expected = _benchmark_expected(
            case_type,
            request.expected if request.expected is not None else existing_expected,
            segment_count,
        )
        subject = request.subject.strip() if request.subject is not None else str(existing["subject"])
        note = request.note.strip() if request.note is not None else str(existing["note"])
        critical = request.critical if request.critical is not None else bool(existing["critical"])
        suite_name = request.suite_name.strip() if request.suite_name is not None else str(existing["suite_name"])
        origin = request.origin if request.origin is not None else str(existing["origin"])
        holdout = request.holdout if request.holdout is not None else bool(existing["holdout"])
        confirmed_by_user = request.confirmed_by_user if request.confirmed_by_user is not None else bool(existing["confirmed_by_user"])
        failure_category = request.failure_category.strip() if request.failure_category is not None else str(existing["failure_category"])
        reviewer_id = request.reviewer_id if request.reviewer_id is not None else str(existing["reviewer_id"] or "local-reviewer")
        reviewer_role = request.reviewer_role if request.reviewer_role is not None else str(existing["reviewer_role"] or "owner")
        review_session = request.review_session if request.review_session is not None else str(existing["review_session"] or "")
        audit = _benchmark_review_audit(
            connection, int(existing["book_id"]), source_segment, expected,
            confirmed=confirmed_by_user, holdout=holdout, critical=critical,
            reviewer_id=reviewer_id, reviewer_role=reviewer_role,
            review_session=review_session,
        )
        duplicate = connection.execute(
            """
            SELECT id FROM quality_benchmark_cases
            WHERE book_id = ? AND case_type = ? AND subject = ? AND source_segment = ? AND id != ?
            """,
            (existing["book_id"], case_type, subject, source_segment, case_id),
        ).fetchone()
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="同一章节已经存在同名同类型金标准。")
        connection.execute(
            """
            UPDATE quality_benchmark_cases
            SET case_type = ?, subject = ?, expected_json = ?, source_segment = ?, note = ?,
                critical = ?, suite_name = ?, origin = ?, holdout = ?, confirmed_by_user = ?,
                failure_category = ?, review_status = ?, reviewer_id = ?, reviewer_role = ?,
                review_session = ?, review_evidence_hash = ?,
                reviewed_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                second_review_status = ?, second_reviewer_id = '', second_reviewed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                case_type, subject, json.dumps(expected, ensure_ascii=False), source_segment,
                note, int(critical), suite_name, origin, int(holdout), int(confirmed_by_user),
                failure_category, audit["review_status"], audit["reviewer_id"],
                audit["reviewer_role"], audit["review_session"], audit["review_evidence_hash"],
                int(confirmed_by_user), audit["second_review_status"], case_id,
            ),
        )
        evaluate_benchmarks(connection, int(existing["book_id"]))
        row = connection.execute(
            """
            SELECT benchmark.*, segment.chapter_title AS source_chapter_title
            FROM quality_benchmark_cases benchmark
            LEFT JOIN segments segment
              ON segment.book_id = benchmark.book_id AND segment.ordinal = benchmark.source_segment
            WHERE benchmark.id = ?
            """,
            (case_id,),
        ).fetchone()
    return _benchmark_case_payload(row, reveal_holdout=False)


@app.delete("/api/benchmarks/{case_id}", status_code=204)
def delete_benchmark_case(case_id: int) -> Response:
    """删除一条人工金标准，随后重新统计准确率门禁。"""

    with transaction(settings.database_path) as connection:
        existing = connection.execute(
            "SELECT book_id FROM quality_benchmark_cases WHERE id = ?", (case_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="找不到这条人工金标准。")
        connection.execute("DELETE FROM quality_benchmark_cases WHERE id = ?", (case_id,))
        evaluate_benchmarks(connection, int(existing["book_id"]))
    return Response(status_code=204)


@app.post("/api/benchmarks/{case_id}/second-review")
def second_review_benchmark_case(case_id: int, request: BenchmarkSecondReview) -> dict[str, Any]:
    """Complete the required second pass for critical and sealed benchmark cases."""

    with transaction(settings.database_path) as connection:
        existing = connection.execute(
            "SELECT * FROM quality_benchmark_cases WHERE id = ?", (case_id,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="找不到这条人工金标准。")
        if not bool(existing["confirmed_by_user"]) or str(existing["review_status"]) == "candidate":
            raise HTTPException(status_code=409, detail="候选案例必须先完成首次人工确认。")
        if not bool(existing["critical"]) and str(existing["review_status"]) != "sealed_holdout":
            raise HTTPException(status_code=409, detail="这条普通开发案例不需要二次复核。")
        connection.execute(
            """
            UPDATE quality_benchmark_cases
            SET second_review_status = 'confirmed', second_reviewer_id = ?,
                second_reviewed_at = CURRENT_TIMESTAMP,
                note = note || ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (request.reviewer_id.strip(), f"\n二次复核：{request.note.strip()}", case_id),
        )
        evaluate_benchmarks(connection, int(existing["book_id"]))
        row = connection.execute(
            """
            SELECT benchmark.*, segment.chapter_title AS source_chapter_title
            FROM quality_benchmark_cases benchmark
            LEFT JOIN segments segment
              ON segment.book_id = benchmark.book_id AND segment.ordinal = benchmark.source_segment
            WHERE benchmark.id = ?
            """,
            (case_id,),
        ).fetchone()
    return _benchmark_case_payload(row, reveal_holdout=False)


@app.post("/api/books/{book_id}/benchmarks/evaluate")
def evaluate_benchmark_cases(book_id: int) -> dict[str, Any]:
    """只使用本地数据库复算人工金标准，不调用模型。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        summary = evaluate_benchmarks(connection, book_id)
        cases = _list_benchmark_cases(connection, book_id, reveal_holdout=False)
    return {"summary": summary, "cases": cases, "estimated_cost_usd": 0}


def _collaboration_payload(row: sqlite3.Row) -> dict[str, Any]:
    """把协作事项中的结构字段转换为页面可直接使用的对象。"""

    item = dict(row)
    for source, target, fallback in (
        ("acceptance_json", "acceptance", []),
        ("impact_json", "impact", []),
        ("evidence_json", "evidence", []),
    ):
        item[target] = parse_json(item.pop(source), fallback)
    item["requires_confirmation"] = bool(item["requires_confirmation"])
    return item


def _domain_rule_payload(row: sqlite3.Row) -> dict[str, Any]:
    """返回阅读规则并保留示例和启用状态。"""

    item = dict(row)
    item["examples"] = parse_json(item.pop("examples_json"), [])
    item["active"] = bool(item["active"])
    return item


@app.get("/api/collaboration")
def list_collaboration_items(book_id: int | None = Query(default=None, gt=0)) -> list[dict[str, Any]]:
    """列出全局事项和指定书籍事项，供外部工具复用同一协作闭环。"""

    with connect(settings.database_path) as connection:
        if book_id is not None:
            ensure_book(connection, book_id)
            result = connection.execute(
                "SELECT * FROM collaboration_items WHERE book_id IS NULL OR book_id = ? ORDER BY updated_at DESC, id DESC",
                (book_id,),
            ).fetchall()
        else:
            result = connection.execute(
                "SELECT * FROM collaboration_items ORDER BY updated_at DESC, id DESC"
            ).fetchall()
    return [_collaboration_payload(row) for row in result]


@app.get("/api/domain-rules")
def list_domain_rules(book_id: int | None = Query(default=None, gt=0)) -> list[dict[str, Any]]:
    """列出分层阅读规则，不混入任何作品外事实。"""

    with connect(settings.database_path) as connection:
        if book_id is not None:
            ensure_book(connection, book_id)
            result = connection.execute(
                "SELECT * FROM domain_rules WHERE book_id IS NULL OR book_id = ? ORDER BY priority, id",
                (book_id,),
            ).fetchall()
        else:
            result = connection.execute("SELECT * FROM domain_rules ORDER BY priority, id").fetchall()
    return [_domain_rule_payload(row) for row in result]


@app.get("/api/external-facts")
def list_external_facts(book_id: int = Query(gt=0)) -> list[dict[str, Any]]:
    """列出带来源的作品外资料，并明确它们不会注入原文抽取。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        result = connection.execute(
            "SELECT * FROM external_facts WHERE book_id = ? ORDER BY id DESC", (book_id,)
        ).fetchall()
    return [
        {**dict(row), "active": bool(row["active"]), "injected_into_extraction": False}
        for row in result
    ]


@app.get("/api/books/{book_id}/control-plane")
def control_plane_overview(book_id: int) -> dict[str, Any]:
    """一次返回协作控制台需要的合同、规则、运行和模型状态。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        contract = product_contract(connection)
        contract["quality"] = {
            "quote_integrity_percent": contract.get("quality", {}).get("quote_integrity_percent", 100),
            "critical_subject_failures": contract.get("quality", {}).get("critical_subject_failures", 0),
            "unresolved_critical_conflicts": contract.get("quality", {}).get("unresolved_critical_conflicts", 0),
        }
        collaboration = [
            _collaboration_payload(row)
            for row in connection.execute(
                "SELECT * FROM collaboration_items WHERE book_id IS NULL OR book_id = ? ORDER BY updated_at DESC, id DESC",
                (book_id,),
            ).fetchall()
        ]
        rules = [
            _domain_rule_payload(row)
            for row in connection.execute(
                "SELECT * FROM domain_rules WHERE book_id IS NULL OR book_id = ? ORDER BY priority, id",
                (book_id,),
            ).fetchall()
        ]
        facts = [
            {**dict(row), "active": bool(row["active"]), "injected_into_extraction": False}
            for row in connection.execute(
                "SELECT * FROM external_facts WHERE book_id = ? ORDER BY id DESC", (book_id,)
            ).fetchall()
        ]
        manifests = [
            manifest_payload(row)
            for row in connection.execute(
                "SELECT * FROM run_manifests WHERE book_id = ? ORDER BY id DESC LIMIT 40", (book_id,)
            ).fetchall()
        ]
        routes = [dict(row) for row in connection.execute("SELECT * FROM model_routes ORDER BY priority, id").fetchall()]
        for route in routes:
            route["enabled"] = bool(route["enabled"])
            route["eligible"] = bool(route["eligible"])
            route.pop("benchmark_json", None)
        prompt_summaries = [
            {
                "task_key": task_key,
                "task_label": PROMPT_TASKS[task_key][0],
                **{
                    key: value
                    for key, value in prompt_bundle_payload(render_prompt_bundle(connection, book_id, task_key)).items()
                    if key in {"id", "version", "status", "prompt_hash", "estimated_tokens"}
                },
            }
            for task_key in PROMPT_TASKS
        ]
        prompt_versions = rows(connection.execute(
            """
            SELECT id, task_key, version, status, change_note, parent_id, prompt_hash,
                created_at, promoted_at
            FROM prompt_bundle_versions ORDER BY task_key, id DESC
            """
        ).fetchall())
    return {
        "contract": contract,
        "collaboration": collaboration,
        "domain_rules": rules,
        "external_facts": facts,
        "prompt_bundles": prompt_summaries,
        "prompt_versions": prompt_versions,
        "runs": manifests,
        "model_routes": routes,
        "secret_rotation_required": True,
    }


@app.post("/api/books/{book_id}/collaboration", status_code=201)
def create_collaboration_item(book_id: int, request: CollaborationItemCreate) -> dict[str, Any]:
    """登记用户原话、系统理解、验收条件和影响范围。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        cursor = connection.execute(
            """
            INSERT INTO collaboration_items(
                book_id, original_text, interpreted_goal, acceptance_json, impact_json,
                estimated_cost_change_percent, requires_confirmation
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, request.original_text.strip(), request.interpreted_goal.strip(),
                json.dumps(request.acceptance, ensure_ascii=False),
                json.dumps(request.impact, ensure_ascii=False),
                request.estimated_cost_change_percent, int(request.requires_confirmation),
            ),
        )
        row = connection.execute(
            "SELECT * FROM collaboration_items WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _collaboration_payload(row)


@app.patch("/api/collaboration/{item_id}")
def patch_collaboration_item(item_id: int, request: CollaborationItemPatch) -> dict[str, Any]:
    """推进协作事项；需要确认的事项不能跳过确认直接发布。"""

    with transaction(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM collaboration_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到这条协作事项。")
        status = request.status or str(row["status"])
        if bool(row["requires_confirmation"]) and status in {"implementing", "validating", "released"} and row["status"] == "interpreted":
            raise HTTPException(status_code=409, detail="这项改动涉及关键决策，请先确认再执行。")
        interpreted = request.interpreted_goal.strip() if request.interpreted_goal is not None else str(row["interpreted_goal"])
        acceptance = request.acceptance if request.acceptance is not None else parse_json(row["acceptance_json"], [])
        impact = request.impact if request.impact is not None else parse_json(row["impact_json"], [])
        evidence = request.evidence if request.evidence is not None else parse_json(row["evidence_json"], [])
        regression_case_id = request.regression_case_id if request.regression_case_id is not None else row["regression_case_id"]
        if regression_case_id is not None and connection.execute(
            "SELECT 1 FROM quality_benchmark_cases WHERE id = ?", (regression_case_id,)
        ).fetchone() is None:
            raise HTTPException(status_code=422, detail="关联的回归案例不存在。")
        connection.execute(
            """
            UPDATE collaboration_items SET status = ?, interpreted_goal = ?, acceptance_json = ?,
                impact_json = ?, evidence_json = ?, regression_case_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                status, interpreted, json.dumps(acceptance, ensure_ascii=False),
                json.dumps(impact, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False),
                regression_case_id, item_id,
            ),
        )
        updated = connection.execute("SELECT * FROM collaboration_items WHERE id = ?", (item_id,)).fetchone()
    return _collaboration_payload(updated)


@app.post("/api/collaboration/{item_id}/regression", status_code=201)
def collaboration_to_regression(item_id: int, request: BenchmarkCaseCreate) -> dict[str, Any]:
    """把具体纠正登记为永久金标准，并与原始反馈双向关联。"""

    with connect(settings.database_path) as connection:
        item = connection.execute("SELECT * FROM collaboration_items WHERE id = ?", (item_id,)).fetchone()
        if item is None or item["book_id"] is None:
            raise HTTPException(status_code=404, detail="找不到可关联到书籍的协作事项。")
        book_id = int(item["book_id"])
    benchmark = create_benchmark_case(book_id, request)
    with transaction(settings.database_path) as connection:
        connection.execute(
            """
            UPDATE quality_benchmark_cases
            SET origin = 'user_correction', confirmed_by_user = 1,
                review_status = 'adjudicated', reviewer_role = 'owner',
                reviewed_at = COALESCE(reviewed_at, CURRENT_TIMESTAMP)
            WHERE id = ?
            """,
            (benchmark["id"],),
        )
        connection.execute(
            """
            UPDATE collaboration_items SET regression_case_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (benchmark["id"], item_id),
        )
    return benchmark


@app.get("/api/books/{book_id}/prompt-bundles")
def list_prompt_bundles(book_id: int) -> dict[str, Any]:
    """返回所有提示词版本和当前实际拼装结果。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        versions = rows(connection.execute(
            """
            SELECT id, task_key, version, status, change_note, parent_id, prompt_hash, created_at, promoted_at
            FROM prompt_bundle_versions ORDER BY task_key, id DESC
            """
        ).fetchall())
        active = [
            prompt_bundle_payload(render_prompt_bundle(connection, book_id, task_key))
            for task_key in PROMPT_TASKS
        ]
    return {"active": active, "versions": versions}


@app.get("/api/prompt-bundles")
def list_prompt_bundle_registry(book_id: int = Query(gt=0)) -> dict[str, Any]:
    """提供与书籍接口相同的提示词注册表集合入口。"""

    return list_prompt_bundles(book_id)


@app.get("/api/books/{book_id}/prompt-bundles/{task_key}")
def get_prompt_bundle(
    book_id: int,
    task_key: str,
    bundle_id: int | None = Query(default=None, gt=0),
    segment_id: int | None = Query(default=None, gt=0),
) -> dict[str, Any]:
    """查看当前正式版本或指定草稿的完整最终提示词。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            rendered = render_prompt_bundle(connection, book_id, task_key, bundle_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload = prompt_bundle_payload(rendered)
        version_row = connection.execute(
            """
            SELECT change_note, parent_id, created_at, promoted_at
            FROM prompt_bundle_versions WHERE id = ?
            """,
            (rendered.bundle_id,),
        ).fetchone()
        if version_row is not None:
            payload.update(dict(version_row))
        production = render_prompt_bundle(connection, book_id, task_key)
        if bundle_id is not None:
            before = production.system_prompt.splitlines()
            after = rendered.system_prompt.splitlines()
            payload["diff"] = "\n".join(unified_diff(before, after, fromfile="正式版本", tofile="候选版本", lineterm=""))
        else:
            payload["diff"] = ""
        payload["runtime_user_prompt"] = ""
        payload["complete_request_preview"] = ""
        payload["runtime_segment_id"] = None
        if task_key == "extraction" and segment_id is not None:
            segment = connection.execute(
                "SELECT * FROM segments WHERE id = ? AND book_id = ?", (segment_id, book_id)
            ).fetchone()
            if segment is None:
                raise HTTPException(status_code=404, detail="找不到用于预览运行上下文的原文片段。")
            context = build_analysis_context(connection, book_id, int(segment["ordinal"]))
            user_prompt = build_user_prompt(
                str(segment["chapter_title"]), int(segment["ordinal"]), str(segment["text"]), context
            )
            payload["runtime_user_prompt"] = user_prompt
            payload["complete_request_preview"] = (
                "<SYSTEM_PROMPT>\n" + rendered.system_prompt + "\n</SYSTEM_PROMPT>\n\n"
                "<USER_PROMPT>\n" + user_prompt + "\n</USER_PROMPT>"
            )
            payload["runtime_segment_id"] = int(segment["id"])
    return payload


@app.post("/api/prompt-bundles/{task_key}/drafts", status_code=201)
def create_prompt_draft(
    task_key: str,
    request: PromptDraftCreate,
    book_id: int = Query(gt=0),
) -> dict[str, Any]:
    """从正式版本创建草稿；保存草稿不会影响任何生产任务。"""

    if task_key not in PROMPT_TASKS:
        raise HTTPException(status_code=404, detail="未知提示词任务。")
    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        production = render_prompt_bundle(connection, book_id, task_key)
        parent = connection.execute("SELECT * FROM prompt_bundle_versions WHERE id = ?", (production.bundle_id,)).fetchone()
        count = int(connection.execute(
            "SELECT COUNT(*) FROM prompt_bundle_versions WHERE task_key = ?", (task_key,)
        ).fetchone()[0])
        version = f"v{count + 1}"
        core_text = request.core_text.strip() if request.core_text is not None else str(parent["core_text"])
        task_text = request.task_text.strip()
        digest = stable_hash(core_text, task_text)
        cursor = connection.execute(
            """
            INSERT INTO prompt_bundle_versions(
                task_key, version, status, core_text, task_text, change_note, parent_id, prompt_hash
            ) VALUES (?, ?, 'draft', ?, ?, ?, ?, ?)
            """,
            (task_key, version, core_text, task_text, request.change_note.strip(), parent["id"], digest),
        )
        rendered = render_prompt_bundle(connection, book_id, task_key, int(cursor.lastrowid))
    return prompt_bundle_payload(rendered)


@app.post("/api/prompt-bundles/{bundle_id}/promote")
def promote_prompt_bundle(bundle_id: int, book_id: int = Query(gt=0)) -> dict[str, Any]:
    """用户显式确认后，把已经试跑的草稿提升为正式版本。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        bundle = connection.execute("SELECT * FROM prompt_bundle_versions WHERE id = ?", (bundle_id,)).fetchone()
        if bundle is None:
            raise HTTPException(status_code=404, detail="找不到这版提示词。")
        rendered = render_prompt_bundle(connection, book_id, str(bundle["task_key"]), bundle_id)
        if str(bundle["task_key"]) == "extraction":
            trial = connection.execute(
                """
                SELECT validation_json FROM run_manifests
                WHERE run_kind = 'prompt_trial' AND prompt_hash = ? AND status = 'completed'
                ORDER BY id DESC LIMIT 1
                """,
                (rendered.prompt_hash,),
            ).fetchone()
            if trial is None:
                raise HTTPException(status_code=409, detail="这版提示词还没有完成单片段试跑，不能发布。")
            validation = parse_json(trial["validation_json"], {})
            if float(validation.get("quote_integrity_percent", 0)) < 100:
                raise HTTPException(status_code=409, detail="试跑结果存在无法回到原文的引文，不能发布。")
        release_gate = evaluation_progress(connection)
        if not release_gate["release_gate_passed"]:
            raise HTTPException(
                status_code=409,
                detail=(
                    "正式评估集门禁尚未通过，不能提升为正式提示词。"
                    f"当前已确认 {release_gate['confirmed_cases']}/300 条，"
                    f"覆盖 {release_gate['book_count']}/5 本书，"
                    f"保留集占比 {release_gate['holdout_share_percent']}%。"
                ),
            )
        connection.execute(
            "UPDATE prompt_bundle_versions SET status = 'staging' WHERE task_key = ? AND status = 'production'",
            (bundle["task_key"],),
        )
        connection.execute(
            """
            UPDATE prompt_bundle_versions SET status = 'production', promoted_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (bundle_id,),
        )
        promoted = render_prompt_bundle(connection, book_id, str(bundle["task_key"]))
    return prompt_bundle_payload(promoted)


@app.post("/api/prompt-bundles/{bundle_id}/rollback")
def rollback_prompt_bundle(
    bundle_id: int,
    book_id: int = Query(gt=0),
    confirmed: bool = Query(default=False),
) -> dict[str, Any]:
    """显式恢复曾经的正式提示词；回滚保留全部版本，不删除历史。"""

    if not confirmed:
        raise HTTPException(status_code=409, detail="回滚正式提示词需要用户明确确认。")
    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        target = connection.execute(
            "SELECT * FROM prompt_bundle_versions WHERE id = ?", (bundle_id,)
        ).fetchone()
        if target is None:
            raise HTTPException(status_code=404, detail="找不到要恢复的提示词版本。")
        if str(target["status"]) == "production":
            raise HTTPException(status_code=409, detail="这版提示词当前已经是正式版本。")
        prior_trial = connection.execute(
            """
            SELECT 1 FROM run_manifests
            WHERE run_kind = 'prompt_trial' AND prompt_hash = ? AND status = 'completed'
            LIMIT 1
            """,
            (render_prompt_bundle(connection, book_id, str(target["task_key"]), bundle_id).prompt_hash,),
        ).fetchone()
        was_formal = target["promoted_at"] is not None or str(target["status"]) in {"staging", "archived"}
        if not was_formal and prior_trial is None:
            raise HTTPException(status_code=409, detail="这版提示词既未正式发布也未通过试跑，不能作为回滚目标。")
        connection.execute(
            "UPDATE prompt_bundle_versions SET status = 'archived' WHERE task_key = ? AND status = 'production'",
            (target["task_key"],),
        )
        connection.execute(
            """
            UPDATE prompt_bundle_versions SET status = 'production', promoted_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (bundle_id,),
        )
        restored = render_prompt_bundle(connection, book_id, str(target["task_key"]))
    return prompt_bundle_payload(restored)


def _trial_validation(result: dict[str, Any], source_text: str) -> dict[str, Any]:
    """逐项核对试跑引文是否连续存在于当前原文。"""

    quotes: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence_quote" and isinstance(item, str) and item:
                    quotes.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    valid = sum(1 for quote in quotes if quote in source_text)
    percent = round(valid / max(1, len(quotes)) * 100, 2)
    return {
        "quote_count": len(quotes),
        "valid_quote_count": valid,
        "quote_integrity_percent": percent,
        "structural_output": True,
    }


@app.post("/api/prompt-bundles/{bundle_id}/trial")
async def trial_prompt_bundle(bundle_id: int, request: PromptTrialRequest) -> dict[str, Any]:
    """使用单个片段试跑草稿，结果只进入运行清单而不写入正式知识库。"""

    with transaction(settings.database_path) as connection:
        book = ensure_book(connection, request.book_id)
        bundle = connection.execute("SELECT * FROM prompt_bundle_versions WHERE id = ?", (bundle_id,)).fetchone()
        if bundle is None or str(bundle["task_key"]) != "extraction":
            raise HTTPException(status_code=404, detail="单片段试跑需要片段抽取提示词。")
        segment = connection.execute(
            "SELECT * FROM segments WHERE id = ? AND book_id = ?", (request.segment_id, request.book_id)
        ).fetchone()
        if segment is None:
            raise HTTPException(status_code=404, detail="找不到这段原文。")
        rendered = render_prompt_bundle(connection, request.book_id, "extraction", bundle_id)
        provider = create_provider(settings, request.provider, request.book_id, bundle_id)
        manifest_id = create_run_manifest(
            connection,
            book_id=request.book_id,
            job_id=None,
            run_kind="prompt_trial",
            provider=provider.name,
            model=provider.model,
            auth_mode=provider.auth_mode,
            prompt=rendered,
            input_scope={"segment_id": int(segment["id"]), "ordinal": int(segment["ordinal"])},
            input_hash=stable_hash(book["source_hash"], segment["anchor"], segment["text"]),
        )
    started = time.monotonic()
    try:
        with connect(settings.database_path) as connection:
            context = build_analysis_context(connection, request.book_id, int(segment["ordinal"]))
        response = await provider.extract(
            str(segment["chapter_title"]), int(segment["ordinal"]), str(segment["text"]), context
        )
        result = response.extraction.model_dump(mode="json")
        validation = _trial_validation(result, str(segment["text"]))
        snapshot = pricing_for(provider.name, provider.model)
        amount = calculate_cost_usd(
            response.cache_hit_input_tokens,
            response.cache_miss_input_tokens,
            response.output_tokens,
            snapshot,
        )
        duration_ms = round((time.monotonic() - started) * 1_000)
        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                INSERT INTO model_call_ledger(
                    book_id, purpose, provider, model, prompt_version, request_hash, status,
                    input_tokens, output_tokens, cache_hit_input_tokens, cache_miss_input_tokens,
                    estimated_cost_usd, run_manifest_id, prompt_hash, duration_ms, auth_mode
                ) VALUES (?, 'prompt_trial', ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.book_id, provider.name, provider.model,
                    provider.prompt_version("extraction", "prompt-trial-v1", rendered.system_prompt),
                    stable_hash(book["source_hash"], segment["anchor"], rendered.prompt_hash),
                    response.input_tokens, response.output_tokens, response.cache_hit_input_tokens,
                    response.cache_miss_input_tokens, amount, manifest_id, rendered.prompt_hash,
                    duration_ms, provider.auth_mode,
                ),
            )
            complete_run_manifest(
                connection, manifest_id, status="completed",
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                estimated_cost_usd=amount, duration_ms=duration_ms, validation=validation,
            )
        return {
            "manifest_id": manifest_id,
            "provider": provider.name,
            "model": provider.model,
            "auth_mode": provider.auth_mode,
            "result": result,
            "validation": validation,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "estimated_cost_usd": amount,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        usage = getattr(exc, "usage", None)
        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                INSERT INTO model_call_ledger(
                    book_id, purpose, provider, model, prompt_version, request_hash, status,
                    input_tokens, output_tokens, run_manifest_id, prompt_hash, duration_ms,
                    auth_mode, error
                ) VALUES (?, 'prompt_trial', ?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.book_id, provider.name, provider.model,
                    provider.prompt_version("extraction", "prompt-trial-v1", rendered.system_prompt),
                    stable_hash(book["source_hash"], segment["anchor"], rendered.prompt_hash),
                    int(getattr(usage, "input_tokens", 0)), int(getattr(usage, "output_tokens", 0)),
                    manifest_id, rendered.prompt_hash, round((time.monotonic() - started) * 1_000),
                    provider.auth_mode, str(exc)[:500],
                ),
            )
            complete_run_manifest(
                connection, manifest_id, status="failed",
                input_tokens=int(getattr(usage, "input_tokens", 0)),
                output_tokens=int(getattr(usage, "output_tokens", 0)),
                duration_ms=round((time.monotonic() - started) * 1_000),
                conflicts=[{"message": str(exc)[:500]}],
            )
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _assert_declarative(statement: str) -> str:
    """阅读规则必须使用陈述句，疑问句会被明确退回。"""

    cleaned = statement.strip()
    if cleaned.endswith(("?", "？")):
        raise HTTPException(status_code=422, detail="阅读规则必须写成陈述句，请直接说明系统应当怎样判断。")
    return cleaned


@app.post("/api/books/{book_id}/domain-rules", status_code=201)
def create_domain_rule(book_id: int, request: DomainRuleCreate) -> dict[str, Any]:
    """新增可版本化阅读规则，保存后不会自动重跑整本书。"""

    statement = _assert_declarative(request.statement)
    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        cursor = connection.execute(
            """
            INSERT INTO domain_rules(book_id, task_key, statement, rationale, examples_json, priority, active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, request.task_key, statement, request.rationale.strip(),
                json.dumps(request.examples, ensure_ascii=False), request.priority, int(request.active),
            ),
        )
        row = connection.execute("SELECT * FROM domain_rules WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _domain_rule_payload(row)


@app.patch("/api/domain-rules/{rule_id}")
def patch_domain_rule(rule_id: int, request: DomainRulePatch) -> dict[str, Any]:
    """编辑或停用阅读规则，每次改动都会增加规则版本。"""

    with transaction(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM domain_rules WHERE id = ?", (rule_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到这条阅读规则。")
        statement = _assert_declarative(request.statement) if request.statement is not None else str(row["statement"])
        values = {
            "task_key": request.task_key or row["task_key"],
            "statement": statement,
            "rationale": request.rationale.strip() if request.rationale is not None else row["rationale"],
            "examples": request.examples if request.examples is not None else parse_json(row["examples_json"], []),
            "priority": request.priority if request.priority is not None else row["priority"],
            "active": request.active if request.active is not None else bool(row["active"]),
        }
        connection.execute(
            """
            UPDATE domain_rules SET task_key = ?, statement = ?, rationale = ?, examples_json = ?,
                priority = ?, active = ?, version = version + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (
                values["task_key"], values["statement"], values["rationale"],
                json.dumps(values["examples"], ensure_ascii=False), values["priority"],
                int(values["active"]), rule_id,
            ),
        )
        updated = connection.execute("SELECT * FROM domain_rules WHERE id = ?", (rule_id,)).fetchone()
    return _domain_rule_payload(updated)


@app.delete("/api/domain-rules/{rule_id}", status_code=204)
def delete_domain_rule(rule_id: int) -> Response:
    """删除阅读规则；正式提示词版本仍保留历史哈希。"""

    with transaction(settings.database_path) as connection:
        if connection.execute("SELECT 1 FROM domain_rules WHERE id = ?", (rule_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="找不到这条阅读规则。")
        connection.execute("DELETE FROM domain_rules WHERE id = ?", (rule_id,))
    return Response(status_code=204)


@app.post("/api/books/{book_id}/external-facts", status_code=201)
def create_external_fact(book_id: int, request: ExternalFactCreate) -> dict[str, Any]:
    """保存带来源的外部资料，它不会进入原文证据链。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        cursor = connection.execute(
            """
            INSERT INTO external_facts(book_id, statement, source_label, source_url, active)
            VALUES (?, ?, ?, ?, ?)
            """,
            (book_id, request.statement.strip(), request.source_label.strip(), request.source_url.strip(), int(request.active)),
        )
        row = connection.execute("SELECT * FROM external_facts WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return {**dict(row), "active": bool(row["active"]), "injected_into_extraction": False}


@app.patch("/api/external-facts/{fact_id}")
def patch_external_fact(fact_id: int, request: ExternalFactPatch) -> dict[str, Any]:
    """编辑或停用作品外资料，来源标签始终必填。"""

    with transaction(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM external_facts WHERE id = ?", (fact_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到这条外部资料。")
        connection.execute(
            """
            UPDATE external_facts SET statement = ?, source_label = ?, source_url = ?, active = ?,
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (
                request.statement.strip() if request.statement is not None else row["statement"],
                request.source_label.strip() if request.source_label is not None else row["source_label"],
                request.source_url.strip() if request.source_url is not None else row["source_url"],
                int(request.active if request.active is not None else bool(row["active"])), fact_id,
            ),
        )
        updated = connection.execute("SELECT * FROM external_facts WHERE id = ?", (fact_id,)).fetchone()
    return {**dict(updated), "active": bool(updated["active"]), "injected_into_extraction": False}


@app.delete("/api/external-facts/{fact_id}", status_code=204)
def delete_external_fact(fact_id: int) -> Response:
    """删除外部资料，不影响已经保存的原文事实。"""

    with transaction(settings.database_path) as connection:
        if connection.execute("SELECT 1 FROM external_facts WHERE id = ?", (fact_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="找不到这条外部资料。")
        connection.execute("DELETE FROM external_facts WHERE id = ?", (fact_id,))
    return Response(status_code=204)


@app.get("/api/books/{book_id}/runs")
def list_run_manifests(book_id: int, limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    """列出模型运行、试跑、评估与其完整版本信息。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        return [
            manifest_payload(row)
            for row in connection.execute(
                "SELECT * FROM run_manifests WHERE book_id = ? ORDER BY id DESC LIMIT ?", (book_id, limit)
            ).fetchall()
        ]


@app.get("/api/runs/{manifest_id}")
def get_run_manifest(manifest_id: int) -> dict[str, Any]:
    """读取一次模型调用的提示词版本、范围、用量、验证和冲突。"""

    with connect(settings.database_path) as connection:
        row = connection.execute("SELECT * FROM run_manifests WHERE id = ?", (manifest_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="找不到这次运行。")
        payload = manifest_payload(row)
        payload["calls"] = rows(connection.execute(
            """
            SELECT id, purpose, provider, model, prompt_version, prompt_hash, status, cache_hit,
                input_tokens, output_tokens, estimated_cost_usd, duration_ms, auth_mode, error, created_at
            FROM model_call_ledger WHERE run_manifest_id = ? ORDER BY id
            """,
            (manifest_id,),
        ).fetchall())
    return payload


@app.get("/api/model-routes")
def list_model_routes() -> list[dict[str, Any]]:
    """返回模型是否可用、是否达标和最近赛马结果。"""

    availability = {item["id"]: item for item in providers()}
    with connect(settings.database_path) as connection:
        routes = rows(connection.execute("SELECT * FROM model_routes ORDER BY priority, id").fetchall())
    for route in routes:
        route["enabled"] = bool(route["enabled"])
        route["eligible"] = bool(route["eligible"])
        route["available"] = bool(availability.get(route["provider"], {}).get("available"))
        route["benchmark"] = parse_json(route.pop("benchmark_json"), {})
    return routes


@app.patch("/api/model-routes/{provider_name}")
def patch_model_route(provider_name: str, request: ModelRoutePatch) -> dict[str, Any]:
    """允许用户停用模型、调整优先级或复位熔断，不允许手工伪造达标资格。"""

    with transaction(settings.database_path) as connection:
        route = connection.execute(
            "SELECT * FROM model_routes WHERE provider = ?", (provider_name,)
        ).fetchone()
        if route is None:
            raise HTTPException(status_code=404, detail="找不到这条模型路由。")
        connection.execute(
            """
            UPDATE model_routes SET enabled = ?, priority = ?,
                consecutive_failures = CASE WHEN ? THEN 0 ELSE consecutive_failures END,
                circuit_open_until = CASE WHEN ? THEN NULL ELSE circuit_open_until END,
                updated_at = CURRENT_TIMESTAMP WHERE provider = ?
            """,
            (
                int(request.enabled if request.enabled is not None else bool(route["enabled"])),
                request.priority if request.priority is not None else int(route["priority"]),
                int(request.reset_circuit), int(request.reset_circuit), provider_name,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM model_routes WHERE provider = ?", (provider_name,)
        ).fetchone()
    payload = dict(updated)
    payload["enabled"] = bool(payload["enabled"])
    payload["eligible"] = bool(payload["eligible"])
    payload["benchmark"] = parse_json(payload.pop("benchmark_json"), {})
    return payload


@app.get("/api/eval-suites/release-gate")
def release_gate() -> dict[str, Any]:
    """公开全库评估规模和正式发布门禁，不暴露保留集答案。"""

    with transaction(settings.database_path) as connection:
        return evaluation_progress(connection)


@app.get("/api/eval-suites/corpus")
def evaluation_corpus() -> dict[str, Any]:
    """Return the declared quality corpus without exposing any case answers."""

    manifest_path = ROOT / "evals" / "quality_corpus_manifest.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=503, detail="正式质量语料清单尚未随当前版本提供")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="正式质量语料清单无法读取") from exc
    return payload


@app.get("/api/eval-suites")
def list_eval_suites() -> dict[str, Any]:
    """按评估集汇总案例规模、保留集比例和结果，不公开保留集答案。"""

    with transaction(settings.database_path) as connection:
        progress = evaluation_progress(connection)
        suites = rows(connection.execute(
            """
            SELECT suite_name, COUNT(*) AS confirmed_cases,
                COUNT(DISTINCT book_id) AS book_count,
                SUM(CASE WHEN review_status = 'sealed_holdout'
                    AND second_review_status = 'confirmed' THEN 1 ELSE 0 END) AS holdout_cases,
                SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS passed_cases,
                SUM(CASE WHEN critical = 1 AND COALESCE(passed, 0) != 1 THEN 1 ELSE 0 END) AS critical_failures
            FROM quality_benchmark_cases WHERE confirmed_by_user = 1
              AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
            GROUP BY suite_name ORDER BY suite_name
            """
        ).fetchall())
    for suite in suites:
        suite["accuracy_percent"] = round(
            int(suite["passed_cases"] or 0) / max(1, int(suite["confirmed_cases"] or 0)) * 100, 2
        )
        suite["holdout_share_percent"] = round(
            int(suite["holdout_cases"] or 0) / max(1, int(suite["confirmed_cases"] or 0)) * 100, 2
        )
    return {"release_gate": progress, "suites": suites}


@app.post("/api/books/{book_id}/model-races")
async def run_model_race(book_id: int, request: ModelRaceRequest) -> dict[str, Any]:
    """用同一片段和同一金标准比较候选模型，只有达标模型才取得自动路由资格。"""

    with transaction(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        summary = evaluate_benchmarks(connection, book_id)
        global_gate = evaluation_progress(connection)
        segment = None
        if request.run_live_canary:
            if request.segment_id is None:
                segment = connection.execute(
                    "SELECT * FROM segments WHERE book_id = ? ORDER BY LENGTH(text) DESC, id LIMIT 1", (book_id,)
                ).fetchone()
            else:
                segment = connection.execute(
                    "SELECT * FROM segments WHERE id = ? AND book_id = ?", (request.segment_id, book_id)
                ).fetchone()
            if segment is None:
                raise HTTPException(status_code=404, detail="找不到赛马使用的原文片段。")
        prompt = render_prompt_bundle(connection, book_id, "extraction")
        current_suite_version = suite_version(connection, book_id)
    reports: list[dict[str, Any]] = []
    for provider_name in request.providers:
        with transaction(settings.database_path) as connection:
            route_seed = connection.execute(
                "SELECT * FROM model_routes WHERE provider = ?", (provider_name,)
            ).fetchone()
            manifest_id = create_run_manifest(
                connection,
                book_id=book_id,
                job_id=None,
                run_kind="model_race_canary" if request.run_live_canary else "model_race_readiness",
                provider=provider_name,
                model=str(route_seed["model"] if route_seed is not None else provider_name),
                auth_mode=str(route_seed["auth_mode"] if route_seed is not None else "unknown"),
                prompt=prompt,
                input_scope={
                    "segment_id": int(segment["id"]) if segment is not None else None,
                    "live_canary": request.run_live_canary,
                    "suite_version": current_suite_version,
                },
                input_hash=stable_hash(
                    book["source_hash"], provider_name,
                    segment["anchor"] if segment is not None else current_suite_version,
                ),
            )
        report: dict[str, Any] = {
            "provider": provider_name,
            "suite_version": current_suite_version,
            "total_cases": summary["total"],
            "passed_cases": summary["passed"],
            "critical_failures": summary["critical_failed"],
            "accuracy_percent": summary["accuracy_percent"],
            "evidence_percent": None,
            "eligible": False,
            "run_manifest_id": manifest_id,
            "status": "需要真实单片段赛马" if not request.run_live_canary else "running",
        }
        if request.run_live_canary and segment is not None:
            started = time.monotonic()
            try:
                provider = create_provider(settings, provider_name, book_id)
                with connect(settings.database_path) as connection:
                    context = build_analysis_context(connection, book_id, int(segment["ordinal"]))
                response = await provider.extract(
                    str(segment["chapter_title"]), int(segment["ordinal"]), str(segment["text"]), context
                )
                validation = _trial_validation(response.extraction.model_dump(mode="json"), str(segment["text"]))
                snapshot = pricing_for(provider.name, provider.model)
                amount = calculate_cost_usd(
                    response.cache_hit_input_tokens, response.cache_miss_input_tokens,
                    response.output_tokens, snapshot,
                )
                report.update({
                    "model": provider.model,
                    "auth_mode": provider.auth_mode,
                    "evidence_percent": validation["quote_integrity_percent"],
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "estimated_cost_usd": amount,
                    "duration_ms": round((time.monotonic() - started) * 1_000),
                    "eligible": bool(global_gate["release_gate_passed"] and validation["quote_integrity_percent"] == 100),
                    "status": (
                        "达标"
                        if global_gate["release_gate_passed"] and validation["quote_integrity_percent"] == 100
                        else "试跑通过，正式门禁未通过"
                        if validation["quote_integrity_percent"] == 100
                        else "证据校验未通过"
                    ),
                })
            except Exception as exc:
                report.update({"status": "运行失败", "error": str(exc)[:500], "duration_ms": round((time.monotonic() - started) * 1_000)})
        with transaction(settings.database_path) as connection:
            route = connection.execute("SELECT * FROM model_routes WHERE provider = ?", (provider_name,)).fetchone()
            model = str(report.get("model") or (route["model"] if route is not None else provider_name))
            cursor = connection.execute(
                """
                INSERT INTO model_race_runs(
                    book_id, provider, model, prompt_hash, suite_version, total_cases, passed_cases,
                    critical_failures, accuracy_percent, evidence_percent, input_tokens, output_tokens,
                    estimated_cost_usd, duration_ms, eligible, report_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    book_id, provider_name, model, prompt.prompt_hash, current_suite_version,
                    report["total_cases"], report["passed_cases"], report["critical_failures"],
                    report["accuracy_percent"], report.get("evidence_percent"),
                    report.get("input_tokens", 0), report.get("output_tokens", 0),
                    report.get("estimated_cost_usd"), report.get("duration_ms", 0),
                    int(report["eligible"]), json.dumps(report, ensure_ascii=False),
                ),
            )
            report["race_id"] = int(cursor.lastrowid)
            if request.run_live_canary:
                operational_success = not bool(report.get("error"))
                connection.execute(
                    """
                    INSERT INTO model_call_ledger(
                        book_id, purpose, provider, model, prompt_version, request_hash, status,
                        input_tokens, output_tokens, estimated_cost_usd, run_manifest_id,
                        prompt_hash, duration_ms, auth_mode, error
                    ) VALUES (?, 'model_race_canary', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id, provider_name, model,
                        f"race-{prompt.prompt_hash[:12]}",
                        stable_hash(book["source_hash"], provider_name, segment["anchor"] if segment is not None else ""),
                        "failed" if report.get("error") else "completed",
                        report.get("input_tokens", 0), report.get("output_tokens", 0),
                        report.get("estimated_cost_usd"), manifest_id, prompt.prompt_hash,
                        report.get("duration_ms", 0), report.get("auth_mode", route["auth_mode"] if route is not None else "unknown"),
                        report.get("error", ""),
                    ),
                )
            complete_run_manifest(
                connection,
                manifest_id,
                status="failed" if report.get("error") else ("completed" if request.run_live_canary else "prepared"),
                input_tokens=int(report.get("input_tokens", 0)),
                output_tokens=int(report.get("output_tokens", 0)),
                estimated_cost_usd=report.get("estimated_cost_usd"),
                duration_ms=int(report.get("duration_ms", 0)),
                validation={
                    "eligible": bool(report["eligible"]),
                    "accuracy_percent": report.get("accuracy_percent"),
                    "evidence_percent": report.get("evidence_percent"),
                    "release_gate_passed": bool(global_gate["release_gate_passed"]),
                    "error": report.get("error", ""),
                },
            )
            if request.run_live_canary:
                connection.execute(
                    """
                    UPDATE model_routes SET eligible = ?, benchmark_json = ?,
                        consecutive_failures = CASE WHEN ? THEN 0 ELSE consecutive_failures + 1 END,
                        circuit_open_until = CASE
                            WHEN ? THEN NULL
                            WHEN consecutive_failures + 1 >= 3 THEN datetime('now', '+30 minutes')
                            ELSE circuit_open_until END,
                        updated_at = CURRENT_TIMESTAMP WHERE provider = ?
                    """,
                    (
                        int(report["eligible"]), json.dumps(report, ensure_ascii=False),
                        int(operational_success), int(operational_success), provider_name,
                    ),
                )
        reports.append(report)
    return {
        "book_id": book_id,
        "prompt_hash": prompt.prompt_hash,
        "suite_version": current_suite_version,
        "live_canary": request.run_live_canary,
        "release_gate": global_gate,
        "reports": reports,
        "selection_rule": "关键案例全通过且总体准确率至少百分之九十五后，优先选择合格事实成本更低的模型",
    }


@app.get("/api/books/{book_id}/overview")
def overview(
    book_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """返回同一剧透边界内的全部派生视图。"""

    with connect(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        visible = window.through_segment
        window_start = window.from_segment
        raw_entities = rows(
            connection.execute(
                """
                SELECT e.*, (SELECT COUNT(*) FROM evidence x WHERE x.target_type = 'entity' AND x.target_id = e.id) AS evidence_count
                FROM entities e WHERE e.book_id = ? AND e.first_segment <= ? ORDER BY e.importance DESC, e.name
                """,
                (book_id, visible),
            ).fetchall()
        )
        visible_entity_ids = {item["id"] for item in raw_entities}
        membership_rows = connection.execute(
            """
            SELECT m.entity_id, m.cluster_id, c.canonical_entity_id, c.confidence AS identity_confidence
            FROM identity_cluster_members m
            JOIN identity_clusters c ON c.id = m.cluster_id
            JOIN entities member ON member.id = m.entity_id
            WHERE c.book_id = ? AND c.status = 'active' AND member.first_segment <= ?
            """,
            (book_id, visible),
        ).fetchall()
        canonical_by_entity = {int(item["id"]): int(item["id"]) for item in raw_entities}
        cluster_by_entity: dict[int, int] = {}
        cluster_confidence: dict[int, float] = {}
        visible_members_by_cluster: dict[int, list[int]] = {}
        for item in membership_rows:
            entity_id = int(item["entity_id"])
            cluster_id = int(item["cluster_id"])
            cluster_by_entity[entity_id] = cluster_id
            cluster_confidence[cluster_id] = float(item["identity_confidence"])
            visible_members_by_cluster.setdefault(cluster_id, []).append(entity_id)
        for item in membership_rows:
            entity_id = int(item["entity_id"])
            cluster_id = int(item["cluster_id"])
            preferred = int(item["canonical_entity_id"])
            representative = preferred if preferred in visible_entity_ids else min(
                visible_members_by_cluster[cluster_id],
                key=lambda value: (int(next(raw["first_segment"] for raw in raw_entities if raw["id"] == value)), value),
            )
            canonical_by_entity[entity_id] = representative
        alias_rows = connection.execute(
            "SELECT entity_id, alias FROM aliases WHERE entity_id IN (SELECT id FROM entities WHERE book_id = ? AND first_segment <= ?)",
            (book_id, visible),
        ).fetchall()
        aliases: dict[int, list[str]] = {}
        for item in alias_rows:
            aliases.setdefault(int(item["entity_id"]), []).append(item["alias"])
        raw_by_id = {int(item["id"]): item for item in raw_entities}
        member_ids_by_canonical: dict[int, list[int]] = {}
        for entity_id, canonical_id in canonical_by_entity.items():
            member_ids_by_canonical.setdefault(canonical_id, []).append(entity_id)
        entities: list[dict[str, Any]] = []
        for canonical_id, member_ids in member_ids_by_canonical.items():
            canonical = dict(raw_by_id.get(canonical_id) or raw_by_id[member_ids[0]])
            canonical["id"] = canonical_id
            canonical["importance"] = max(float(raw_by_id[item]["importance"]) for item in member_ids)
            canonical["first_segment"] = min(int(raw_by_id[item]["first_segment"]) for item in member_ids)
            canonical["evidence_count"] = sum(int(raw_by_id[item]["evidence_count"]) for item in member_ids)
            all_aliases = {
                value
                for item in member_ids
                for value in [raw_by_id[item]["name"], *aliases.get(item, [])]
                if value != canonical["name"]
            }
            canonical["aliases"] = sorted(all_aliases)
            canonical["identity_member_ids"] = sorted(member_ids)
            canonical["identity_cluster_id"] = cluster_by_entity.get(member_ids[0])
            canonical["identity_confidence"] = cluster_confidence.get(cluster_by_entity.get(member_ids[0], -1), 1.0)
            entities.append(canonical)
        entities.sort(key=lambda item: (-float(item["importance"]), str(item["name"])))
        display_by_id = {int(item["id"]): item for item in entities}

        claim_rows = connection.execute(
            """
            SELECT c.*, s.name AS source_name, s.kind AS source_kind, t.name AS target_name, t.kind AS target_kind,
                (SELECT COUNT(*) FROM evidence x WHERE x.target_type = 'claim' AND x.target_id = c.id) AS evidence_count
            FROM claims c
            JOIN entities s ON s.id = c.source_entity_id
            JOIN entities t ON t.id = c.target_entity_id
            WHERE c.book_id = ? AND c.first_segment <= ? AND c.status != 'rejected'
            ORDER BY c.confidence DESC, c.id
            """,
            (book_id, visible),
        ).fetchall()
        claims_by_key: dict[tuple[int, int, str, int], dict[str, Any]] = {}
        for row in claim_rows:
            if row["source_entity_id"] not in visible_entity_ids or row["target_entity_id"] not in visible_entity_ids:
                continue
            claim = dict(row)
            claim["source_entity_id"] = canonical_by_entity.get(int(row["source_entity_id"]), int(row["source_entity_id"]))
            claim["target_entity_id"] = canonical_by_entity.get(int(row["target_entity_id"]), int(row["target_entity_id"]))
            if claim["source_entity_id"] == claim["target_entity_id"]:
                continue
            claim["source_name"] = display_by_id.get(claim["source_entity_id"], {}).get("name", row["source_name"])
            claim["target_name"] = display_by_id.get(claim["target_entity_id"], {}).get("name", row["target_name"])
            key = (
                claim["source_entity_id"], claim["target_entity_id"],
                str(claim["predicate"]), int(claim["first_segment"]),
            )
            previous = claims_by_key.get(key)
            if previous is None or float(claim["confidence"]) > float(previous["confidence"]):
                claims_by_key[key] = claim
        claims = list(claims_by_key.values())

        event_rows = connection.execute(
            """
            SELECT v.*, p.name AS location_name,
                (SELECT COUNT(*) FROM evidence x WHERE x.target_type = 'event' AND x.target_id = v.id) AS evidence_count
            FROM events v LEFT JOIN entities p ON p.id = v.location_entity_id
            WHERE v.book_id = ? AND v.first_segment <= ? ORDER BY v.story_order, v.narrative_order
            """,
            (book_id, visible),
        ).fetchall()
        events = rows(event_rows)
        for event in events:
            if event["location_entity_id"] is not None:
                event["location_entity_id"] = canonical_by_entity.get(
                    int(event["location_entity_id"]), int(event["location_entity_id"]),
                )
                event["location_name"] = display_by_id.get(event["location_entity_id"], {}).get(
                    "name", event["location_name"],
                )
            raw_participants = rows(
                connection.execute(
                    """
                    SELECT e.id, e.name, ep.role FROM event_participants ep
                    JOIN entities e ON e.id = ep.entity_id WHERE ep.event_id = ?
                    """,
                    (event["id"],),
                ).fetchall()
            )
            participants_by_id: dict[int, dict[str, Any]] = {}
            for person in raw_participants:
                canonical_id = canonical_by_entity.get(int(person["id"]), int(person["id"]))
                current = participants_by_id.setdefault(
                    canonical_id,
                    {
                        "id": canonical_id,
                        "name": display_by_id.get(canonical_id, {}).get("name", person["name"]),
                        "role": str(person["role"]),
                    },
                )
                if person["role"] not in current["role"]:
                    current["role"] += "、" + str(person["role"])
            event["participants"] = list(participants_by_id.values())

        settings_row = connection.execute(
            "SELECT * FROM book_settings WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        protagonist = None
        if settings_row is not None and not bool(settings_row["auto_protagonist"]) and settings_row["protagonist_entity_id"]:
            configured_id = canonical_by_entity.get(
                int(settings_row["protagonist_entity_id"]), int(settings_row["protagonist_entity_id"]),
            )
            protagonist = next(
                (item for item in entities if item["id"] == configured_id),
                None,
            )
        if protagonist is None and (settings_row is None or settings_row["auto_protagonist"]):
            subject_id = select_main_subject(connection, book_id)
            if subject_id is not None:
                subject_id = canonical_by_entity.get(subject_id, subject_id)
                protagonist = next((item for item in entities if item["id"] == subject_id), None)

        routes: list[dict[str, Any]] = []
        journey_rows: list[sqlite3.Row] = []
        if protagonist is not None:
            protagonist_member_ids = protagonist.get("identity_member_ids", [protagonist["id"]])
            placeholders = ",".join("?" for _ in protagonist_member_ids)
            journey_rows = connection.execute(
                f"""
                SELECT j.*, source.name AS from_name, target.name AS to_name,
                    e.story_order, e.title AS event_title
                FROM journey_legs j
                LEFT JOIN entities source ON source.id = j.from_entity_id
                LEFT JOIN entities target ON target.id = j.to_entity_id
                LEFT JOIN events e ON e.id = j.event_id
                WHERE j.book_id = ? AND j.first_segment <= ?
                  AND j.subject_entity_id IN ({placeholders})
                ORDER BY COALESCE(e.story_order, j.ordinal), j.ordinal, j.id
                """,  # noqa: S608
                (book_id, visible, *protagonist_member_ids),
            ).fetchall()
        for row in journey_rows:
            route = dict(row)
            if route["from_entity_id"] is not None:
                route["from_id"] = canonical_by_entity.get(int(route["from_entity_id"]), int(route["from_entity_id"]))
                route["from_name"] = display_by_id.get(route["from_id"], {}).get("name", route["from_name"])
            else:
                route["from_id"] = None
            if route["to_entity_id"] is not None:
                route["to_id"] = canonical_by_entity.get(int(route["to_entity_id"]), int(route["to_entity_id"]))
                route["to_name"] = display_by_id.get(route["to_id"], {}).get("name", route["to_name"])
            else:
                route["to_id"] = None
            routes.append(route)
        # 编年和逻辑地图共享同一份故事步骤。主角只决定地图上的人物轨迹层，
        # 不再过滤事件，否则地图步数会与编年产生永久偏差。
        route_by_event_id = {
            int(route["event_id"]): route
            for route in routes
            if route.get("event_id") is not None
        }
        story_map_steps: list[dict[str, Any]] = []
        for canonical_index, event in enumerate(events):
            step = dict(event)
            route = route_by_event_id.get(int(event["id"]))
            step.update(
                {
                    "event_id": int(event["id"]),
                    "canonical_index": canonical_index,
                    "location_state": "known" if event.get("location_entity_id") is not None else "unknown",
                    "route_id": int(route["id"]) if route is not None else None,
                    "route_from_id": route.get("from_id") if route is not None else None,
                    "route_to_id": route.get("to_id") if route is not None else None,
                    "route_status": route.get("gap_status") if route is not None else "not_recorded",
                    "route_transport": route.get("transport") if route is not None else event.get("transport"),
                }
            )
            story_map_steps.append(step)
        # 旧客户端继续读取 journey_events；新客户端使用 story_map_steps。
        # 两个字段指向相同的有序数据，迁移期间也不会出现两套行程。
        journey_events = story_map_steps

        geography_relations = rows(
            connection.execute(
                """
                SELECT g.*, source.name AS source_name, target.name AS target_name,
                    (SELECT COUNT(*) FROM evidence x
                     WHERE x.target_type = 'place_relation' AND x.target_id = g.id) AS evidence_count
                FROM place_relations g
                JOIN entities source ON source.id = g.source_entity_id
                JOIN entities target ON target.id = g.target_entity_id
                WHERE g.book_id = ? AND g.first_segment <= ?
                ORDER BY g.confidence DESC, g.first_segment, g.id
                """,
                (book_id, visible),
            ).fetchall()
        )

        world_notes = rows(
            connection.execute(
                """
                SELECT w.*, (SELECT COUNT(*) FROM evidence x WHERE x.target_type = 'world_note' AND x.target_id = w.id) AS evidence_count
                FROM world_notes w
                WHERE w.book_id = ? AND w.first_segment <= ?
                  AND w.archived_at IS NULL
                  AND (
                    w.created_by = 'synthesis'
                    OR NOT EXISTS (
                        SELECT 1 FROM synthesis_basis sb
                        JOIN world_notes synthesis ON synthesis.id = sb.world_note_id
                        WHERE sb.basis_type = 'world_note' AND sb.basis_id = w.id
                          AND synthesis.book_id = w.book_id
                          AND synthesis.first_segment <= ?
                    )
                  )
                ORDER BY w.category, w.title
                """,
                (book_id, visible, visible),
            ).fetchall()
        )
        entry_rows = connection.execute(
            """
            SELECT d.*, (SELECT COUNT(*) FROM evidence x WHERE x.target_type = 'entry' AND x.target_id = d.id) AS evidence_count
            FROM entries d WHERE d.book_id = ? AND d.first_segment <= ? ORDER BY d.category, d.name
            """,
            (book_id, visible),
        ).fetchall()
        entries = rows(entry_rows)
        for entry in entries:
            try:
                entry["attributes"] = json.loads(entry.pop("attributes_json"))
            except json.JSONDecodeError:
                entry["attributes"] = {}
        runs = rows(
            connection.execute(
                "SELECT * FROM analysis_runs WHERE book_id = ? ORDER BY id DESC LIMIT 8",
                (book_id,),
            ).fetchall()
        )
        jobs = rows(
            connection.execute(
                "SELECT * FROM analysis_jobs WHERE book_id = ? ORDER BY id DESC LIMIT 8",
                (book_id,),
            ).fetchall()
        )
        cost_row = connection.execute(
            """
            SELECT COUNT(*) AS job_count,
                SUM(CASE WHEN estimated_cost_usd IS NOT NULL THEN 1 ELSE 0 END) AS priced_job_count,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cache_hit_input_tokens), 0) AS cache_hit_input_tokens,
                COALESCE(SUM(cache_miss_input_tokens), 0) AS cache_miss_input_tokens,
                COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
            FROM analysis_jobs WHERE book_id = ?
            """,
            (book_id,),
        ).fetchone()
        cost_ledger = rows(
            connection.execute(
                """
                SELECT id, job_id, purpose, provider, model, status, cache_hit,
                    input_tokens, output_tokens, cache_hit_input_tokens,
                    cache_miss_input_tokens, estimated_cost_usd, created_at
                FROM model_call_ledger WHERE book_id = ? ORDER BY id DESC LIMIT 80
                """,
                (book_id,),
            ).fetchall()
        )
        identity_summary = {
            str(row["verdict"]): int(row["amount"])
            for row in connection.execute(
                """
                SELECT verdict, COUNT(*) AS amount FROM identity_decisions
                WHERE book_id = ? AND undone_at IS NULL GROUP BY verdict
                """,
                (book_id,),
            ).fetchall()
        }
        time_conflicts = rows(
            connection.execute(
                """
                SELECT edge.*, earlier.title AS earlier_title, later.title AS later_title
                FROM event_order_edges edge
                JOIN events earlier ON earlier.id = edge.earlier_event_id
                JOIN events later ON later.id = edge.later_event_id
                WHERE edge.book_id = ? AND edge.status = 'conflict'
                  AND earlier.first_segment <= ? AND later.first_segment <= ?
                ORDER BY edge.confidence DESC, edge.id
                """,
                (book_id, visible, visible),
            ).fetchall()
        )
        time_constraint_reviews = rows(
            connection.execute(
                """
                SELECT edge.*, earlier.title AS earlier_title, later.title AS later_title
                FROM event_order_edges edge
                JOIN events earlier ON earlier.id = edge.earlier_event_id
                JOIN events later ON later.id = edge.later_event_id
                WHERE edge.book_id = ?
                  AND edge.status IN ('conflict', 'rejected', 'quarantined', 'auto_rejected')
                  AND earlier.first_segment <= ? AND later.first_segment <= ?
                ORDER BY CASE edge.status WHEN 'conflict' THEN 0 ELSE 1 END,
                    COALESCE(edge.resolved_at, edge.id) DESC, edge.id DESC
                LIMIT 200
                """,
                (book_id, visible, visible),
            ).fetchall()
        )
        relationship_layouts = rows(
            connection.execute(
                "SELECT * FROM relationship_layouts WHERE book_id = ?",
                (book_id,),
            ).fetchall()
        )
        merge_candidates = rows(
            connection.execute(
                """
                SELECT MIN(m.id) AS id, m.book_id, m.left_entity_id, m.right_entity_id,
                       GROUP_CONCAT(DISTINCT m.reason) AS reason, MAX(m.confidence) AS confidence,
                       MAX(m.status) AS status,
                       l.name AS left_name, l.kind AS left_kind,
                       r.name AS right_name, r.kind AS right_kind
                FROM entity_merge_candidates m
                JOIN entities l ON l.id = m.left_entity_id
                JOIN entities r ON r.id = m.right_entity_id
                WHERE m.book_id = ? AND m.status IN ('unreviewed', 'needs_review')
                  AND l.first_segment <= ? AND r.first_segment <= ?
                GROUP BY m.book_id, m.left_entity_id, m.right_entity_id,
                         l.name, l.kind, r.name, r.kind
                ORDER BY MAX(m.confidence) DESC, MIN(m.id)
                """,
                (book_id, visible, visible),
            ).fetchall()
        )
        identity_conflict_reviews = rows(
            connection.execute(
                """
                SELECT MIN(m.id) AS id, m.book_id, m.left_entity_id, m.right_entity_id,
                       GROUP_CONCAT(DISTINCT m.reason) AS reason, MAX(m.confidence) AS confidence,
                       MAX(m.status) AS status, MAX(m.resolution_reason) AS resolution_reason,
                       MAX(m.resolved_by) AS resolved_by, MAX(m.resolved_at) AS resolved_at,
                       l.name AS left_name, l.kind AS left_kind,
                       r.name AS right_name, r.kind AS right_kind
                FROM entity_merge_candidates m
                JOIN entities l ON l.id = m.left_entity_id
                JOIN entities r ON r.id = m.right_entity_id
                WHERE m.book_id = ?
                  AND l.first_segment <= ? AND r.first_segment <= ?
                GROUP BY m.book_id, m.left_entity_id, m.right_entity_id,
                         l.name, l.kind, r.name, r.kind
                ORDER BY CASE MAX(m.status)
                    WHEN 'unreviewed' THEN 0 WHEN 'needs_review' THEN 0 ELSE 1 END,
                    MAX(m.confidence) DESC, MIN(m.id)
                LIMIT 200
                """,
                (book_id, visible, visible),
            ).fetchall()
        )
        contradiction_rows = rows(
            connection.execute(
                """
                SELECT * FROM contradictions WHERE book_id = ?
                ORDER BY CASE status WHEN 'unreviewed' THEN 0 ELSE 1 END,
                    confidence DESC, id DESC LIMIT 200
                """,
                (book_id,),
            ).fetchall()
        )
        # Contradictions are source-backed review metadata; do not reveal a
        # contradiction whose two targets only appear after the spoiler ceiling.
        def _target_first_segment(target_type: str, target_id: int) -> int | None:
            table_by_type = {
                "entity": "entities",
                "claim": "claims",
                "event": "events",
                "place_relation": "place_relations",
                "world_note": "world_notes",
                "entry": "entries",
            }
            table = table_by_type.get(target_type)
            if table is None:
                return None
            row = connection.execute(
                f"SELECT first_segment FROM {table} WHERE id = ? AND book_id = ?",  # noqa: S608
                (target_id, book_id),
            ).fetchone()
            return int(row["first_segment"]) if row is not None and row["first_segment"] is not None else None

        contradiction_rows = [
            item for item in contradiction_rows
            if all(
                first is None or first <= visible
                for first in (
                    _target_first_segment(str(item.get("left_type") or ""), int(item["left_id"])),
                    _target_first_segment(str(item.get("right_type") or ""), int(item["right_id"])),
                )
            )
        ]
        for contradiction in contradiction_rows:
            contradiction["left"] = describe_review_target(
                connection, book_id, str(contradiction["left_type"]), int(contradiction["left_id"]),
            )
            contradiction["right"] = describe_review_target(
                connection, book_id, str(contradiction["right_type"]), int(contradiction["right_id"]),
            )
        processed_segments = connection.execute(
            """
            SELECT COUNT(DISTINCT result.segment_id) FROM segment_results result
            JOIN segments segment ON segment.id = result.segment_id
            WHERE result.book_id = ? AND segment.ordinal <= ?
              AND (result.provider = 'demo' OR result.prompt_version = ?)
            """,
            (book_id, visible, PROMPT_VERSION),
        ).fetchone()[0]
        quality = build_quality_report(connection, book_id, visible, window_start)
        connectivity_reviews = rows(
            connection.execute(
                """
                SELECT review.*, entity.name, entity.kind, entity.summary
                FROM entity_connectivity_reviews review
                JOIN entities entity ON entity.id = review.entity_id
                WHERE review.book_id = ? AND entity.first_segment <= ?
                ORDER BY CASE review.status
                    WHEN 'ambiguous' THEN 0 WHEN 'pending' THEN 1
                    WHEN 'confirmed_isolated' THEN 2 ELSE 3 END,
                    entity.importance DESC, entity.name
                """,
                (book_id, visible),
            ).fetchall()
        )
        event_location_reviews = rows(
            connection.execute(
                """
                SELECT review.*, location.name AS effective_location_name,
                    event.title AS event_title, event.summary AS event_summary,
                    event.first_segment AS event_first_segment
                FROM event_location_reviews review
                LEFT JOIN entities location ON location.id = review.effective_location_entity_id
                JOIN events event ON event.id = review.event_id
                WHERE review.book_id = ? AND event.first_segment <= ?
                """,
                (book_id, visible),
            ).fetchall()
        )
        segment_titles = rows(
            connection.execute(
                "SELECT id, ordinal, chapter_title, anchor FROM segments WHERE book_id = ? AND ordinal <= ? ORDER BY ordinal",
                (book_id, visible),
            ).fetchall()
        )
        # 起点之前的对象可作为上下文，但必须带有明确标记；终点之后不进入任何派生数组
        mark_context_only(entities, window)
        mark_context_only(claims, window)
        mark_context_only(events, window)
        mark_context_only(story_map_steps, window)
        mark_context_only(routes, window)
        mark_context_only(geography_relations, window)
        mark_context_only(world_notes, window)
        mark_context_only(entries, window)
        for segment_title in segment_titles:
            segment_title["context_only"] = int(segment_title.get("ordinal", 0)) < window.from_segment
    return {
        "book": dict(book),
        "through_segment": visible,
        "from_segment": window_start,
        "reading_window": window.payload(),
        "segments": segment_titles,
        "entities": entities,
        "claims": claims,
        "events": events,
        "story_map_steps": story_map_steps,
        "chronology_event_ids": [int(item["event_id"]) for item in story_map_steps],
        "routes": routes,
        "geography_relations": geography_relations,
        "journey_events": journey_events,
        "protagonist": protagonist,
        "protagonist_auto": settings_row is None or bool(settings_row["auto_protagonist"]),
        "world_notes": world_notes,
        "entries": entries,
        "analysis_runs": runs,
        "analysis_jobs": jobs,
        "cost_summary": dict(cost_row),
        "cost_ledger": cost_ledger,
        "processed_segments": processed_segments,
        "merge_candidates": merge_candidates,
        "identity_conflict_reviews": identity_conflict_reviews,
        "identity_summary": identity_summary,
        "contradictions": contradiction_rows,
        "time_conflicts": time_conflicts,
        "time_constraint_reviews": time_constraint_reviews,
        "relationship_layouts": relationship_layouts,
        "connectivity_reviews": connectivity_reviews,
        "event_location_reviews": event_location_reviews,
        "quality": quality,
    }


@app.patch("/api/books/{book_id}/settings")
def patch_book_settings(book_id: int, patch: BookSettingsPatch) -> dict[str, Any]:
    """保存人物轨迹层选择，不改变编年与地图的故事步骤。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        if patch.protagonist_entity_id is not None:
            entity = connection.execute(
                "SELECT id FROM entities WHERE id = ? AND book_id = ? AND kind = 'person'",
                (patch.protagonist_entity_id, book_id),
            ).fetchone()
            if entity is None:
                raise HTTPException(status_code=422, detail="所选主角不属于这本书的人物。")
        connection.execute(
            """
            INSERT INTO book_settings(book_id, protagonist_entity_id, auto_protagonist, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id) DO UPDATE SET
                protagonist_entity_id = excluded.protagonist_entity_id,
                auto_protagonist = excluded.auto_protagonist,
                updated_at = CURRENT_TIMESTAMP
            """,
            (book_id, patch.protagonist_entity_id, int(patch.auto_protagonist)),
        )
        rebuild_derived_journey(connection, book_id)
    return {
        "book_id": book_id,
        "protagonist_entity_id": patch.protagonist_entity_id,
        "auto_protagonist": patch.auto_protagonist,
    }


@app.post("/api/books/{book_id}/entities/merge")
def merge_book_entities(book_id: int, request: EntityMergeRequest) -> dict[str, Any]:
    """执行可撤销的身份归并，原始实体和全部证据保持不变。"""

    try:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            decision_id = merge_identity_clusters(
                connection,
                book_id,
                request.keep_entity_id,
                request.remove_entity_id,
                request.reason,
                1.0,
                created_by="human",
            )
            connection.execute(
                """
                UPDATE entity_merge_candidates SET status = 'accepted',
                    resolution_reason = ?, resolved_by = 'human', resolved_at = CURRENT_TIMESTAMP
                WHERE book_id = ? AND (
                    (left_entity_id = ? AND right_entity_id = ?)
                    OR (left_entity_id = ? AND right_entity_id = ?)
                )
                """,
                (
                    request.reason, book_id, request.keep_entity_id, request.remove_entity_id,
                    request.remove_entity_id, request.keep_entity_id,
                ),
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "book_id": book_id,
        "kept_entity_id": request.keep_entity_id,
        "decision_id": decision_id,
        "reversible": True,
    }


@app.post("/api/identity-decisions/{decision_id}/undo")
def undo_book_identity_decision(decision_id: int) -> dict[str, Any]:
    """撤销最近一次身份归并并恢复原展示身份。"""

    try:
        with transaction(settings.database_path) as connection:
            undo_identity_decision(connection, decision_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"decision_id": decision_id, "status": "undone"}


@app.patch("/api/merge-candidates/{candidate_id}")
def reject_merge_candidate(candidate_id: int, request: MergeCandidatePatch) -> dict[str, Any]:
    """拒绝错误的同一人物建议，避免它继续出现在待办中。"""

    with transaction(settings.database_path) as connection:
        candidate = connection.execute(
            "SELECT id, book_id, left_entity_id, right_entity_id FROM entity_merge_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise HTTPException(status_code=404, detail="找不到这条合并建议。")
        connection.execute(
            """
            UPDATE entity_merge_candidates SET status = ?,
                resolution_reason = '人工确认不是同一实体', resolved_by = 'human',
                resolved_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND left_entity_id = ? AND right_entity_id = ?
            """,
            (
                request.status, candidate["book_id"],
                candidate["left_entity_id"], candidate["right_entity_id"],
            ),
        )
        connection.execute(
            """
            INSERT INTO identity_decisions(
                book_id, left_entity_id, right_entity_id, verdict, confidence,
                reason, created_by
            ) VALUES (?, ?, ?, 'separate', 1, '人工确认不是同一实体', 'human')
            """,
            (
                candidate["book_id"], candidate["left_entity_id"],
                candidate["right_entity_id"],
            ),
        )
    return {"id": candidate_id, "status": request.status}


@app.patch("/api/contradictions/{contradiction_id}")
def resolve_contradiction(contradiction_id: int, request: ContradictionPatch) -> dict[str, Any]:
    """人工关闭事实冲突，同时保留两侧原记录和来源证据。"""

    status_by_action = {
        "contextual": "resolved_contextual",
        "false_positive": "resolved_false_positive",
        "quarantine": "quarantined",
    }
    with transaction(settings.database_path) as connection:
        contradiction = connection.execute(
            "SELECT id, book_id FROM contradictions WHERE id = ?",
            (contradiction_id,),
        ).fetchone()
        if contradiction is None:
            raise HTTPException(status_code=404, detail="找不到这条事实冲突。")
        status = status_by_action[request.action]
        connection.execute(
            """
            UPDATE contradictions SET status = ?, resolution_reason = ?, resolved_by = 'human',
                resolved_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (status, request.reason.strip(), contradiction_id),
        )
    return {"id": contradiction_id, "book_id": int(contradiction["book_id"]), "status": status}


@app.patch("/api/time-conflicts/{edge_id}")
def resolve_time_conflict(edge_id: int, request: TimeConflictPatch) -> dict[str, Any]:
    """人工舍弃、隔离或反转时间约束，剧情事件本身始终保留。"""

    try:
        with transaction(settings.database_path) as connection:
            edge = connection.execute(
                "SELECT * FROM event_order_edges WHERE id = ?",
                (edge_id,),
            ).fetchone()
            if edge is None:
                raise HTTPException(status_code=404, detail="找不到这条时间约束。")
            if request.action == "reverse":
                connection.execute(
                    """
                    UPDATE event_order_edges
                    SET earlier_event_id = ?, later_event_id = ?, created_by = 'human',
                        status = 'pending', resolution_reason = ?, resolved_by = 'human',
                        resolved_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        edge["later_event_id"], edge["earlier_event_id"],
                        request.reason.strip(), edge_id,
                    ),
                )
                recompute_chronology_dag(connection, int(edge["book_id"]))
                updated = connection.execute(
                    "SELECT status FROM event_order_edges WHERE id = ?",
                    (edge_id,),
                ).fetchone()
                if updated is None or str(updated["status"]) == "conflict":
                    raise HTTPException(
                        status_code=422,
                        detail="反转后仍会形成时间循环，原约束已经保持不变。请选择舍弃或隔离。",
                    )
                status = str(updated["status"])
            else:
                status = "rejected" if request.action == "reject" else "quarantined"
                connection.execute(
                    """
                    UPDATE event_order_edges
                    SET status = ?, resolution_reason = ?, resolved_by = 'human',
                        resolved_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (status, request.reason.strip(), edge_id),
                )
                recompute_chronology_dag(connection, int(edge["book_id"]))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(
            status_code=422,
            detail="反转后的时间约束已经存在，原约束保持不变。请选择舍弃或隔离。",
        ) from exc
    return {"id": edge_id, "book_id": int(edge["book_id"]), "status": status}


@app.post("/api/books/{book_id}/conflicts/auto-resolve")
def auto_resolve_book_conflicts(book_id: int) -> dict[str, Any]:
    """先用本地无损规则关闭冲突，不产生模型费用。"""

    with transaction(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        resolution = conservatively_close_conflicts(connection, book_id)
        refresh_local_reviews(connection, book_id)
        quality = build_quality_report(connection, book_id, max(0, int(book["segment_count"]) - 1))
        model_review_count = int(connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM entity_connectivity_reviews
                 WHERE book_id = ? AND status IN ('pending', 'ambiguous'))
                + (SELECT COUNT(*) FROM event_location_reviews
                   WHERE book_id = ? AND status = 'unresolved')
            """,
            (book_id, book_id),
        ).fetchone()[0])
    return {
        "book_id": book_id,
        "resolution": resolution,
        "quality": quality,
        "needs_model_review": model_review_count > 0,
        "model_review_items": model_review_count,
        "estimated_cost_usd": 0,
    }


@app.put("/api/books/{book_id}/relationship-layout")
def put_relationship_layout(book_id: int, patch: RelationshipLayoutPatch) -> dict[str, Any]:
    """保存用户拖动后的二维或三维节点位置。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        valid_ids = {
            int(row["id"])
            for row in connection.execute("SELECT id FROM entities WHERE book_id = ?", (book_id,)).fetchall()
        }
        invalid = [node.entity_id for node in patch.nodes if node.entity_id not in valid_ids]
        if invalid:
            raise HTTPException(status_code=422, detail="布局中包含不属于本书的实体。")
        connection.executemany(
            """
            INSERT INTO relationship_layouts(book_id, entity_id, mode, x, y, z, pinned, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, entity_id, mode) DO UPDATE SET
                x = excluded.x, y = excluded.y, z = excluded.z,
                pinned = excluded.pinned, updated_at = CURRENT_TIMESTAMP
            """,
            [
                (book_id, node.entity_id, patch.mode, node.x, node.y, node.z, int(node.pinned))
                for node in patch.nodes
            ],
        )
    return {"book_id": book_id, "mode": patch.mode, "saved": len(patch.nodes)}


@app.delete("/api/books/{book_id}/relationship-layout/{mode}", status_code=204)
def delete_relationship_layout(book_id: int, mode: str) -> Response:
    """清除一个视图的固定位置，让布局重新计算。"""

    if mode not in {"2d", "3d"}:
        raise HTTPException(status_code=422, detail="布局模式只能是二维或三维。")
    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        connection.execute(
            "DELETE FROM relationship_layouts WHERE book_id = ? AND mode = ?",
            (book_id, mode),
        )
    return Response(status_code=204)


@app.patch("/api/records/{target_type}/{target_id}")
def patch_record(target_type: str, target_id: int, patch: RecordPatch) -> dict[str, Any]:
    """修正人物、事件、世界设定或数据库条目，并保存修改历史。"""

    table_map = {
        "entity": ("entities", {"name", "summary", "aliases", "kind", "importance"}),
        "event": ("events", {"title", "summary", "temporal_value", "location_entity_id", "transport_mode", "participants"}),
        "world_note": ("world_notes", {"title", "summary", "category"}),
        "entry": ("entries", {"name", "summary", "category", "attributes"}),
        "claim": ("claims", {"summary", "status", "source_entity_id", "target_entity_id", "predicate", "directionality", "reverse_predicate", "temporal_scope"}),
        "place_relation": ("place_relations", {"source_entity_id", "target_entity_id", "relative_position", "summary"}),
        "narrative_unit": ("narrative_units", {"title", "start_segment", "end_segment", "world_id"}),
        "story_world": ("story_worlds", {"name"}),
    }
    table_info = table_map.get(target_type)
    if table_info is None or patch.field_name not in table_info[1]:
        raise HTTPException(status_code=422, detail="这个字段不支持直接修改。")
    if target_type == "claim" and patch.field_name == "status" and patch.new_value not in {"unreviewed", "accepted", "rejected"}:
        raise HTTPException(status_code=422, detail="关系审核状态只能是未审核、已接受或已拒绝。")
    table = table_info[0]
    database_field = "attributes_json" if patch.field_name == "attributes" else "transport" if patch.field_name == "transport_mode" else patch.field_name
    with transaction(settings.database_path) as connection:
        record = connection.execute(
            f"SELECT * FROM {table} WHERE id = ?",  # noqa: S608
            (target_id,),
        ).fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="找不到待修改记录。")
        if patch.field_name == "aliases":
            old_value = json.dumps([str(row["alias"]) for row in connection.execute("SELECT alias FROM aliases WHERE entity_id = ? ORDER BY alias", (target_id,)).fetchall()], ensure_ascii=False)
        elif patch.field_name == "participants":
            old_value = json.dumps([dict(row) for row in connection.execute("SELECT entity_id, role FROM event_participants WHERE event_id = ? ORDER BY entity_id, role", (target_id,)).fetchall()], ensure_ascii=False)
        else:
            old_value = "" if record[database_field] is None else str(record[database_field])
        new_value = patch.new_value
        if patch.field_name == "attributes":
            try:
                attributes = json.loads(patch.new_value)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail="属性必须是完整的 JSON 对象。") from exc
            if not isinstance(attributes, dict):
                raise HTTPException(status_code=422, detail="属性必须是 JSON 对象。")
            new_value = json.dumps(attributes, ensure_ascii=False)
        if patch.field_name in {"source_entity_id", "target_entity_id", "location_entity_id"}:
            if new_value.strip() in {"", "null", "none"} and patch.field_name == "location_entity_id":
                new_value = None
            else:
                try:
                    entity_id = int(new_value)
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail="请选择有效的人物或地点") from exc
                valid = connection.execute("SELECT id FROM entities WHERE id = ? AND book_id = ?", (entity_id, int(record["book_id"]))).fetchone()
                if valid is None:
                    raise HTTPException(status_code=422, detail="选择的对象不属于当前书籍")
                new_value = entity_id
        if patch.field_name == "importance":
            try:
                importance = float(new_value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="重要程度必须是 0 到 1 之间的数字") from exc
            if not 0 <= importance <= 1:
                raise HTTPException(status_code=422, detail="重要程度必须是 0 到 1 之间的数字")
            new_value = importance
        if patch.field_name == "directionality" and new_value not in {"directed", "bidirectional"}:
            raise HTTPException(status_code=422, detail="关系方向只能是单向或双向")
        if target_type == "narrative_unit" and patch.field_name in {"start_segment", "end_segment"}:
            try:
                new_value = int(new_value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="故事边界必须是有效章节序号") from exc
            segment_count = int(connection.execute("SELECT COUNT(*) FROM segments WHERE book_id = ?", (int(record["book_id"]),)).fetchone()[0])
            other_field = "end_segment" if patch.field_name == "start_segment" else "start_segment"
            other_value = int(record[other_field])
            start_value = new_value if patch.field_name == "start_segment" else other_value
            end_value = new_value if patch.field_name == "end_segment" else other_value
            if not 0 <= start_value <= end_value < segment_count:
                raise HTTPException(status_code=422, detail="故事边界必须位于现有章节内，且开始不能晚于结束")
        if target_type == "narrative_unit" and patch.field_name == "world_id":
            try:
                new_value = int(new_value)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="请选择有效的故事世界") from exc
            if connection.execute("SELECT 1 FROM story_worlds WHERE id = ? AND book_id = ?", (new_value, int(record["book_id"]))).fetchone() is None:
                raise HTTPException(status_code=422, detail="选择的故事世界不属于当前书籍")
        if patch.field_name == "aliases":
            try:
                aliases = json.loads(patch.new_value)
            except json.JSONDecodeError:
                aliases = [item.strip() for item in patch.new_value.replace("，", ",").split(",") if item.strip()]
            if not isinstance(aliases, list) or any(not isinstance(item, str) for item in aliases):
                raise HTTPException(status_code=422, detail="别名必须是名称列表")
            connection.execute("DELETE FROM aliases WHERE entity_id = ?", (target_id,))
            connection.executemany("INSERT OR IGNORE INTO aliases(entity_id, alias) VALUES (?, ?)", [(target_id, item.strip()) for item in aliases if item.strip()])
            new_value = json.dumps([item.strip() for item in aliases if item.strip()], ensure_ascii=False)
        elif patch.field_name == "participants":
            try:
                participants = json.loads(patch.new_value)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=422, detail="参与者必须是包含人物和角色的完整列表") from exc
            if not isinstance(participants, list):
                raise HTTPException(status_code=422, detail="参与者必须是列表")
            prepared = []
            for item in participants:
                if not isinstance(item, dict) or not item.get("entity_id") or not str(item.get("role") or "").strip():
                    raise HTTPException(status_code=422, detail="每位参与者都需要人物和角色")
                entity_id = int(item["entity_id"])
                if connection.execute("SELECT 1 FROM entities WHERE id = ? AND book_id = ?", (entity_id, int(record["book_id"]))).fetchone() is None:
                    raise HTTPException(status_code=422, detail="参与者不属于当前书籍")
                prepared.append((target_id, entity_id, str(item["role"]).strip()))
            connection.execute("DELETE FROM event_participants WHERE event_id = ?", (target_id,))
            connection.executemany("INSERT INTO event_participants(event_id, entity_id, role) VALUES (?, ?, ?)", prepared)
            new_value = json.dumps(participants, ensure_ascii=False)
        else:
            try:
                connection.execute(
                    f"UPDATE {table} SET {database_field} = ?, created_by = 'human' WHERE id = ?",  # noqa: S608
                    (new_value, target_id),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="修改后的名称与现有记录冲突") from exc
        audit_new_value = "" if new_value is None else str(new_value)
        connection.execute(
            """
            INSERT INTO corrections(book_id, target_type, target_id, field_name, old_value, new_value, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["book_id"], target_type, target_id, patch.field_name,
                old_value, audit_new_value, patch.reason,
            ),
        )
        connection.execute(
            """
            INSERT INTO record_versions(book_id, target_type, target_id, field_name, value, source, reason)
            VALUES (?, ?, ?, ?, ?, 'human', ?)
            """,
            (record["book_id"], target_type, target_id, patch.field_name, audit_new_value, patch.reason),
        )
        if target_type == "entity" and patch.field_name == "name":
            connection.execute(
                "DELETE FROM entity_keys WHERE entity_id = ? AND source = 'canonical'",
                (target_id,),
            )
            register_entity_keys(connection, int(record["book_id"]))
        if target_type in {"entity", "event", "claim", "place_relation", "narrative_unit", "story_world"}:
            connection.execute("DELETE FROM map_layout_snapshots WHERE book_id = ?", (int(record["book_id"]),))
    return {"id": target_id, "field_name": patch.field_name, "new_value": new_value}


@app.post("/api/books/{book_id}/world-notes", status_code=201)
def create_world_note(book_id: int, request: WorldNoteCreate) -> dict[str, Any]:
    """人工创建世界信息，内容明确标为人工补充且可继续编辑。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            cursor = connection.execute(
                """
                INSERT INTO world_notes(
                    book_id, category, title, summary, confidence, first_segment, created_by, archived_at
                ) VALUES (?, ?, ?, ?, 1, 0, 'human', NULL)
                """,
                (book_id, request.category, request.title.strip(), request.summary.strip()),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="同一分类中已经存在同名世界信息。") from exc
        note_id = int(cursor.lastrowid)
        connection.executemany(
            """
            INSERT INTO record_versions(book_id, target_type, target_id, field_name, value, source, reason)
            VALUES (?, 'world_note', ?, ?, ?, 'human', '人工创建世界信息')
            """,
            [
                (book_id, note_id, "title", request.title.strip()),
                (book_id, note_id, "summary", request.summary.strip()),
                (book_id, note_id, "category", request.category),
            ],
        )
    return {
        "id": note_id,
        "book_id": book_id,
        "category": request.category,
        "title": request.title.strip(),
        "summary": request.summary.strip(),
        "confidence": 1.0,
        "first_segment": 0,
        "created_by": "human",
        "evidence_count": 0,
    }


@app.delete("/api/world-notes/{note_id}", status_code=204)
def archive_world_note(note_id: int) -> Response:
    """归档世界信息，保留恢复能力和版本历史。"""

    with transaction(settings.database_path) as connection:
        note = connection.execute("SELECT * FROM world_notes WHERE id = ?", (note_id,)).fetchone()
        if note is None:
            raise HTTPException(status_code=404, detail="找不到这条世界信息。")
        connection.execute("UPDATE world_notes SET archived_at = CURRENT_TIMESTAMP WHERE id = ?", (note_id,))
        connection.execute(
            """
            INSERT INTO record_versions(book_id, target_type, target_id, field_name, value, source, reason)
            VALUES (?, 'world_note', ?, 'archived_at', CURRENT_TIMESTAMP, 'human', '人工归档世界信息')
            """,
            (note["book_id"], note_id),
        )
    return Response(status_code=204)


@app.post("/api/world-notes/{note_id}/restore")
def restore_world_note(note_id: int) -> dict[str, Any]:
    """恢复已经归档的世界信息。"""

    with transaction(settings.database_path) as connection:
        note = connection.execute("SELECT * FROM world_notes WHERE id = ?", (note_id,)).fetchone()
        if note is None:
            raise HTTPException(status_code=404, detail="找不到这条世界信息。")
        connection.execute("UPDATE world_notes SET archived_at = NULL WHERE id = ?", (note_id,))
        connection.execute(
            """
            INSERT INTO record_versions(book_id, target_type, target_id, field_name, value, source, reason)
            VALUES (?, 'world_note', ?, 'archived_at', '', 'human', '恢复世界信息')
            """,
            (note["book_id"], note_id),
        )
    return {"id": note_id, "status": "active"}


@app.get("/api/books/{book_id}/world-notes/archived")
def list_archived_world_notes(book_id: int) -> list[dict[str, Any]]:
    """列出可恢复的世界信息。"""

    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        archived = connection.execute(
            """
            SELECT w.*, (SELECT COUNT(*) FROM evidence e
                WHERE e.target_type = 'world_note' AND e.target_id = w.id) AS evidence_count
            FROM world_notes w WHERE w.book_id = ? AND w.archived_at IS NOT NULL
            ORDER BY w.archived_at DESC, w.title
            """,
            (book_id,),
        ).fetchall()
    return rows(archived)


@app.patch("/api/connectivity-reviews/{review_id}")
def resolve_connectivity_review(review_id: int, patch: ConnectivityReviewPatch) -> dict[str, Any]:
    """人工裁定自动复审仍无法唯一判断的孤立节点。"""

    with transaction(settings.database_path) as connection:
        review = connection.execute(
            "SELECT * FROM entity_connectivity_reviews WHERE id = ?",
            (review_id,),
        ).fetchone()
        if review is None:
            raise HTTPException(status_code=404, detail="找不到这条关系复审记录。")
        connection.execute(
            """
            UPDATE entity_connectivity_reviews SET status = ?, reason = ?, confidence = 1,
                review_method = 'human', updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (patch.status, patch.reason.strip(), review_id),
        )
    return {"id": review_id, "status": patch.status, "reason": patch.reason.strip()}


@app.post("/api/connectivity-reviews/{review_id}/relation", status_code=201)
def create_connectivity_relation(review_id: int, request: ConnectivityLinkCreate) -> dict[str, Any]:
    """使用用户选择的原文片段和逐字引文补建关系。"""

    with transaction(settings.database_path) as connection:
        review = connection.execute(
            """
            SELECT review.*, entity.name AS source_name FROM entity_connectivity_reviews review
            JOIN entities entity ON entity.id = review.entity_id WHERE review.id = ?
            """,
            (review_id,),
        ).fetchone()
        if review is None:
            raise HTTPException(status_code=404, detail="找不到这条关系复审记录。")
        target = connection.execute(
            "SELECT * FROM entities WHERE id = ? AND book_id = ? AND kind IN ('person', 'faction')",
            (request.target_entity_id, review["book_id"]),
        ).fetchone()
        if target is None or int(target["id"]) == int(review["entity_id"]):
            raise HTTPException(status_code=422, detail="请选择同一本书中的另一个人物或势力。")
        segment = connection.execute(
            "SELECT * FROM segments WHERE id = ? AND book_id = ?",
            (request.segment_id, review["book_id"]),
        ).fetchone()
        if segment is None or find_quote(str(segment["text"]), request.evidence_quote) is None:
            raise HTTPException(status_code=422, detail="逐字引文不在所选原文片段中。")
        directionality, reverse_predicate = normalize_relation_semantics(
            request.predicate, request.directionality, request.reverse_predicate,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO claims(
                book_id, source_entity_id, target_entity_id, predicate, directionality,
                reverse_predicate, summary, confidence, status, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'accepted', ?, 'human')
            """,
            (
                review["book_id"], review["entity_id"], target["id"], request.predicate.strip(),
                directionality, reverse_predicate, request.summary.strip(), segment["ordinal"],
            ),
        )
        claim = connection.execute(
            """
            SELECT id FROM claims WHERE book_id = ? AND source_entity_id = ? AND target_entity_id = ?
              AND predicate = ? AND first_segment = ?
            """,
            (
                review["book_id"], review["entity_id"], target["id"],
                request.predicate.strip(), segment["ordinal"],
            ),
        ).fetchone()
        if claim is None:
            raise HTTPException(status_code=409, detail="关系写入失败。")
        add_evidence(
            connection, int(review["book_id"]), "claim", int(claim["id"]), int(segment["id"]),
            str(segment["text"]), request.evidence_quote,
        )
        connection.execute(
            """
            UPDATE entity_connectivity_reviews SET status = 'connected', candidate_count = candidate_count + 1,
                confidence = 1, reason = '用户使用逐字原文证据补建关系。', review_method = 'human',
                updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (review_id,),
        )
    return {"review_id": review_id, "claim_id": int(claim["id"]), "status": "connected"}


@app.patch("/api/event-location-reviews/{event_id}")
def resolve_event_location(event_id: int, patch: EventLocationReviewPatch) -> dict[str, Any]:
    """使用用户选择的地点和逐字原文解决未闭环的剧情位置。"""

    with transaction(settings.database_path) as connection:
        review = connection.execute(
            "SELECT * FROM event_location_reviews WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        if review is None:
            raise HTTPException(status_code=404, detail="找不到这条剧情位置复审记录。")
        location = connection.execute(
            "SELECT * FROM entities WHERE id = ? AND book_id = ? AND kind = 'place'",
            (patch.location_entity_id, review["book_id"]),
        ).fetchone()
        if location is None:
            raise HTTPException(status_code=422, detail="请选择同一本书中的地点。")
        segment = connection.execute(
            "SELECT * FROM segments WHERE id = ? AND book_id = ?",
            (patch.segment_id, review["book_id"]),
        ).fetchone()
        if segment is None or find_quote(str(segment["text"]), patch.evidence_quote) is None:
            raise HTTPException(status_code=422, detail="逐字引文不在所选原文章节中。")
        connection.execute(
            "UPDATE events SET location_entity_id = ? WHERE id = ? AND book_id = ?",
            (location["id"], event_id, review["book_id"]),
        )
        connection.execute(
            """
            UPDATE event_location_reviews SET status = 'explicit', effective_location_entity_id = ?,
                reason = '用户使用逐字原文证据确认剧情地点。', updated_at = CURRENT_TIMESTAMP
            WHERE event_id = ?
            """,
            (location["id"], event_id),
        )
        add_evidence(
            connection, int(review["book_id"]), "event", event_id, int(segment["id"]),
            str(segment["text"]), patch.evidence_quote,
        )
    return {"event_id": event_id, "status": "explicit", "location_entity_id": int(location["id"])}


@app.post("/api/books/{book_id}/quality/retry")
async def retry_quality_harness(book_id: int) -> dict[str, Any]:
    """只重跑未闭环的质量专项，不重新分析已经通过的章节。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        job = connection.execute(
            """
            SELECT * FROM analysis_jobs WHERE book_id = ?
            ORDER BY CASE WHEN status = 'needs_review' THEN 0 ELSE 1 END, id DESC LIMIT 1
            """,
            (book_id,),
        ).fetchone()
        if job is None:
            raise HTTPException(status_code=409, detail="这本书还没有可复用的分析任务。")
        conflict_resolution = conservatively_close_conflicts(connection, book_id)
        connection.execute(
            """
            UPDATE analysis_jobs SET status = 'quality_checking', quality_gate_status = 'running',
                error = '正在重试未闭环的关系和地点质量检查', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (job["id"],),
        )
    try:
        provider = create_provider(settings, str(job["provider"]), book_id)
        summary = await run_quality_harness(settings, provider, book_id, int(job["id"]))
        with connect(settings.database_path) as connection:
            ambiguous_count = int(connection.execute(
                "SELECT COUNT(*) FROM entity_connectivity_reviews WHERE book_id = ? AND status = 'ambiguous'",
                (book_id,),
            ).fetchone()[0])
        if (
            ambiguous_count
            and provider.name == "deepseek"
            and provider.model == "deepseek-v4-flash"
            and not summary.get("stopped_for_budget")
        ):
            refresh_job_metrics(settings, int(job["id"]))
            strong_provider = create_provider(
                replace(settings, deepseek_model="deepseek-v4-pro"), "deepseek", book_id
            )
            strong_summary = await run_quality_harness(
                settings, strong_provider, book_id, int(job["id"]), include_ambiguous=True,
            )
            summary["strong_model_calls"] = strong_summary.get("calls", 0)
            summary["stopped_for_budget"] = strong_summary.get("stopped_for_budget", False)
    except ProviderError as exc:
        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'needs_review', quality_gate_status = 'needs_review',
                    error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (str(exc)[:600], job["id"]),
            )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # 任何意外都必须回到可重试状态，任务不能永久停在“质量检查中”。
        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'needs_review', quality_gate_status = 'needs_review',
                    error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (f"质量复审意外中断：{str(exc)[:500]}", job["id"]),
            )
        raise HTTPException(status_code=500, detail="质量复审已安全停止，可以直接重试或人工解决剩余项目。") from exc
    try:
        refresh_job_metrics(settings, int(job["id"]))
        with transaction(settings.database_path) as connection:
            book = ensure_book(connection, book_id)
            report = build_quality_report(connection, book_id, max(0, int(book["segment_count"]) - 1))
            passed = bool(report.get("quality_gate_passed")) and not summary.get("stopped_for_budget")
            snapshot = connection.execute(
                "INSERT INTO quality_gate_snapshots(book_id, job_id, status, report_json) VALUES (?, ?, ?, ?)",
                (book_id, job["id"], "passed" if passed else "needs_review", json.dumps(report, ensure_ascii=False)),
            )
            connection.execute(
                """
                UPDATE analysis_jobs SET status = ?, quality_gate_status = ?, quality_gate_snapshot_id = ?,
                    error = ?, completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (
                    "completed" if passed else "needs_review",
                    "passed" if passed else "needs_review", int(snapshot.lastrowid),
                    "质量门禁已经通过" if passed else "仍有项目需要自动重试或人工解决",
                    job["id"],
                ),
            )
    except Exception as exc:
        # 最终快照也属于任务的一部分；即使碰到数据库竞争，也必须回到可重试状态。
        with transaction(settings.database_path) as connection:
            connection.execute(
                """
                UPDATE analysis_jobs SET status = 'needs_review', quality_gate_status = 'needs_review',
                    error = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
                """,
                (f"质量结果保存中断：{str(exc)[:500]}", job["id"]),
            )
        raise HTTPException(status_code=500, detail="质量结果已保存到复核缓存，可以直接继续，不会重复处理已完成项目。") from exc
    return {
        "job_id": int(job["id"]),
        "status": "completed" if passed else "needs_review",
        "summary": summary,
        "conflict_resolution": conflict_resolution,
        "quality": report,
    }


@app.post("/api/records/{target_type}/{target_id}/drafts", status_code=201)
async def create_record_draft(
    target_type: str,
    target_id: int,
    request: RecordDraftRequest,
) -> dict[str, Any]:
    """按陈述式任务生成证据受限草稿，正式记录保持不变。"""

    table_map = {
        "world_note": ("world_notes", "title"),
        "entry": ("entries", "name"),
    }
    if target_type not in table_map:
        raise HTTPException(status_code=422, detail="只有世界信息和数据库条目支持二次生成。")
    instruction = request.instruction.strip()
    if len(instruction) < 6:
        raise HTTPException(status_code=422, detail="整理任务至少写六个字，并明确要修改什么。")
    if any(mark in instruction for mark in ("?", "？")):
        raise HTTPException(status_code=422, detail="请使用陈述句说明修改任务，不要提交问句。")
    if not any(
        verb in instruction
        for verb in ("补充", "改写", "整理", "说明", "突出", "合并", "修正", "扩写", "精简", "生成")
    ):
        raise HTTPException(status_code=422, detail="陈述句需要明确写出补充、改写、整理或修正等任务。")
    table, title_field = table_map[target_type]
    with connect(settings.database_path) as connection:
        record = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (target_id,)).fetchone()  # noqa: S608
        if record is None:
            raise HTTPException(status_code=404, detail="找不到待生成草稿的记录。")
        evidence_rows = connection.execute(
            """
            SELECT x.quote, s.chapter_title, s.ordinal FROM evidence x
            JOIN segments s ON s.id = x.segment_id
            WHERE x.target_type = ? AND x.target_id = ?
            ORDER BY s.ordinal, x.quote_start LIMIT 30
            """,
            (target_type, target_id),
        ).fetchall()
        regeneration_prompt = render_prompt_bundle(connection, int(record["book_id"]), "record_regeneration") if record is not None else None
    if not evidence_rows:
        raise HTTPException(status_code=422, detail="这条记录没有逐字原文证据，不能进行二次生成。")
    verified_quotes = [str(item["quote"]) for item in evidence_rows]
    attributes = str(record["attributes_json"]) if target_type == "entry" else "{}"
    record_context = (
        f"记录类别：{target_type}\n"
        f"现有标题：{record[title_field]}\n"
        f"现有分类：{record['category']}\n"
        f"现有说明：{record['summary']}\n"
        f"现有属性：{attributes}\n"
        f"用户陈述式任务：{instruction}\n\n"
        "<VERIFIED_QUOTES>\n- " + "\n- ".join(verified_quotes) + "\n</VERIFIED_QUOTES>"
    )
    try:
        provider = create_provider(settings, request.provider, int(record["book_id"]))
    except ProviderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    pricing = pricing_for(provider.name, provider.model)
    if provider.name != "mock" and not pricing.available:
        raise HTTPException(status_code=422, detail="当前供应商没有可复算价格，无法执行金额硬限制。")
    estimated_input, estimated_output = estimate_segment_tokens(record_context, 0)
    estimated_before = calculate_cost_usd(0, estimated_input, estimated_output, pricing)
    if estimated_before is not None and estimated_before > request.max_cost_usd:
        raise HTTPException(
            status_code=422,
            detail=f"草稿调用的保守预估为 {estimated_before:.6f} 美元，超过本次上限。",
        )
    call_hash = request_hash(
        provider.name, provider.model, regeneration_prompt.prompt_hash, str(record[title_field]),
        int(target_id), record_context, "",
    )
    with transaction(settings.database_path) as connection:
        manifest_id = create_run_manifest(
            connection,
            book_id=int(record["book_id"]),
            job_id=None,
            run_kind="record_regeneration",
            provider=provider.name,
            model=provider.model,
            auth_mode=provider.auth_mode,
            prompt=regeneration_prompt,
            input_scope={"target_type": target_type, "target_id": target_id},
            input_hash=stable_hash(call_hash, *verified_quotes),
        )
    started = time.monotonic()
    try:
        response = await provider.regenerate_record(record_context)
    except ProviderError as exc:
        with transaction(settings.database_path) as connection:
            complete_run_manifest(
                connection, manifest_id, status="failed",
                duration_ms=round((time.monotonic() - started) * 1_000),
                validation={"error": str(exc)[:500]},
            )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        with transaction(settings.database_path) as connection:
            complete_run_manifest(
                connection, manifest_id, status="failed",
                duration_ms=round((time.monotonic() - started) * 1_000),
                validation={"error": str(exc)[:500]},
            )
        raise HTTPException(status_code=500, detail="候选版本生成已安全停止，可以直接重试。") from exc
    selected_quotes = list(response.result.evidence_quotes)
    if any(quote not in verified_quotes for quote in selected_quotes):
        with transaction(settings.database_path) as connection:
            complete_run_manifest(
                connection, manifest_id, status="rejected",
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                duration_ms=round((time.monotonic() - started) * 1_000),
                validation={"quote_integrity_percent": 0, "reason": "引用不在已核验证据中"},
            )
        raise HTTPException(status_code=422, detail="模型草稿引用了未提供的证据，草稿已拒绝。")
    actual_cost = calculate_cost_usd(
        response.cache_hit_input_tokens,
        response.cache_miss_input_tokens,
        response.output_tokens,
        pricing,
    )
    if actual_cost is not None and actual_cost > request.max_cost_usd:
        with transaction(settings.database_path) as connection:
            complete_run_manifest(
                connection, manifest_id, status="budget_rejected",
                input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                estimated_cost_usd=actual_cost,
                duration_ms=round((time.monotonic() - started) * 1_000),
                validation={"quote_integrity_percent": 100, "budget_limit_usd": request.max_cost_usd},
            )
        raise HTTPException(status_code=422, detail="本次模型实际用量超过草稿上限，结果未保存。")
    with transaction(settings.database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO generation_drafts(
                book_id, target_type, target_id, instruction, provider, model,
                title_value, summary_value, category_value, attributes_json,
                evidence_json, input_tokens, output_tokens, estimated_cost_usd
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["book_id"], target_type, target_id, instruction, provider.name, provider.model,
                response.result.title, response.result.summary, response.result.category,
                json.dumps(response.result.attributes, ensure_ascii=False),
                json.dumps(selected_quotes, ensure_ascii=False), response.input_tokens,
                response.output_tokens, actual_cost,
            ),
        )
        draft_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO model_call_ledger(
                book_id, purpose, provider, model, prompt_version, request_hash, status,
                input_tokens, output_tokens, cache_hit_input_tokens,
                cache_miss_input_tokens, estimated_cost_usd, run_manifest_id, prompt_hash,
                duration_ms, auth_mode
            ) VALUES (?, 'record_regeneration', ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["book_id"], provider.name, provider.model,
                provider.prompt_version("record_regeneration", "record-regeneration-v2", regeneration_prompt.system_prompt),
                call_hash,
                response.input_tokens, response.output_tokens, response.cache_hit_input_tokens,
                response.cache_miss_input_tokens, actual_cost, manifest_id,
                regeneration_prompt.prompt_hash, round((time.monotonic() - started) * 1_000),
                provider.auth_mode,
            ),
        )
        complete_run_manifest(
            connection, manifest_id, status="completed",
            input_tokens=response.input_tokens, output_tokens=response.output_tokens,
            estimated_cost_usd=actual_cost,
            duration_ms=round((time.monotonic() - started) * 1_000),
            validation={"quote_integrity_percent": 100, "selected_quote_count": len(selected_quotes)},
        )
    return {
        "id": draft_id,
        "status": "draft",
        "title": response.result.title,
        "summary": response.result.summary,
        "category": response.result.category,
        "attributes": response.result.attributes,
        "evidence_quotes": selected_quotes,
        "estimated_cost_usd": actual_cost,
        "run_manifest_id": manifest_id,
    }


@app.post("/api/record-drafts/{draft_id}/apply")
def apply_record_draft(draft_id: int) -> dict[str, Any]:
    """确认并应用候选版本，同时保存每个字段的旧值和新值。"""

    with transaction(settings.database_path) as connection:
        draft = connection.execute(
            "SELECT * FROM generation_drafts WHERE id = ?",
            (draft_id,),
        ).fetchone()
        if draft is None:
            raise HTTPException(status_code=404, detail="找不到这份草稿。")
        if draft["status"] != "draft":
            raise HTTPException(status_code=409, detail="这份草稿已经处理。")
        if draft["target_type"] == "world_note":
            table = "world_notes"
            title_field = "title"
            fields = {
                "title": str(draft["title_value"]),
                "summary": str(draft["summary_value"]),
                "category": str(draft["category_value"]),
            }
        else:
            table = "entries"
            title_field = "name"
            fields = {
                "name": str(draft["title_value"]),
                "summary": str(draft["summary_value"]),
                "category": str(draft["category_value"]),
                "attributes_json": str(draft["attributes_json"]),
            }
        record = connection.execute(
            f"SELECT * FROM {table} WHERE id = ?",  # noqa: S608
            (draft["target_id"],),
        ).fetchone()
        if record is None:
            raise HTTPException(status_code=404, detail="草稿对应的正式记录已经不存在。")
        for field_name, value in fields.items():
            connection.execute(
                """
                INSERT INTO record_versions(book_id, target_type, target_id, field_name, value, source, reason)
                VALUES (?, ?, ?, ?, ?, 'before_draft', ?)
                """,
                (
                    draft["book_id"], draft["target_type"], draft["target_id"], field_name,
                    str(record[field_name]), f"应用草稿 {draft_id} 前的版本",
                ),
            )
            connection.execute(
                """
                INSERT INTO record_versions(book_id, target_type, target_id, field_name, value, source, reason)
                VALUES (?, ?, ?, ?, ?, 'draft', ?)
                """,
                (
                    draft["book_id"], draft["target_type"], draft["target_id"], field_name,
                    value, f"应用草稿 {draft_id}",
                ),
            )
        assignments = ", ".join(f"{field} = ?" for field in fields)
        connection.execute(
            f"UPDATE {table} SET {assignments}, created_by = 'human' WHERE id = ?",  # noqa: S608
            (*fields.values(), draft["target_id"]),
        )
        connection.execute(
            "UPDATE generation_drafts SET status = 'applied', applied_at = CURRENT_TIMESTAMP WHERE id = ?",
            (draft_id,),
        )
    return {
        "draft_id": draft_id,
        "status": "applied",
        "target_type": draft["target_type"],
        "target_id": draft["target_id"],
        "title_field": title_field,
    }


@app.get("/api/books/{book_id}/search")
def search_book(
    book_id: int,
    q: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=60, ge=1, le=200),
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """同时搜索结构知识和原文片段。"""

    pattern = f"%{q}%"
    results: list[dict[str, Any]] = []
    with connect(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        visible = window.through_segment
        for target_type, table, title_expression, searchable in (
            ("entity", "entities", "name", "name || ' ' || summary"),
            ("event", "events", "title", "title || ' ' || summary || ' ' || temporal_value"),
            ("world_note", "world_notes", "title", "title || ' ' || summary"),
            ("entry", "entries", "name", "name || ' ' || summary || ' ' || attributes_json"),
        ):
            remaining = limit - len(results)
            if remaining <= 0:
                break
            active_filter = " AND archived_at IS NULL" if table == "world_notes" else ""
            records = connection.execute(
                f"""
                SELECT id, {title_expression} AS title, summary, first_segment
                FROM {table} WHERE book_id = ? AND first_segment <= ? AND first_segment >= ?{active_filter}
                  AND {searchable} LIKE ? LIMIT ?
                """,  # noqa: S608
                (book_id, visible, window.from_segment, pattern, remaining),
            ).fetchall()
            results.extend(
                {
                    "target_type": target_type,
                    "target_id": int(record["id"]),
                    "title": record["title"],
                    "snippet": record["summary"],
                    "first_segment": int(record["first_segment"]),
                }
                for record in records
            )
        remaining = limit - len(results)
        if remaining > 0:
            segments = connection.execute(
                """
                SELECT id, ordinal, chapter_title, text FROM segments
                WHERE book_id = ? AND ordinal <= ? AND ordinal >= ? AND text LIKE ? ORDER BY ordinal LIMIT ?
                """,
                (book_id, visible, window.from_segment, pattern, remaining),
            ).fetchall()
            for item in segments:
                position = str(item["text"]).find(q)
                start = max(0, position - 50)
                end = min(len(item["text"]), position + len(q) + 90)
                results.append(
                    {
                        "target_type": "segment",
                        "target_id": int(item["id"]),
                        "title": item["chapter_title"],
                        "snippet": str(item["text"])[start:end],
                        "first_segment": int(item["ordinal"]),
                    }
                )
    return results


@app.get("/api/books/{book_id}/export")
def export_book(book_id: int, include_text: bool = False) -> Response:
    """导出一本书的结构知识、证据和可选原文。"""

    with connect(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        data: dict[str, Any] = {"format": "novel-atlas-v1", "book": dict(book)}
        for table in (
            "entities", "claims", "events", "world_notes", "entries", "analysis_jobs", "corrections",
            "identity_clusters", "identity_decisions", "journey_legs", "record_versions",
            "model_call_ledger", "quality_benchmark_cases",
        ):
            data[table] = rows(connection.execute(f"SELECT * FROM {table} WHERE book_id = ?", (book_id,)).fetchall())  # noqa: S608
        entity_ids = [item["id"] for item in data["entities"]]
        event_ids = [item["id"] for item in data["events"]]
        data["aliases"] = rows(
            connection.execute(
                "SELECT * FROM aliases WHERE entity_id IN (SELECT id FROM entities WHERE book_id = ?)",
                (book_id,),
            ).fetchall()
        )
        data["event_participants"] = rows(
            connection.execute(
                "SELECT * FROM event_participants WHERE event_id IN (SELECT id FROM events WHERE book_id = ?)",
                (book_id,),
            ).fetchall()
        )
        data["evidence"] = rows(connection.execute("SELECT * FROM evidence WHERE book_id = ?", (book_id,)).fetchall())
        segment_columns = "*" if include_text else "id, book_id, ordinal, chapter_title, anchor, char_start, char_end"
        data["segments"] = rows(
            connection.execute(
                f"SELECT {segment_columns} FROM segments WHERE book_id = ? ORDER BY ordinal",  # noqa: S608
                (book_id,),
            ).fetchall()
        )
        data["export_counts"] = {
            "entities": len(entity_ids),
            "events": len(event_ids),
            "evidence": len(data["evidence"]),
            "segments": len(data["segments"]),
        }
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return Response(
        content=content,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="novel-atlas-book-{book_id}.json"'},
    )


@app.get("/api/backup")
def backup_database() -> FileResponse:
    """使用 SQLite 在线备份生成一致副本，下载结束后清理临时文件。"""

    handle, temporary_path = tempfile.mkstemp(prefix="novel-atlas-backup-", suffix=".db")
    os.close(handle)
    source = connect(settings.database_path)
    destination = sqlite3.connect(temporary_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return FileResponse(
        temporary_path,
        filename="novel-atlas-backup.db",
        media_type="application/vnd.sqlite3",
        background=BackgroundTask(os.unlink, temporary_path),
    )


@app.get("/api/books/{book_id}/map-layout")
def map_layout(
    book_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
    detail_level: str = Query(default="high", pattern="^(low|medium|high)$"),
    focus: str = Query(default="", max_length=120),
) -> dict[str, Any]:
    """返回基于证据约束和稳定拓扑的 2D/3D 共用布局。"""

    with derived_view_lock:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            try:
                window = resolve_reading_window(
                    connection, book_id,
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            payload = build_map_layout_snapshot(
                connection, book_id, window.through_segment, window.from_segment,
            )
            payload["reading_window"] = window.payload()
            payload["requested_detail_level"] = detail_level
            payload["requested_focus"] = focus
            return payload


@app.get("/api/books/{book_id}/narrative-memory")
def narrative_memory(
    book_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """返回最近场景、人物状态、未闭合线索、故事弧和因果边。"""

    with derived_view_lock:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            try:
                window = resolve_reading_window(
                    connection, book_id,
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            payload = narrative_memory_payload(
                connection, book_id, window.through_segment, window.from_segment,
            )
            payload["reading_window"] = window.payload()
            return payload


@app.get("/api/books/{book_id}/concepts")
def concepts(
    book_id: int,
    q: str = Query(default="", max_length=120),
    category: str = Query(default="", max_length=80),
    status: str = Query(default="active", max_length=40),
    limit: int = Query(default=200, ge=1, le=1_000),
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """按名称、别名、说明、分类和状态检索知识概念。"""

    with derived_view_lock:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            try:
                window = resolve_reading_window(
                    connection, book_id,
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return concept_payload(
                connection, book_id, query=q, category=category, status=status,
                limit=limit, from_segment=window.from_segment,
                through_segment=window.through_segment,
            )


@app.post("/api/books/{book_id}/concepts")
def create_concept(book_id: int, request: ConceptCreate) -> dict[str, Any]:
    """创建书内概念或自定义分类，并可挂到现有上位概念。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        if request.parent_concept_id is not None:
            parent = connection.execute(
                "SELECT id FROM concepts WHERE id = ? AND book_id = ? AND status != 'archived'",
                (request.parent_concept_id, book_id),
            ).fetchone()
            if parent is None:
                raise HTTPException(status_code=422, detail="上位概念不属于当前书籍或已经归档。")
        try:
            cursor = connection.execute(
                """
                INSERT INTO concepts(
                    book_id, scheme, category, preferred_label, description,
                    aliases_json, custom, status, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, 1, 'active', 'human')
                """,
                (
                    book_id, request.scheme, request.category, request.preferred_label,
                    request.description, json.dumps(request.aliases, ensure_ascii=False),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="同一分类中已经存在同名概念。") from exc
        concept_id = int(cursor.lastrowid)
        if request.parent_concept_id is not None:
            connection.execute(
                """
                INSERT INTO concept_relations(
                    book_id, source_concept_id, target_concept_id, relation, created_by
                ) VALUES (?, ?, ?, 'broader', 'human')
                """,
                (book_id, concept_id, request.parent_concept_id),
            )
        created = next(
            item for item in concept_payload(connection, book_id, status="", limit=1_000)
            if int(item["id"]) == concept_id
        )
        record_revision(connection, book_id, "concept", concept_id, "created", {}, created)
        return created


@app.patch("/api/concepts/{concept_id}")
def patch_concept(concept_id: int, patch: ConceptPatch) -> dict[str, Any]:
    """修改概念并保留其事实、证据和关联记录。"""

    with transaction(settings.database_path) as connection:
        concept = connection.execute("SELECT * FROM concepts WHERE id = ?", (concept_id,)).fetchone()
        if concept is None:
            raise HTTPException(status_code=404, detail="找不到知识概念。")
        updates = {
            "category": patch.category,
            "preferred_label": patch.preferred_label,
            "description": patch.description,
            "status": patch.status,
        }
        assignments: list[str] = []
        values: list[Any] = []
        for field, value in updates.items():
            if value is not None:
                assignments.append(f"{field} = ?")
                values.append(value)
        if patch.aliases is not None:
            assignments.append("aliases_json = ?")
            values.append(json.dumps(patch.aliases, ensure_ascii=False))
        if assignments:
            try:
                connection.execute(
                    f"UPDATE concepts SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # noqa: S608
                    (*values, concept_id),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(status_code=409, detail="修改后会与现有概念重名。") from exc
        if patch.move_to_root or patch.parent_concept_id is not None:
            connection.execute(
                "DELETE FROM concept_relations WHERE source_concept_id = ? AND relation = 'broader'",
                (concept_id,),
            )
        if patch.parent_concept_id is not None:
            parent = connection.execute(
                "SELECT id FROM concepts WHERE id = ? AND book_id = ? AND id != ? AND status != 'archived'",
                (patch.parent_concept_id, concept["book_id"], concept_id),
            ).fetchone()
            if parent is None:
                raise HTTPException(status_code=422, detail="上位概念无效。")
            connection.execute(
                """
                INSERT INTO concept_relations(
                    book_id, source_concept_id, target_concept_id, relation, created_by
                ) VALUES (?, ?, ?, 'broader', 'human')
                """,
                (concept["book_id"], concept_id, patch.parent_concept_id),
            )
        updated = next(
            item for item in concept_payload(connection, int(concept["book_id"]), status="", limit=1_000)
            if int(item["id"]) == concept_id
        )
        record_revision(
            connection, int(concept["book_id"]), "concept", concept_id,
            "updated", dict(concept), updated,
        )
        return updated


@app.delete("/api/concepts/{concept_id}")
def archive_concept(concept_id: int) -> dict[str, Any]:
    """归档概念而不删除事实、证据和修改历史。"""

    with transaction(settings.database_path) as connection:
        concept = connection.execute("SELECT * FROM concepts WHERE id = ?", (concept_id,)).fetchone()
        if concept is None:
            raise HTTPException(status_code=404, detail="找不到知识概念。")
        connection.execute(
            "UPDATE concepts SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (concept_id,),
        )
        record_revision(
            connection, int(concept["book_id"]), "concept", concept_id,
            "archived", dict(concept), {**dict(concept), "status": "archived"},
        )
    return {"id": concept_id, "status": "archived"}


@app.get("/api/books/{book_id}/knowledge-claims")
def knowledge_claims(
    book_id: int,
    concept_id: int | None = Query(default=None, gt=0),
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """返回知识概念下的原子事实、限定条件和证据数量。"""

    with derived_view_lock:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            try:
                window = resolve_reading_window(
                    connection, book_id,
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return knowledge_claim_payload(
                connection, book_id, concept_id,
                from_segment=window.from_segment, through_segment=window.through_segment,
            )


@app.post("/api/books/{book_id}/knowledge-claims")
def create_knowledge_claim(book_id: int, request: KnowledgeClaimCreate) -> dict[str, Any]:
    """创建原子事实；原文事实必须先通过逐字引文验证。"""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        concept = connection.execute(
            "SELECT id FROM concepts WHERE id = ? AND book_id = ? AND status != 'archived'",
            (request.concept_id, book_id),
        ).fetchone()
        if concept is None:
            raise HTTPException(status_code=422, detail="概念不属于当前书籍或已经归档。")
        segment = None
        if request.source_kind == "original_text":
            if request.segment_id is None or not request.evidence_quote:
                raise HTTPException(status_code=422, detail="原文事实必须提供章节和逐字引文。")
            segment = connection.execute(
                "SELECT * FROM segments WHERE id = ? AND book_id = ?",
                (request.segment_id, book_id),
            ).fetchone()
            if segment is None or find_quote(str(segment["text"]), request.evidence_quote) is None:
                raise HTTPException(status_code=422, detail="引文无法在当前书籍的指定章节逐字找到。")
        try:
            cursor = connection.execute(
                """
                INSERT INTO knowledge_claims(
                    book_id, concept_id, subject_type, subject_id, predicate, value_json,
                    status, confidence, source_kind, created_by
                ) VALUES (?, ?, 'concept', ?, ?, ?, 'accepted', ?, ?, 'human')
                """,
                (
                    book_id, request.concept_id, request.concept_id, request.predicate,
                    json.dumps(request.value, ensure_ascii=False), request.confidence, request.source_kind,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="完全相同的事实已经存在。") from exc
        claim_id = int(cursor.lastrowid)
        for key, value in request.qualifiers.items():
            connection.execute(
                "INSERT INTO claim_qualifiers(knowledge_claim_id, qualifier_key, qualifier_value_json) VALUES (?, ?, ?)",
                (claim_id, str(key), json.dumps(value, ensure_ascii=False)),
            )
        if segment is not None and request.segment_id is not None:
            add_evidence(
                connection, book_id, "knowledge_claim", claim_id, request.segment_id,
                str(segment["text"]), request.evidence_quote,
            )
            evidence = connection.execute(
                """
                SELECT id FROM evidence
                WHERE target_type = 'knowledge_claim' AND target_id = ? AND segment_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (claim_id, request.segment_id),
            ).fetchone()
            if evidence is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO knowledge_claim_evidence(knowledge_claim_id, evidence_id) VALUES (?, ?)",
                    (claim_id, int(evidence["id"])),
                )
        created = next(item for item in knowledge_claim_payload(connection, book_id, request.concept_id) if int(item["id"]) == claim_id)
        record_revision(connection, book_id, "knowledge_claim", claim_id, "created", {}, created)
        return created


@app.patch("/api/knowledge-claims/{claim_id}")
def patch_knowledge_claim(claim_id: int, patch: KnowledgeClaimPatch) -> dict[str, Any]:
    """修改事实值、状态、限定条件或置信度。"""

    with transaction(settings.database_path) as connection:
        claim = connection.execute("SELECT * FROM knowledge_claims WHERE id = ?", (claim_id,)).fetchone()
        if claim is None:
            raise HTTPException(status_code=404, detail="找不到知识事实。")
        assignments: list[str] = []
        values: list[Any] = []
        if "value" in patch.model_fields_set:
            assignments.append("value_json = ?")
            values.append(json.dumps(patch.value, ensure_ascii=False))
        if patch.status is not None:
            assignments.append("status = ?")
            values.append(patch.status)
        if patch.confidence is not None:
            assignments.append("confidence = ?")
            values.append(patch.confidence)
        if assignments:
            connection.execute(
                f"UPDATE knowledge_claims SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # noqa: S608
                (*values, claim_id),
            )
        if patch.qualifiers is not None:
            connection.execute("DELETE FROM claim_qualifiers WHERE knowledge_claim_id = ?", (claim_id,))
            for key, value in patch.qualifiers.items():
                connection.execute(
                    "INSERT INTO claim_qualifiers(knowledge_claim_id, qualifier_key, qualifier_value_json) VALUES (?, ?, ?)",
                    (claim_id, str(key), json.dumps(value, ensure_ascii=False)),
                )
        updated = next(item for item in knowledge_claim_payload(connection, int(claim["book_id"]), int(claim["concept_id"])) if int(item["id"]) == claim_id)
        record_revision(
            connection, int(claim["book_id"]), "knowledge_claim", claim_id,
            "updated", dict(claim), updated,
        )
        return updated


@app.delete("/api/knowledge-claims/{claim_id}")
def deprecate_knowledge_claim(claim_id: int) -> dict[str, Any]:
    """弃用事实但保留证据与历史，供冲突和版本追踪。"""

    with transaction(settings.database_path) as connection:
        claim = connection.execute("SELECT * FROM knowledge_claims WHERE id = ?", (claim_id,)).fetchone()
        if claim is None:
            raise HTTPException(status_code=404, detail="找不到知识事实。")
        connection.execute(
            "UPDATE knowledge_claims SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (claim_id,),
        )
        record_revision(
            connection, int(claim["book_id"]), "knowledge_claim", claim_id,
            "deprecated", dict(claim), {**dict(claim), "status": "deprecated"},
        )
    return {"id": claim_id, "status": "deprecated"}


@app.get("/api/books/{book_id}/knowledge-facets")
def knowledge_facets(
    book_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """返回知识库分类、状态和证据覆盖统计。"""

    with derived_view_lock:
        with transaction(settings.database_path) as connection:
            ensure_book(connection, book_id)
            try:
                window = resolve_reading_window(
                    connection, book_id,
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            payload = facet_payload(
                connection, book_id,
                from_segment=window.from_segment, through_segment=window.through_segment,
            )
            payload["reading_window"] = window.payload()
            return payload


@app.get("/api/books/{book_id}/knowledge-revisions")
def knowledge_revisions(
    book_id: int,
    target_type: str = Query(default="", max_length=40),
    target_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """返回概念和原子事实的人工修改记录。"""

    if target_type and target_type not in {"concept", "knowledge_claim"}:
        raise HTTPException(status_code=422, detail="不支持的知识修改记录类型。")
    with connect(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return revision_payload(
            connection, book_id, target_type, target_id, limit,
            from_segment=window.from_segment,
            through_segment=window.through_segment,
        )


@app.get("/api/evidence/{target_type}/{target_id}")
def target_evidence(
    target_type: str,
    target_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """返回事实的逐字引文、章节和稳定锚点。"""

    allowed = {
        "entity", "claim", "place_relation", "event", "journey_leg",
        "world_note", "entry", "narrative_frame", "knowledge_claim",
    }
    if target_type not in allowed:
        raise HTTPException(status_code=400, detail="未知证据类型。")
    with connect(settings.database_path) as connection:
        book_row = connection.execute(
            "SELECT book_id FROM evidence WHERE target_type = ? AND target_id = ? LIMIT 1",
            (target_type, target_id),
        ).fetchone()
        window: ReadingWindow | None = None
        if book_row is not None:
            try:
                window = resolve_reading_window(
                    connection, int(book_row["book_id"]),
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = connection.execute(
            """
            SELECT x.*, s.ordinal, s.chapter_title, s.anchor, s.char_start, s.char_end
            FROM evidence x JOIN segments s ON s.id = x.segment_id
            WHERE x.target_type = ? AND x.target_id = ? ORDER BY s.ordinal, x.quote_start
            """,
            (target_type, target_id),
        ).fetchall()
        payload = rows(result)
        if window is not None:
            payload = [
                item for item in payload
                if int(item.get("ordinal", 0)) <= window.through_segment
            ]
            for item in payload:
                item["context_only"] = int(item.get("ordinal", 0)) < window.from_segment
        for item in payload:
            manifest_id = item.get("run_manifest_id")
            model_call_id = item.get("model_call_id")
            if manifest_id is None or model_call_id is None:
                segment_result = connection.execute(
                    """
                    SELECT run_manifest_id, model_call_id FROM segment_results
                    WHERE segment_id = ? ORDER BY completed_at DESC, id DESC LIMIT 1
                    """,
                    (item["segment_id"],),
                ).fetchone()
                if segment_result is not None:
                    manifest_id = manifest_id or segment_result["run_manifest_id"]
                    model_call_id = model_call_id or segment_result["model_call_id"]
            manifest = connection.execute(
                """
                SELECT id, run_kind, provider, model, auth_mode, contract_version,
                    prompt_hash, domain_rule_hash, external_fact_hash, schema_version,
                    eval_suite_version, status, input_tokens, output_tokens,
                    estimated_cost_usd, started_at, completed_at
                FROM run_manifests WHERE id = ?
                """,
                (manifest_id,),
            ).fetchone() if manifest_id is not None else None
            call = connection.execute(
                """
                SELECT id, purpose, provider, model, prompt_version, prompt_hash, status,
                    cache_hit, input_tokens, output_tokens, estimated_cost_usd,
                    duration_ms, auth_mode, created_at
                FROM model_call_ledger WHERE id = ?
                """,
                (model_call_id,),
            ).fetchone() if model_call_id is not None else None
            item["lineage"] = {
                "manifest": dict(manifest) if manifest is not None else None,
                "model_call": dict(call) if call is not None else None,
                "trace_status": "complete" if manifest is not None and call is not None else "legacy_or_local",
            }
    return payload


@app.get("/api/segments/{segment_id}")
def segment(
    segment_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """读取完整原文片段，供证据抽屉跳转。"""

    with connect(settings.database_path) as connection:
        item = connection.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
    if item is None:
        raise HTTPException(status_code=404, detail="找不到原文片段。")
    window: ReadingWindow | None = None
    if through_segment is not None or from_segment is not None:
        with connect(settings.database_path) as connection:
            try:
                window = resolve_reading_window(
                    connection, int(item["book_id"]),
                    from_segment=from_segment,
                    through_segment=through_segment,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        if int(item["ordinal"]) > window.through_segment:
            raise HTTPException(status_code=404, detail="该原文片段超出当前阅读范围。")
    payload = dict(item)
    if window is not None:
        payload["context_only"] = int(item["ordinal"]) < window.from_segment
        payload["reading_window"] = window.payload()
    return payload


@app.patch("/api/claims/{claim_id}")
def patch_claim(claim_id: int, patch: ClaimPatch) -> dict[str, Any]:
    """保留关系事实的人工审核历史。"""

    with transaction(settings.database_path) as connection:
        claim = connection.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
        if claim is None:
            raise HTTPException(status_code=404, detail="找不到关系事实。")
        new_summary = patch.summary or claim["summary"]
        requested_direction = patch.directionality or str(claim["directionality"])
        requested_reverse = patch.reverse_predicate if patch.reverse_predicate is not None else claim["reverse_predicate"]
        new_direction, new_reverse = normalize_relation_semantics(
            str(claim["predicate"]), requested_direction, requested_reverse,
        )
        if new_summary != claim["summary"]:
            connection.execute(
                """
                INSERT INTO corrections(book_id, target_type, target_id, field_name, old_value, new_value, reason)
                VALUES (?, 'claim', ?, 'summary', ?, ?, ?)
                """,
                (claim["book_id"], claim_id, claim["summary"], new_summary, patch.reason),
            )
        if patch.status != claim["status"]:
            connection.execute(
                """
                INSERT INTO corrections(book_id, target_type, target_id, field_name, old_value, new_value, reason)
                VALUES (?, 'claim', ?, 'status', ?, ?, ?)
                """,
                (claim["book_id"], claim_id, claim["status"], patch.status, patch.reason),
            )
        for field_name, old_value, new_value in (
            ("directionality", claim["directionality"], new_direction),
            ("reverse_predicate", claim["reverse_predicate"] or "", new_reverse or ""),
        ):
            if str(old_value or "") != str(new_value or ""):
                connection.execute(
                    """
                    INSERT INTO corrections(book_id, target_type, target_id, field_name, old_value, new_value, reason)
                    VALUES (?, 'claim', ?, ?, ?, ?, ?)
                    """,
                    (claim["book_id"], claim_id, field_name, old_value or "", new_value or "", patch.reason),
                )
        connection.execute(
            """
            UPDATE claims SET status = ?, summary = ?, directionality = ?, reverse_predicate = ?
            WHERE id = ?
            """,
            (patch.status, new_summary, new_direction, new_reverse, claim_id),
        )
    return {
        "id": claim_id,
        "status": patch.status,
        "summary": new_summary,
        "directionality": new_direction,
        "reverse_predicate": new_reverse,
    }


@app.get("/api/books/{book_id}/systems")
def list_systems(
    book_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> list[dict[str, Any]]:
    """Return every evidence-bounded hierarchy, order, or network in one book."""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return systems_payload(
            connection, book_id, window.through_segment, window.from_segment,
        )


@app.post("/api/books/{book_id}/systems", status_code=201)
def create_system(book_id: int, request: SystemCreate) -> dict[str, Any]:
    """Create a system shell; an empty system does not assert a fictional hierarchy."""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        try:
            cursor = connection.execute(
                """
                INSERT INTO world_systems(book_id, name, category, structure_type, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (book_id, request.name, request.category, request.structure_type, request.description),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="同类体系中已经存在这个名称") from exc
        system_id = int(cursor.lastrowid)
        return next(item for item in systems_payload(connection, book_id) if int(item["id"]) == system_id)


@app.patch("/api/systems/{system_id}")
def patch_system(system_id: int, request: SystemPatch) -> dict[str, Any]:
    """Edit or archive a system without deleting its nodes and evidence."""

    with transaction(settings.database_path) as connection:
        system = connection.execute("SELECT * FROM world_systems WHERE id = ?", (system_id,)).fetchone()
        if system is None:
            raise HTTPException(status_code=404, detail="找不到这个体系")
        assignments: list[str] = []
        values: list[Any] = []
        for key in ("name", "category", "structure_type", "description", "status"):
            value = getattr(request, key)
            if value is not None:
                assignments.append(f"{key} = ?")
                values.append(value)
        if assignments:
            connection.execute(
                f"UPDATE world_systems SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # noqa: S608
                (*values, system_id),
            )
        return next(item for item in systems_payload(connection, int(system["book_id"])) if int(item["id"]) == system_id)


@app.delete("/api/systems/{system_id}")
def archive_system(system_id: int) -> dict[str, Any]:
    """Archive a system while preserving its evidence and revision history."""

    with transaction(settings.database_path) as connection:
        system = connection.execute("SELECT * FROM world_systems WHERE id = ?", (system_id,)).fetchone()
        if system is None:
            raise HTTPException(status_code=404, detail="找不到这个体系")
        connection.execute(
            "UPDATE world_systems SET status = 'archived', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (system_id,),
        )
        return {"id": system_id, "status": "archived"}


def _validated_segment_quote(
    connection: sqlite3.Connection,
    book_id: int,
    segment_id: int | None,
    quote: str,
) -> sqlite3.Row | None:
    if segment_id is None and not quote.strip():
        return None
    if segment_id is None or not quote.strip():
        raise HTTPException(status_code=422, detail="原文证据必须同时包含章节和逐字引文")
    segment = connection.execute(
        "SELECT * FROM segments WHERE id = ? AND book_id = ?", (segment_id, book_id),
    ).fetchone()
    if segment is None or find_quote(str(segment["text"]), quote) is None:
        raise HTTPException(status_code=422, detail="引文无法在指定章节逐字找到")
    return segment


@app.post("/api/systems/{system_id}/nodes", status_code=201)
def create_system_node(system_id: int, request: SystemNodeCreate) -> dict[str, Any]:
    """Add a system node and bind its literal evidence when provided."""

    with transaction(settings.database_path) as connection:
        system = connection.execute("SELECT * FROM world_systems WHERE id = ?", (system_id,)).fetchone()
        if system is None:
            raise HTTPException(status_code=404, detail="找不到这个体系")
        book_id = int(system["book_id"])
        segment = _validated_segment_quote(connection, book_id, request.segment_id, request.evidence_quote)
        if segment is None:
            raise HTTPException(status_code=422, detail="体系节点必须绑定逐字原文证据")
        cursor = connection.execute(
            """
            INSERT INTO world_system_nodes(
                system_id, label, description, rank_value, concept_id,
                effective_from_segment, effective_to_segment, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                system_id, request.label, request.description, request.rank_value, request.concept_id,
                request.effective_from_segment, request.effective_to_segment, request.confidence,
            ),
        )
        node_id = int(cursor.lastrowid)
        if segment is not None and request.segment_id is not None:
            add_evidence(
                connection, book_id, "system_node", node_id, request.segment_id,
                str(segment["text"]), request.evidence_quote,
            )
            evidence = connection.execute(
                "SELECT id FROM evidence WHERE target_type = 'system_node' AND target_id = ? ORDER BY id DESC LIMIT 1",
                (node_id,),
            ).fetchone()
            if evidence is not None:
                connection.execute("UPDATE world_system_nodes SET evidence_id = ? WHERE id = ?", (int(evidence["id"]), node_id))
        return next(
            node for item in systems_payload(connection, book_id) if int(item["id"]) == system_id
            for node in item["nodes"] if int(node["id"]) == node_id
        )


@app.patch("/api/system-nodes/{node_id}")
def patch_system_node(node_id: int, request: SystemNodePatch) -> dict[str, Any]:
    """Edit a node without changing its bound source evidence."""

    with transaction(settings.database_path) as connection:
        node = connection.execute(
            "SELECT node.*, system.book_id FROM world_system_nodes node JOIN world_systems system ON system.id = node.system_id WHERE node.id = ?",
            (node_id,),
        ).fetchone()
        if node is None:
            raise HTTPException(status_code=404, detail="找不到这个体系节点")
        assignments: list[str] = []
        values: list[Any] = []
        for key in ("label", "description", "effective_from_segment", "effective_to_segment", "status"):
            value = getattr(request, key)
            if value is not None:
                assignments.append(f"{key} = ?")
                values.append(value)
        if request.clear_rank:
            assignments.append("rank_value = NULL")
        elif request.rank_value is not None:
            assignments.append("rank_value = ?")
            values.append(request.rank_value)
        if assignments:
            connection.execute(
                f"UPDATE world_system_nodes SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # noqa: S608
                (*values, node_id),
            )
        return next(
            item for system in systems_payload(connection, int(node["book_id"])) if int(system["id"]) == int(node["system_id"])
            for item in system["nodes"] if int(item["id"]) == node_id
        )


@app.delete("/api/system-nodes/{node_id}")
def archive_system_node(node_id: int) -> dict[str, Any]:
    """Archive a node and its visible edges without deleting historical evidence."""

    with transaction(settings.database_path) as connection:
        node = connection.execute("SELECT * FROM world_system_nodes WHERE id = ?", (node_id,)).fetchone()
        if node is None:
            raise HTTPException(status_code=404, detail="找不到这个体系节点")
        connection.execute("UPDATE world_system_nodes SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (node_id,))
        connection.execute("UPDATE world_system_relations SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP WHERE source_node_id = ? OR target_node_id = ?", (node_id, node_id))
        return {"id": node_id, "status": "deprecated"}


@app.post("/api/systems/{system_id}/relations", status_code=201)
def create_system_relation(system_id: int, request: SystemRelationCreate) -> dict[str, Any]:
    """Add a supported comparison or hierarchy edge; unsupported ordering remains incomparable."""

    with transaction(settings.database_path) as connection:
        system = connection.execute("SELECT * FROM world_systems WHERE id = ?", (system_id,)).fetchone()
        if system is None:
            raise HTTPException(status_code=404, detail="找不到这个体系")
        book_id = int(system["book_id"])
        endpoints = connection.execute(
            "SELECT COUNT(*) AS count FROM world_system_nodes WHERE system_id = ? AND id IN (?, ?)",
            (system_id, request.source_node_id, request.target_node_id),
        ).fetchone()
        if int(endpoints["count"] or 0) != 2:
            raise HTTPException(status_code=422, detail="关系两端必须属于同一个体系")
        segment = _validated_segment_quote(connection, book_id, request.segment_id, request.evidence_quote)
        if segment is None:
            raise HTTPException(status_code=422, detail="体系关系必须绑定逐字原文证据")
        cursor = connection.execute(
            """
            INSERT INTO world_system_relations(
                system_id, source_node_id, target_node_id, relation_type, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (system_id, request.source_node_id, request.target_node_id, request.relation_type, request.confidence),
        )
        relation_id = int(cursor.lastrowid)
        if segment is not None and request.segment_id is not None:
            add_evidence(
                connection, book_id, "system_relation", relation_id, request.segment_id,
                str(segment["text"]), request.evidence_quote,
            )
            evidence = connection.execute(
                "SELECT id FROM evidence WHERE target_type = 'system_relation' AND target_id = ? ORDER BY id DESC LIMIT 1",
                (relation_id,),
            ).fetchone()
            if evidence is not None:
                connection.execute(
                    "UPDATE world_system_relations SET evidence_id = ? WHERE id = ?",
                    (int(evidence["id"]), relation_id),
                )
        return next(
            relation for item in systems_payload(connection, book_id) if int(item["id"]) == system_id
            for relation in item["relations"] if int(relation["id"]) == relation_id
        )


@app.patch("/api/system-relations/{relation_id}")
def patch_system_relation(relation_id: int, request: SystemRelationPatch) -> dict[str, Any]:
    """Edit an edge while keeping its literal evidence binding."""

    with transaction(settings.database_path) as connection:
        relation = connection.execute(
            "SELECT relation.*, system.book_id FROM world_system_relations relation JOIN world_systems system ON system.id = relation.system_id WHERE relation.id = ?",
            (relation_id,),
        ).fetchone()
        if relation is None:
            raise HTTPException(status_code=404, detail="找不到这条体系关系")
        assignments: list[str] = []
        values: list[Any] = []
        for key in ("relation_type", "confidence", "status"):
            value = getattr(request, key)
            if value is not None:
                assignments.append(f"{key} = ?")
                values.append(value)
        if assignments:
            connection.execute(
                f"UPDATE world_system_relations SET {', '.join(assignments)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",  # noqa: S608
                (*values, relation_id),
            )
        return next(
            item for system in systems_payload(connection, int(relation["book_id"])) if int(system["id"]) == int(relation["system_id"])
            for item in system["relations"] if int(item["id"]) == relation_id
        )


@app.delete("/api/system-relations/{relation_id}")
def archive_system_relation(relation_id: int) -> dict[str, Any]:
    """Archive one edge without erasing its evidence."""

    with transaction(settings.database_path) as connection:
        relation = connection.execute("SELECT * FROM world_system_relations WHERE id = ?", (relation_id,)).fetchone()
        if relation is None:
            raise HTTPException(status_code=404, detail="找不到这条体系关系")
        connection.execute("UPDATE world_system_relations SET status = 'deprecated', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (relation_id,))
        return {"id": relation_id, "status": "deprecated"}


@app.get("/api/books/{book_id}/story-context/{event_id}")
def story_context(
    book_id: int,
    event_id: int,
    from_segment: int | None = Query(default=None, ge=0),
    through_segment: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Return a zero-call knowledge capsule constrained by the spoiler boundary."""

    with connect(settings.database_path) as connection:
        book = ensure_book(connection, book_id)
        try:
            window = resolve_reading_window(
                connection, book_id,
                from_segment=from_segment,
                through_segment=through_segment,
            )
            payload = story_knowledge_context(
                connection, book_id, event_id, window.through_segment, window.from_segment,
            )
            payload["reading_window"] = window.payload()
            return payload
        except ValueError as exc:
            if "窗口" in str(exc) or "范围" in str(exc):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/books/{book_id}/knowledge/complete", status_code=202)
def complete_knowledge(book_id: int, request: KnowledgeCompleteRequest) -> dict[str, Any]:
    """Build a reviewable, local-first completion candidate without silently asserting facts."""

    with transaction(settings.database_path) as connection:
        ensure_book(connection, book_id)
        segment_ids = request.segment_ids
        if request.concept_id is not None:
            concept = connection.execute(
                "SELECT * FROM concepts WHERE id = ? AND book_id = ?", (request.concept_id, book_id),
            ).fetchone()
            if concept is None:
                raise HTTPException(status_code=422, detail="知识概念不属于当前书籍")
        if not segment_ids:
            segment_ids = [int(row["id"]) for row in connection.execute(
                "SELECT id FROM segments WHERE book_id = ? ORDER BY ordinal LIMIT 12", (book_id,),
            )]
        marks = ",".join("?" for _ in segment_ids) or "NULL"
        rows = connection.execute(
            f"SELECT id, ordinal, chapter_title, text FROM segments WHERE book_id = ? AND id IN ({marks}) ORDER BY ordinal",  # noqa: S608
            (book_id, *segment_ids),
        ).fetchall()
        label = str(concept["preferred_label"]) if request.concept_id is not None else ""
        candidates: list[dict[str, Any]] = []
        for row in rows:
            text_value = str(row["text"])
            if label and label not in text_value:
                continue
            index = text_value.find(label) if label else 0
            start = max(0, index - 90)
            quote = text_value[start:start + 260].strip()
            if quote:
                candidates.append({
                    "segment_id": int(row["id"]), "ordinal": int(row["ordinal"]),
                    "chapter_title": str(row["chapter_title"]), "evidence_quote": quote,
                })
            if len(candidates) >= 8:
                break
        cursor = connection.execute(
            """
            INSERT INTO knowledge_completion_requests(
                book_id, concept_id, instruction, segment_ids_json, status, result_json
            ) VALUES (?, ?, ?, ?, 'candidate_ready', ?)
            """,
            (
                book_id, request.concept_id, request.instruction,
                json.dumps(segment_ids, ensure_ascii=False),
                json.dumps({"candidates": candidates, "provider": request.provider}, ensure_ascii=False),
            ),
        )
        return {
            "id": int(cursor.lastrowid), "status": "candidate_ready", "candidates": candidates,
            "message": "候选引文已经整理，只有人工确认或模型结构化复核后才会写入正式事实",
        }


@app.get("/api/ui-issues")
def list_ui_issues(status: str | None = None) -> list[dict[str, Any]]:
    """Return the local visual acceptance ledger without exposing screenshot files."""

    with connect(settings.database_path) as connection:
        query = "SELECT * FROM ui_issues"
        parameters: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY CASE severity WHEN 'blocker' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id DESC"
        return rows(connection.execute(query, parameters).fetchall())


@app.post("/api/ui-issues", status_code=201)
def create_ui_issue(request: UiIssueCreate) -> dict[str, Any]:
    """Add one reproducible interface issue to the release ledger."""

    with transaction(settings.database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ui_issues(
                page_key, component_key, issue_class, viewport,
                detected_environment, verified_environment, severity, summary,
                root_cause, reproduction, acceptance, screenshot_path,
                regression_test, regression_case
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.page_key, request.component_key, request.issue_class, request.viewport,
                request.detected_environment, request.verified_environment, request.severity, request.summary,
                request.root_cause, request.reproduction, request.acceptance, request.screenshot_path,
                request.regression_test, request.regression_case,
            ),
        )
        return dict(connection.execute("SELECT * FROM ui_issues WHERE id = ?", (int(cursor.lastrowid),)).fetchone())


@app.patch("/api/ui-issues/{issue_id}")
def patch_ui_issue(issue_id: int, request: UiIssuePatch) -> dict[str, Any]:
    """Update or verify one interface issue for the release gate."""

    with transaction(settings.database_path) as connection:
        issue = connection.execute("SELECT * FROM ui_issues WHERE id = ?", (issue_id,)).fetchone()
        if issue is None:
            raise HTTPException(status_code=404, detail="找不到这条界面问题")
        if request.status == "verified":
            verified_environment = request.verified_environment or str(issue["verified_environment"] or "")
            screenshot_path = request.screenshot_path or str(issue["screenshot_path"] or "")
            if not verified_environment or not screenshot_path:
                raise HTTPException(status_code=422, detail="完成界面验证前需要记录实际视口和本机截图")
        assignments: list[str] = []
        values: list[Any] = []
        for key in (
            "severity", "component_key", "issue_class", "detected_environment", "verified_environment",
            "summary", "root_cause", "reproduction", "acceptance", "screenshot_path",
            "regression_test", "regression_case", "status",
        ):
            value = getattr(request, key)
            if value is not None:
                assignments.append(f"{key} = ?")
                values.append(value)
        if request.status in {"verified", "wont_fix"}:
            assignments.append("closed_at = CURRENT_TIMESTAMP")
        elif request.status in {"open", "fixed"}:
            assignments.append("closed_at = NULL")
        if assignments:
            connection.execute(
                f"UPDATE ui_issues SET {', '.join(assignments)} WHERE id = ?",  # noqa: S608
                (*values, issue_id),
            )
        return dict(connection.execute("SELECT * FROM ui_issues WHERE id = ?", (issue_id,)).fetchone())


@app.get("/healthz", include_in_schema=False)
def health() -> dict[str, str]:
    """供本机容器与反向代理检查进程状态，不返回书库或模型信息。"""

    return {"status": "ok", "version": APP_VERSION}


@app.get("/readyz", include_in_schema=False)
def ready() -> dict[str, str]:
    """确认数据库已经可以读写查询。"""

    with connect(settings.database_path) as connection:
        connection.execute("SELECT 1").fetchone()
    return {
        "status": "ready",
        "version": APP_VERSION,
        "build_commit": BUILD_COMMIT,
        "build_time": BUILD_TIME,
        "layout_version": LAYOUT_VERSION,
        "database_schema_version": DATABASE_SCHEMA_VERSION,
    }


@app.get("/")
def index() -> FileResponse:
    """返回单页应用入口。"""

    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
