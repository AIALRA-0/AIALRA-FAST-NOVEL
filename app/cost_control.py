"""分析任务的保守预估、自动预算适配、紧急保护线和可复用请求缓存。"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass

from app.pricing import PricingSnapshot, calculate_cost_usd

ADAPTIVE_MAX_COST_USD = 1_000.0
ADAPTIVE_MAX_INPUT_TOKENS = 500_000_000
ADAPTIVE_MAX_OUTPUT_TOKENS = 100_000_000


@dataclass(frozen=True)
class UsageEstimate:
    """一次或一组模型请求的保守令牌与金额上限估算。"""

    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None
    segment_count: int


def estimate_segment_tokens(text: str, context_characters: int = 10_000) -> tuple[int, int]:
    """按中文长文本的偏高比例估算输入和结构化输出令牌。"""

    # 完整请求还包含固定提示词、JSON Schema 和输出约束。它们在短章节中占比很高，
    # 因此预估必须把观察到的请求信封成本一并纳入，避免预算只看正文长度。
    input_tokens = max(1_200, math.ceil(len(text) * 1.10) + math.ceil(context_characters * 0.65) + 20_000)
    output_tokens = max(1_200, min(10_240, math.ceil(len(text) * 1.10) + 1_600))
    return input_tokens, output_tokens


def estimate_job_usage(
    connection: sqlite3.Connection,
    book_id: int,
    start_segment: int,
    end_segment: int,
    pricing: PricingSnapshot,
    review_mode: str,
) -> UsageEstimate:
    """根据待分析原文计算任务启动前可见的保守成本。"""

    rows = connection.execute(
        """
        SELECT LENGTH(text) AS characters FROM segments
        WHERE book_id = ? AND ordinal BETWEEN ? AND ? ORDER BY ordinal
        """,
        (book_id, start_segment, end_segment),
    ).fetchall()
    input_tokens = 0
    output_tokens = 0
    for row in rows:
        characters = int(row["characters"] or 0)
        estimated_input, estimated_output = estimate_segment_tokens("文" * characters)
        input_tokens += estimated_input
        output_tokens += estimated_output
    if review_mode == "full" and rows:
        input_tokens += min(240_000, max(12_000, input_tokens // 6))
        output_tokens += min(40_000, max(4_000, output_tokens // 8))
    amount = calculate_cost_usd(0, input_tokens, output_tokens, pricing)
    return UsageEstimate(input_tokens, output_tokens, amount, len(rows))


def request_hash(
    provider: str,
    model: str,
    prompt_version: str,
    chapter_title: str,
    ordinal: int,
    text: str,
    context: str,
) -> str:
    """对完整请求内容做哈希，只有输入完全一致时才复用结果。"""

    payload = "\u241f".join((provider, model, prompt_version, chapter_title, str(ordinal), text, context))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def next_call_fits_budget(
    job: sqlite3.Row,
    estimated_input_tokens: int,
    estimated_output_tokens: int,
) -> tuple[bool, str, float | None]:
    """在发起请求前检查金额和两类令牌上限。"""

    projected_input = int(job["input_tokens"] or 0) + estimated_input_tokens
    projected_output = int(job["output_tokens"] or 0) + estimated_output_tokens
    if projected_input > int(job["max_input_tokens"]):
        return False, "预计下一次请求会超过输入令牌上限", None
    if projected_output > int(job["max_output_tokens"]):
        return False, "预计下一次请求会超过输出令牌上限", None
    pricing = PricingSnapshot(
        cache_hit_input_usd_per_million=job["cache_hit_input_usd_per_million"],
        cache_miss_input_usd_per_million=job["cache_miss_input_usd_per_million"],
        output_usd_per_million=job["output_usd_per_million"],
        source=str(job["pricing_source"]),
        effective_date=str(job["pricing_effective_date"]),
    )
    projected_cost = calculate_cost_usd(
        int(job["cache_hit_input_tokens"] or 0),
        int(job["cache_miss_input_tokens"] or 0) + estimated_input_tokens,
        projected_output,
        pricing,
    )
    if projected_cost is not None and projected_cost > float(job["max_cost_usd"]):
        return False, "预计下一次请求会超过任务金额上限", projected_cost
    return True, "预算充足", projected_cost


def actual_usage_fits_budget(
    job: sqlite3.Row,
    *,
    input_tokens: int,
    output_tokens: int,
) -> tuple[bool, str]:
    """模型返回后再次比较真实用量，避免预估偏低时静默越过任务上限。"""

    if int(input_tokens) > int(job["max_input_tokens"]):
        return False, "实际输入令牌已经超过任务上限"
    if int(output_tokens) > int(job["max_output_tokens"]):
        return False, "实际输出令牌已经超过任务上限"
    return True, "实际用量未超过任务上限"


def adaptive_budget_limits(
    job: sqlite3.Row,
    *,
    required_input_tokens: int,
    required_output_tokens: int,
) -> tuple[float, int, int, float | None] | None:
    """根据累计真实或预估用量扩展任务上限，并保留全局紧急保护线。"""

    try:
        budget_mode = str(job["budget_mode"])
    except (IndexError, KeyError):
        budget_mode = "manual"
    if budget_mode != "adaptive":
        return None
    pricing = PricingSnapshot(
        cache_hit_input_usd_per_million=job["cache_hit_input_usd_per_million"],
        cache_miss_input_usd_per_million=job["cache_miss_input_usd_per_million"],
        output_usd_per_million=job["output_usd_per_million"],
        source=str(job["pricing_source"]),
        effective_date=str(job["pricing_effective_date"]),
    )
    required_cost = calculate_cost_usd(
        int(job["cache_hit_input_tokens"] or 0),
        max(int(job["cache_miss_input_tokens"] or 0), required_input_tokens),
        required_output_tokens,
        pricing,
    )
    if (
        required_input_tokens > ADAPTIVE_MAX_INPUT_TOKENS
        or required_output_tokens > ADAPTIVE_MAX_OUTPUT_TOKENS
        or (required_cost is not None and required_cost > ADAPTIVE_MAX_COST_USD)
    ):
        return None
    input_limit = min(
        ADAPTIVE_MAX_INPUT_TOKENS,
        max(int(job["max_input_tokens"]), math.ceil(required_input_tokens * 1.2)),
    )
    output_limit = min(
        ADAPTIVE_MAX_OUTPUT_TOKENS,
        max(int(job["max_output_tokens"]), math.ceil(required_output_tokens * 1.2)),
    )
    cost_limit = float(job["max_cost_usd"])
    if required_cost is not None:
        cost_limit = min(
            ADAPTIVE_MAX_COST_USD,
            max(cost_limit, required_cost * 1.2, cost_limit * 1.5, cost_limit + 0.05),
        )
    return cost_limit, input_limit, output_limit, required_cost
