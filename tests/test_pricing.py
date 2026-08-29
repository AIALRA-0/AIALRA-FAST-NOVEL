"""验证模型费用可以从价格快照和令牌明细稳定复算。"""

from app.cost_control import ADAPTIVE_MAX_INPUT_TOKENS, actual_usage_fits_budget, adaptive_budget_limits
from app.pricing import DEEPSEEK_CHAT_PRICING, UNKNOWN_PRICING, calculate_cost_usd


def test_deepseek_cost_distinguishes_cache_hit_and_miss() -> None:
    """缓存命中输入、未命中输入和输出必须使用各自费率。"""

    cost = calculate_cost_usd(1_000_000, 1_000_000, 1_000_000, DEEPSEEK_CHAT_PRICING)
    assert cost == 1.44


def test_unknown_price_returns_no_fake_amount() -> None:
    """价格不完整时只记录令牌，不能伪造金额。"""

    assert calculate_cost_usd(10, 20, 30, UNKNOWN_PRICING) is None


def test_actual_usage_blocks_a_response_that_exceeds_the_output_limit() -> None:
    """预估通过后，真实响应仍然必须受到任务令牌上限约束。"""

    job = {"max_input_tokens": 25_000, "max_output_tokens": 5_000}
    fits, reason = actual_usage_fits_budget(job, input_tokens=24_111, output_tokens=6_193)  # type: ignore[arg-type]
    assert not fits
    assert reason == "实际输出令牌已经超过任务上限"


def test_adaptive_budget_expands_cumulative_limits_with_headroom() -> None:
    """自动模式为下一次调用留出余量，并保留可复算的金额范围。"""

    job = {
        "budget_mode": "adaptive",
        "max_cost_usd": 0.01,
        "max_input_tokens": 10_000,
        "max_output_tokens": 2_000,
        "cache_hit_input_tokens": 0,
        "cache_miss_input_tokens": 8_000,
        "cache_hit_input_usd_per_million": 0.07,
        "cache_miss_input_usd_per_million": 0.27,
        "output_usd_per_million": 1.1,
        "pricing_source": "测试价格",
        "pricing_effective_date": "2026-08-25",
    }
    expansion = adaptive_budget_limits(  # type: ignore[arg-type]
        job,
        required_input_tokens=25_000,
        required_output_tokens=6_000,
    )
    assert expansion is not None
    assert expansion[1] == 30_000
    assert expansion[2] == 7_200
    assert expansion[0] > 0.01


def test_adaptive_budget_keeps_global_emergency_limit() -> None:
    """异常规模超过全局保护线时，自动模式返回暂停信号。"""

    job = {
        "budget_mode": "adaptive",
        "max_cost_usd": 0.5,
        "max_input_tokens": 500_000,
        "max_output_tokens": 120_000,
        "cache_hit_input_tokens": 0,
        "cache_miss_input_tokens": 0,
        "cache_hit_input_usd_per_million": None,
        "cache_miss_input_usd_per_million": None,
        "output_usd_per_million": None,
        "pricing_source": "订阅用量",
        "pricing_effective_date": "2026-08-25",
    }
    assert adaptive_budget_limits(  # type: ignore[arg-type]
        job,
        required_input_tokens=ADAPTIVE_MAX_INPUT_TOKENS + 1,
        required_output_tokens=1_000,
    ) is None
