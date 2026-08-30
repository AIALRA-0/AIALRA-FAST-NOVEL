"""模型价格快照与可复算费用。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PricingSnapshot:
    """一次任务使用的美元计价标准。"""

    cache_hit_input_usd_per_million: float | None
    cache_miss_input_usd_per_million: float | None
    output_usd_per_million: float | None
    source: str
    effective_date: str

    @property
    def available(self) -> bool:
        """三个费率齐全时，金额才可复算。"""

        return all(
            rate is not None
            for rate in (
                self.cache_hit_input_usd_per_million,
                self.cache_miss_input_usd_per_million,
                self.output_usd_per_million,
            )
        )


DEEPSEEK_CHAT_PRICING = PricingSnapshot(
    cache_hit_input_usd_per_million=0.07,
    cache_miss_input_usd_per_million=0.27,
    output_usd_per_million=1.10,
    source="DeepSeek 官方价格页",
    effective_date="2026-08-23",
)

# DeepSeek 以人民币结算。这里按 1 美元兑 7.2 元人民币把高峰时段单价换成美元，
# 预算判断保持保守；账本同时保存价格日期和换算依据。
DEEPSEEK_V4_FLASH_PRICING = PricingSnapshot(
    cache_hit_input_usd_per_million=0.01388889,
    cache_miss_input_usd_per_million=0.41666667,
    output_usd_per_million=1.25,
    source="DeepSeek 官方高峰价；按 1 美元兑 7.2 元人民币换算",
    effective_date="2026-08-24",
)

DEEPSEEK_V4_PRO_PRICING = PricingSnapshot(
    cache_hit_input_usd_per_million=0.04166667,
    cache_miss_input_usd_per_million=1.25,
    output_usd_per_million=3.75,
    source="DeepSeek 官方高峰价；按 1 美元兑 7.2 元人民币换算",
    effective_date="2026-08-24",
)

MOCK_PRICING = PricingSnapshot(
    cache_hit_input_usd_per_million=0.0,
    cache_miss_input_usd_per_million=0.0,
    output_usd_per_million=0.0,
    source="本地离线模式",
    effective_date="2026-08-23",
)

UNKNOWN_PRICING = PricingSnapshot(
    cache_hit_input_usd_per_million=None,
    cache_miss_input_usd_per_million=None,
    output_usd_per_million=None,
    source="供应商价格尚未配置",
    effective_date="",
)


def pricing_for(provider: str, model: str) -> PricingSnapshot:
    """返回供应商和模型对应的计价快照。"""

    if provider == "mock":
        return MOCK_PRICING
    if provider == "deepseek" and model in {"deepseek-chat", "deepseek-reasoner"}:
        if model == "deepseek-reasoner":
            return PricingSnapshot(
                cache_hit_input_usd_per_million=0.14,
                cache_miss_input_usd_per_million=0.55,
                output_usd_per_million=2.19,
                source="DeepSeek 官方价格页",
                effective_date="2026-08-23",
            )
        return DEEPSEEK_CHAT_PRICING
    if provider == "deepseek" and model == "deepseek-v4-flash":
        return DEEPSEEK_V4_FLASH_PRICING
    if provider == "deepseek" and model == "deepseek-v4-pro":
        return DEEPSEEK_V4_PRO_PRICING
    return UNKNOWN_PRICING


def calculate_cost_usd(
    cache_hit_input_tokens: int,
    cache_miss_input_tokens: int,
    output_tokens: int,
    snapshot: PricingSnapshot,
) -> float | None:
    """按照一百万令牌单价计算美元费用。"""

    if not snapshot.available:
        return None
    cost = (
        cache_hit_input_tokens * float(snapshot.cache_hit_input_usd_per_million)
        + cache_miss_input_tokens * float(snapshot.cache_miss_input_usd_per_million)
        + output_tokens * float(snapshot.output_usd_per_million)
    ) / 1_000_000
    return round(cost, 8)
