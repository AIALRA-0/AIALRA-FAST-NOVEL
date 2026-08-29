"""分析任务的保守预估、自动预算适配、紧急保护线和可复用请求缓存。"""

from __future__ import annotations

import hashlib
import math
import sqlite3
from statistics import median
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


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


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


def forecast_job_usage(
    connection: sqlite3.Connection,
    book_id: int,
    start_segment: int,
    end_segment: int,
    pricing: PricingSnapshot,
    review_mode: str,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    reanalyze: bool = False,
) -> dict[str, object]:
    """Calibrate a median and conservative forecast from real completed calls."""

    segment_rows = connection.execute(
        """
        SELECT segment.id, segment.ordinal, LENGTH(segment.text) AS characters,
               EXISTS(
                   SELECT 1 FROM segment_results result
                   WHERE result.book_id = segment.book_id AND result.segment_id = segment.id
                     AND result.provider = ? AND result.model = ? AND result.prompt_version = ?
               ) AS exact_cache
        FROM segments segment
        WHERE segment.book_id = ? AND segment.ordinal BETWEEN ? AND ?
        ORDER BY segment.ordinal
        """,
        (provider, model, prompt_version, book_id, start_segment, end_segment),
    ).fetchall()
    pending = [row for row in segment_rows if reanalyze or not bool(row["exact_cache"])]

    historical = connection.execute(
        """
        SELECT LENGTH(segment.text) AS characters,
               job_segment.input_tokens, job_segment.output_tokens,
               job_segment.cache_hit_input_tokens, job_segment.cache_miss_input_tokens
        FROM analysis_job_segments job_segment
        JOIN analysis_jobs job ON job.id = job_segment.job_id
        JOIN segments segment ON segment.id = job_segment.segment_id
        WHERE job.provider = ? AND job.model = ? AND job_segment.status = 'completed'
          AND job_segment.input_tokens > 0 AND LENGTH(segment.text) > 0
        ORDER BY job_segment.id DESC LIMIT 400
        """,
        (provider, model),
    ).fetchall()
    input_ratios = [float(row["input_tokens"]) / max(1, int(row["characters"])) for row in historical]
    output_ratios = [float(row["output_tokens"]) / max(1, int(row["characters"])) for row in historical]
    cache_shares = [
        float(row["cache_hit_input_tokens"]) / max(1, int(row["input_tokens"]))
        for row in historical
    ]
    sample_count = len(historical)

    if sample_count >= 3:
        input_mid_ratio = median(input_ratios)
        output_mid_ratio = median(output_ratios)
        input_upper_ratio = max(input_mid_ratio, _percentile(input_ratios, 0.90))
        output_upper_ratio = max(output_mid_ratio, _percentile(output_ratios, 0.90))
        cache_probability = min(0.95, max(0.0, median(cache_shares)))
        confidence = "high" if sample_count >= 40 else "medium" if sample_count >= 10 else "low"
        method = "historical_calibration"
    else:
        # There is not enough ledger history to claim precision. Use a broad envelope.
        input_mid_ratio, output_mid_ratio = 1.8, 0.42
        input_upper_ratio, output_upper_ratio = 3.2, 1.05
        cache_probability = 0.0
        confidence = "low"
        method = "cold_start_envelope"

    fixed_input_mid = 0 if sample_count >= 3 else 4_500
    fixed_input_upper = 0 if sample_count >= 3 else 8_500
    input_mid = sum(math.ceil(int(row["characters"]) * input_mid_ratio) + fixed_input_mid for row in pending)
    output_mid = sum(max(300, math.ceil(int(row["characters"]) * output_mid_ratio)) for row in pending)
    input_upper = sum(math.ceil(int(row["characters"]) * input_upper_ratio) + fixed_input_upper for row in pending)
    output_upper = sum(max(600, math.ceil(int(row["characters"]) * output_upper_ratio)) for row in pending)

    if review_mode == "full" and pending:
        input_mid += min(160_000, max(8_000, input_mid // 8))
        output_mid += min(30_000, max(2_000, output_mid // 10))
        input_upper += min(240_000, max(12_000, input_upper // 6))
        output_upper += min(40_000, max(4_000, output_upper // 8))

    cache_hit_mid = math.floor(input_mid * cache_probability)
    cache_miss_mid = max(0, input_mid - cache_hit_mid)
    median_cost = calculate_cost_usd(cache_hit_mid, cache_miss_mid, output_mid, pricing)
    upper_cost = calculate_cost_usd(0, input_upper, output_upper, pricing)

    actual = connection.execute(
        """
        SELECT COALESCE(SUM(job.estimated_cost_usd), 0) AS amount,
               COALESCE(SUM(job.input_tokens), 0) AS input_tokens,
               COALESCE(SUM(job.output_tokens), 0) AS output_tokens
        FROM analysis_jobs job
        WHERE job.book_id = ? AND job.provider = ? AND job.model = ?
          AND job.status IN ('completed', 'needs_review', 'quality_checking')
        """,
        (book_id, provider, model),
    ).fetchone()
    task_breakdown = [{
        "task": "segment_extraction",
        "segments": len(pending),
        "median_input_tokens": input_mid,
        "median_output_tokens": output_mid,
        "conservative_input_tokens": input_upper,
        "conservative_output_tokens": output_upper,
    }]
    if review_mode == "full":
        task_breakdown.append({"task": "global_review", "segments": 1 if pending else 0})
    backtest = None
    chronological = list(reversed(historical[:50]))
    if len(chronological) >= 20:
        calibration = chronological[:10]
        validation = chronological[10:]
        input_ratio = median(float(row["input_tokens"]) / max(1, int(row["characters"])) for row in calibration)
        output_ratio = median(float(row["output_tokens"]) / max(1, int(row["characters"])) for row in calibration)
        cache_share = median(
            float(row["cache_hit_input_tokens"]) / max(1, int(row["input_tokens"]))
            for row in calibration
        )
        predicted_input = sum(math.ceil(int(row["characters"]) * input_ratio) for row in validation)
        predicted_output = sum(math.ceil(int(row["characters"]) * output_ratio) for row in validation)
        predicted_hit = math.floor(predicted_input * cache_share)
        predicted_cost = calculate_cost_usd(predicted_hit, predicted_input - predicted_hit, predicted_output, pricing)
        actual_hit = sum(int(row["cache_hit_input_tokens"] or 0) for row in validation)
        actual_miss = sum(int(row["cache_miss_input_tokens"] or 0) for row in validation)
        if actual_hit + actual_miss == 0:
            actual_miss = sum(int(row["input_tokens"] or 0) for row in validation)
        actual_output = sum(int(row["output_tokens"] or 0) for row in validation)
        actual_cost = calculate_cost_usd(actual_hit, actual_miss, actual_output, pricing)
        error_percent = None
        if predicted_cost is not None and actual_cost is not None and actual_cost > 0:
            error_percent = round(abs(predicted_cost - actual_cost) / actual_cost * 100, 2)
        backtest = {
            "calibration_segments": len(calibration),
            "validation_segments": len(validation),
            "predicted_cost_usd": predicted_cost,
            "actual_cost_usd": actual_cost,
            "absolute_error_percent": error_percent,
            "target_percent": 25,
            "passed": error_percent is not None and error_percent <= 25,
        }

    return {
        "forecast_version": "cost-forecast-v2",
        "provider": provider,
        "model": model,
        "start_segment": start_segment,
        "end_segment": end_segment,
        "total_segments": len(segment_rows),
        "pending_segments": len(pending),
        "exact_cache_segments": len(segment_rows) - len(pending),
        "median_input_tokens": input_mid,
        "median_output_tokens": output_mid,
        "conservative_input_tokens": input_upper,
        "conservative_output_tokens": output_upper,
        "median_cost_usd": median_cost,
        "conservative_cost_usd": upper_cost,
        "actual_cost_usd": float(actual["amount"] or 0),
        "actual_input_tokens": int(actual["input_tokens"] or 0),
        "actual_output_tokens": int(actual["output_tokens"] or 0),
        "cache_hit_probability": round(cache_probability, 4),
        "sample_count": sample_count,
        "confidence": confidence,
        "method": method,
        "task_breakdown": task_breakdown,
        "backtest": backtest,
        "pricing_available": pricing.available,
        "pricing_source": pricing.source,
    }


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
