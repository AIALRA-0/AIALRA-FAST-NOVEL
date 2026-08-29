"""验证模型结构错误说明和重试用量累加。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.models import ExtractionResult, GlobalReviewResult
from app.providers import (
    MockProvider,
    OpenAICompatibleProvider,
    ProviderError,
    ProviderUsage,
    _codex_version_tuple,
    _codex_event_error,
    _strict_codex_schema,
    _restore_codex_attribute_maps,
    add_usage,
    parse_structured_content,
)


def test_codex_version_comparison_accepts_desktop_prerelease_suffix() -> None:
    """桌面应用内置的新 CLI 应按基础版本胜过 PATH 中的旧版本。"""

    assert _codex_version_tuple("codex-cli 0.149.0-alpha.4.3") == (0, 149, 0)
    assert _codex_version_tuple("codex-cli 0.149.0-alpha.4.3") > _codex_version_tuple("codex-cli 0.128.0")


def test_codex_failure_message_comes_from_json_event_without_echoing_input() -> None:
    """CLI 失败时应显示事件里的根因，不把小说提示词写进错误。"""

    message = _codex_event_error([
        {"type": "turn.failed", "error": {"message": "模型不支持当前结构"}},
        {"type": "item.completed", "text": "敏感小说原文"},
    ])
    assert message == "模型不支持当前结构"
    assert "敏感小说原文" not in message


def test_codex_schema_requires_every_property_recursively() -> None:
    """Codex 的严格结构必须在根对象和嵌套对象列出所有属性。"""

    schema = _strict_codex_schema(ExtractionResult.model_json_schema())
    assert set(schema["required"]) == set(schema["properties"])
    entity = schema["$defs"]["EntityCandidate"]
    assert set(entity["required"]) == set(entity["properties"])
    assert entity["additionalProperties"] is False
    entry_attributes = schema["$defs"]["EntryCandidate"]["properties"]["attributes"]
    assert entry_attributes["type"] == "array"
    assert entry_attributes["items"]["required"] == ["key", "value"]


def test_codex_attribute_pairs_are_restored_to_database_dictionary() -> None:
    """严格结构中的属性键值对需要在验证前恢复为条目字典。"""

    restored = _restore_codex_attribute_maps({
        "entries": [{"attributes": [{"key": "品阶", "value": "玄级"}, {"key": "数量", "value": 2}]}]
    })
    assert restored["entries"][0]["attributes"] == {"品阶": "玄级", "数量": 2}


def test_invalid_list_item_is_rejected_without_losing_segment() -> None:
    """单条候选结构错误时丢弃该条，其余完整 JSON 仍可入库。"""

    content = '{"entities":[],"relations":[],"place_relations":[],"events":[],"world_notes":[],"entries":[{"category":"错误类别","name":"敏感原文","summary":"说明","attributes":{},"confidence":0.8,"evidence_quote":"敏感引文"}]}'

    rejected: list[str] = []
    result = parse_structured_content(content, ExtractionResult, rejected)

    assert result.entries == []
    assert rejected == ["entries.0"]


def test_root_structure_error_does_not_echo_input() -> None:
    """无法局部拒绝的顶层错误只暴露字段路径，不把内容写进日志。"""

    with pytest.raises(ProviderError) as error:
        parse_structured_content('{"entities":"敏感原文"}', ExtractionResult)

    assert "entities:list_type" in str(error.value)
    assert "敏感原文" not in str(error.value)


def test_provider_usage_adds_failed_and_successful_attempts() -> None:
    """逻辑分析内的失败重试也会计入令牌账本。"""

    total = add_usage(
        ProviderUsage(100, 20, 60, 40),
        ProviderUsage(120, 30, 80, 40),
    )

    assert total == ProviderUsage(220, 50, 140, 80)


def test_review_overflow_is_truncated_instead_of_losing_batch() -> None:
    """全书复核偶尔多给一条时间建议时，应保留前 60 条而不是丢掉整批。"""

    suggestion = {
        "earlier_event_title": "先发生",
        "later_event_title": "后发生",
        "reason": "原文明确说明先后",
        "confidence": 0.9,
    }
    content = json.dumps({
        "syntheses": [],
        "merge_suggestions": [],
        "order_suggestions": [suggestion for _ in range(61)],
        "contradictions": [],
        "protagonist_name": "孙悟空",
    }, ensure_ascii=False)

    result = parse_structured_content(content, GlobalReviewResult)

    assert len(result.order_suggestions) == 60


def test_mock_record_regeneration_keeps_multiline_evidence() -> None:
    """离线演示需要完整保留带换行的首条证据，才能通过正式证据闸门。"""

    quote = "第一行原文。\n第二行仍属于同一条原文。"
    response = asyncio.run(
        MockProvider().regenerate_record(
            "现有标题：测试设定\n现有分类：rule\n"
            f"<VERIFIED_QUOTES>\n- {quote}\n</VERIFIED_QUOTES>"
        )
    )

    assert response.result.evidence_quotes == [quote]
    assert response.result.summary == quote


def test_deepseek_v4_structured_call_disables_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构化批处理必须关闭默认思考，避免思维链耗尽输出额度。"""

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "choices": [{"finish_reason": "stop", "message": {"content": ExtractionResult().model_dump_json()}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args: object) -> None:
            del args

        async def post(self, url: str, *, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
            del url, headers
            captured.update(json)
            return FakeResponse()

    monkeypatch.setattr("app.providers.httpx.AsyncClient", FakeClient)
    provider = OpenAICompatibleProvider(
        name="deepseek",
        api_key="test-key",
        base_url="https://example.invalid",
        model="deepseek-v4-flash",
        json_schema=False,
    )

    asyncio.run(provider.extract("第一章", 0, "一段测试原文。"))

    assert captured["thinking"] == {"type": "disabled"}
