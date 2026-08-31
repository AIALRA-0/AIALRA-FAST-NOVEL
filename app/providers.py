"""DeepSeek、Moonshot、Codex CLI 与离线模拟模型适配器。"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models import (
    ConnectivityAuditDecision,
    ConnectivityAuditResult,
    EntityCandidate,
    EventCandidate,
    ExtractionResult,
    GlobalReviewResult,
    RecordRegenerationResult,
)
from app.prompts import (
    CONNECTIVITY_AUDIT_PROMPT,
    GLOBAL_REVIEW_PROMPT,
    RECORD_REGENERATION_PROMPT,
    SYSTEM_PROMPT,
    build_user_prompt,
)


@dataclass(frozen=True)
class ProviderUsage:
    """一次成功请求的令牌明细。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0


def add_usage(left: ProviderUsage, right: ProviderUsage) -> ProviderUsage:
    """累加同一次逻辑分析中的重试用量，失败请求也能进入费用账本。"""

    return ProviderUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_hit_input_tokens=left.cache_hit_input_tokens + right.cache_hit_input_tokens,
        cache_miss_input_tokens=left.cache_miss_input_tokens + right.cache_miss_input_tokens,
    )


class ProviderError(RuntimeError):
    """屏蔽原文和密钥，只携带可操作错误与已经产生的令牌用量。"""

    def __init__(self, message: str, usage: ProviderUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage or ProviderUsage()


async def probe_provider(
    provider: str,
    *,
    api_key: str | None = None,
    base_url: str = "",
    model: str = "",
) -> dict[str, Any]:
    """只探测供应商模型目录；不发送小说内容，也不改变路由资格。

    返回值刻意只包含状态和错误代码，绝不回传密钥或响应正文
    """

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if provider == "codex_luna":
        status = codex_cli_status()
        configured = bool(status.get("available"))
        return {
            "provider": provider,
            "configured": configured,
            "reachable": configured,
            "model_available": configured,
            "status": "connected" if configured else "unconfigured",
            "last_checked_at": now,
            "error_code": None if configured else "not_logged_in",
            "requires_key": False,
            "model": status.get("model", "gpt-5.6-luna"),
        }
    if provider not in {"deepseek", "moonshot"}:
        raise ValueError("不支持探测该模型供应商")
    if not api_key:
        return {
            "provider": provider,
            "configured": False,
            "reachable": False,
            "model_available": False,
            "status": "unconfigured",
            "last_checked_at": now,
            "error_code": "missing_key",
            "requires_key": True,
            "model": model,
        }
    root = str(base_url or "").rstrip("/")
    if provider == "moonshot" and not root.endswith("/v1"):
        root = f"{root}/v1"
    url = f"{root}/models"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = await client.get(url, headers=headers)
        status_code = int(response.status_code)
        if status_code in {401, 403}:
            error_code = "auth_failed"
            status = "auth_failed"
            reachable = True
            model_available = False
        elif status_code == 404:
            error_code = "models_endpoint_not_found"
            status = "service_error"
            reachable = True
            model_available = False
        elif status_code >= 500:
            error_code = "service_unavailable"
            status = "service_error"
            reachable = False
            model_available = False
        else:
            response.raise_for_status()
            payload = response.json()
            entries = payload.get("data", []) if isinstance(payload, dict) else []
            model_ids = {
                str(item.get("id"))
                for item in entries
                if isinstance(item, dict) and item.get("id")
            }
            model_available = not model or model in model_ids
            error_code = None if model_available else "model_not_found"
            status = "connected" if model_available else "model_unavailable"
            reachable = True
        return {
            "provider": provider,
            "configured": True,
            "reachable": reachable,
            "model_available": model_available,
            "status": status,
            "last_checked_at": now,
            "error_code": error_code,
            "requires_key": False,
            "model": model,
        }
    except httpx.TimeoutException:
        return {
            "provider": provider,
            "configured": True,
            "reachable": False,
            "model_available": False,
            "status": "timeout",
            "last_checked_at": now,
            "error_code": "timeout",
            "requires_key": False,
            "model": model,
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        return {
            "provider": provider,
            "configured": True,
            "reachable": False,
            "model_available": False,
            "status": "service_error",
            "last_checked_at": now,
            "error_code": "invalid_response",
            "requires_key": False,
            "model": model,
        }


@dataclass(frozen=True)
class ProviderResponse:
    """结构结果和可复算的令牌用量。"""

    extraction: ExtractionResult
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0
    structural_rejections: int = 0


@dataclass(frozen=True)
class GlobalReviewResponse:
    """全书整理结果和令牌明细。"""

    result: GlobalReviewResult
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0


@dataclass(frozen=True)
class RecordRegenerationResponse:
    """一张候选卡片及其模型调用用量。"""

    result: RecordRegenerationResult
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0


@dataclass(frozen=True)
class ConnectivityAuditResponse:
    """孤立节点专项复审结果和令牌明细。"""

    result: ConnectivityAuditResult
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0


class Provider(Protocol):
    """所有模型供应商遵守的最小协议。"""

    name: str
    model: str
    auth_mode: str

    def prompt_version(self, task_key: str, base_version: str, fallback: str) -> str:
        """返回包含当前提示词哈希的缓存版本。"""

    async def extract(self, chapter_title: str, ordinal: int, text: str, context: str = "") -> ProviderResponse:
        """抽取单个原文片段。"""

    async def review_knowledge(self, facts: str) -> GlobalReviewResponse:
        """整理一批已经核验的跨章节事实。"""

    async def regenerate_record(self, record_context: str) -> RecordRegenerationResponse:
        """根据记录、陈述式任务和证据生成候选版本。"""

    async def review_connectivity(self, audit_payload: str) -> ConnectivityAuditResponse:
        """只复审缺少关系线的人物和势力。"""


def parse_json_content(content: str) -> ExtractionResult:
    """移除偶发代码围栏并执行严格结构校验。"""

    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return ExtractionResult.model_validate_json(cleaned)
    except Exception as exc:
        raise ProviderError("模型返回的 JSON 未通过结构校验。") from exc


def parse_structured_content(
    content: str,
    model_type: type[BaseModel],
    rejected_items: list[str] | None = None,
) -> BaseModel:
    """严格解析 JSON；单条候选结构错误时只拒绝该候选，不丢弃整段。"""

    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
    except Exception as exc:
        raise ProviderError("模型返回的内容不是完整 JSON。") from exc
    for _ in range(200):
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            # 根对象中的列表项彼此独立；删除坏项并留下拒绝计数，避免一条坏方位拖垮整章。
            removable: set[tuple[str, int]] = set()
            truncated = False
            for item in exc.errors(include_input=False):
                location = item.get("loc", ())
                if (
                    len(location) == 1
                    and isinstance(location[0], str)
                    and item.get("type") == "too_long"
                    and isinstance(payload, dict)
                    and isinstance(payload.get(location[0]), list)
                ):
                    field = model_type.model_fields.get(location[0])
                    maximum = next(
                        (
                            int(metadata.max_length)
                            for metadata in (field.metadata if field is not None else [])
                            if getattr(metadata, "max_length", None) is not None
                        ),
                        None,
                    )
                    if maximum is not None:
                        removed_count = max(0, len(payload[location[0]]) - maximum)
                        del payload[location[0]][maximum:]
                        if rejected_items is not None:
                            rejected_items.extend(f"{location[0]}.overflow" for _ in range(removed_count))
                        truncated = True
                    continue
                if (
                    len(location) >= 2
                    and isinstance(location[0], str)
                    and isinstance(location[1], int)
                    and isinstance(payload, dict)
                    and isinstance(payload.get(location[0]), list)
                ):
                    removable.add((location[0], location[1]))
            if truncated and not removable:
                continue
            if not removable:
                issues = []
                for item in exc.errors(include_input=False)[:8]:
                    location = ".".join(str(part) for part in item.get("loc", ())) or "根节点"
                    issues.append(f"{location}:{item.get('type', 'invalid')}")
                detail = "；".join(issues) or "未知结构错误"
                raise ProviderError(f"模型 JSON 结构错误：{detail}") from exc
            for field, index in sorted(removable, key=lambda value: (value[0], -value[1])):
                items = payload[field]
                if 0 <= index < len(items):
                    items.pop(index)
                    if rejected_items is not None:
                        rejected_items.append(f"{field}.{index}")
    raise ProviderError("模型 JSON 含有过多不合格候选。")


class OpenAICompatibleProvider:
    """调用与 OpenAI Chat Completions 兼容的供应商接口。"""

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        json_schema: bool,
        prompt_overrides: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.json_schema = json_schema
        self.auth_mode = "api"
        self.prompt_overrides = prompt_overrides or {}

    def prompt_for(self, task_key: str, fallback: str) -> str:
        """使用当前正式提示词或单片段试跑指定的草稿。"""

        return self.prompt_overrides.get(task_key, fallback)

    def prompt_version(self, task_key: str, base_version: str, fallback: str) -> str:
        """让阅读规则和提示词变更自动失效旧缓存。"""

        digest = hashlib.sha256(self.prompt_for(task_key, fallback).encode("utf-8")).hexdigest()[:12]
        return f"{base_version}-{digest}"

    async def extract(self, chapter_title: str, ordinal: int, text: str, context: str = "") -> ProviderResponse:
        """发送受控请求，最多进行一次结构失败重试。"""

        result, usage, structural_rejections = await self._request_json(
            self.prompt_for("extraction", SYSTEM_PROMPT),
            build_user_prompt(chapter_title, ordinal, text, context),
            ExtractionResult,
            "novel_extraction",
        )
        return ProviderResponse(
            extraction=ExtractionResult.model_validate(result),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_hit_input_tokens=usage.cache_hit_input_tokens,
            cache_miss_input_tokens=usage.cache_miss_input_tokens,
            structural_rejections=structural_rejections,
        )

    async def review_knowledge(self, facts: str) -> GlobalReviewResponse:
        """对已核验事实做一次低频全局整理。"""

        result, usage, _ = await self._request_json(
            self.prompt_for("global_review", GLOBAL_REVIEW_PROMPT),
            f"<VERIFIED_FACTS>\n{facts}\n</VERIFIED_FACTS>\n\n整理这些事实。",
            GlobalReviewResult,
            "novel_global_review",
        )
        return GlobalReviewResponse(
            result=GlobalReviewResult.model_validate(result),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_hit_input_tokens=usage.cache_hit_input_tokens,
            cache_miss_input_tokens=usage.cache_miss_input_tokens,
        )

    async def regenerate_record(self, record_context: str) -> RecordRegenerationResponse:
        """生成证据受限草稿，不直接修改正式记录。"""

        result, usage, _ = await self._request_json(
            self.prompt_for("record_regeneration", RECORD_REGENERATION_PROMPT),
            record_context,
            RecordRegenerationResult,
            "novel_record_regeneration",
        )
        return RecordRegenerationResponse(
            result=RecordRegenerationResult.model_validate(result),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_hit_input_tokens=usage.cache_hit_input_tokens,
            cache_miss_input_tokens=usage.cache_miss_input_tokens,
        )

    async def review_connectivity(self, audit_payload: str) -> ConnectivityAuditResponse:
        """使用独立长提示词复审孤立节点，不重复发送整本书。"""

        result, usage, _ = await self._request_json(
            self.prompt_for("connectivity_audit", CONNECTIVITY_AUDIT_PROMPT),
            f"<AUDIT_PAYLOAD>\n{audit_payload}\n</AUDIT_PAYLOAD>\n\n逐项完成关系完整性复审。",
            ConnectivityAuditResult,
            "novel_connectivity_audit",
        )
        return ConnectivityAuditResponse(
            result=ConnectivityAuditResult.model_validate(result),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_hit_input_tokens=usage.cache_hit_input_tokens,
            cache_miss_input_tokens=usage.cache_miss_input_tokens,
        )

    async def _request_json(
        self,
        system_prompt: str,
        user_prompt: str,
        result_type: type[BaseModel],
        schema_name: str,
        thinking_mode: str = "disabled",
    ) -> tuple[BaseModel, ProviderUsage, int]:
        """复用供应商请求、结构校验和一次格式重试。"""

        response_format: dict[str, object]
        if self.json_schema:
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": result_type.model_json_schema(),
                },
            }
        else:
            response_format = {"type": "json_object"}
        system_content = system_prompt
        if not self.json_schema:
            system_content += "\n\n必须严格符合以下 JSON Schema：\n" + json.dumps(
                result_type.model_json_schema(), ensure_ascii=False
            )
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 8192,
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": response_format,
        }
        # DeepSeek V4 默认开启思考模式；结构抽取更需要短而完整的 JSON。
        # 显式关闭思考可以避免思维链耗尽输出额度，也能显著降低长篇批处理费用。
        if self.name == "deepseek" and self.model.startswith("deepseek-v4-"):
            payload["thinking"] = {"type": thinking_mode}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error = ""
        total_usage = ProviderUsage()
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
                    response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                response.raise_for_status()
                body = response.json()
                usage = body.get("usage") or {}
                input_tokens = int(usage.get("prompt_tokens") or 0)
                cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
                current_usage = ProviderUsage(
                    input_tokens=input_tokens,
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    cache_hit_input_tokens=cache_hit,
                    cache_miss_input_tokens=int(
                        usage.get("prompt_cache_miss_tokens") or max(0, input_tokens - cache_hit)
                    ),
                )
                total_usage = add_usage(total_usage, current_usage)
                if body.get("choices", [{}])[0].get("finish_reason") == "length":
                    raise ProviderError("模型输出达到长度上限，需要减少条目数量。")
                content = body["choices"][0]["message"]["content"]
                if not content:
                    raise ProviderError("模型返回了空内容。")
                rejected_items: list[str] = []
                result = parse_structured_content(content, result_type, rejected_items)
                return result, total_usage, len(rejected_items)
            except (httpx.HTTPError, KeyError, TypeError, ValueError, ProviderError) as exc:
                last_error = str(exc)
                # 输出已经到达供应商上限时，重复同一请求只会再次付费；交给上层拆分批次。
                if isinstance(exc, ProviderError) and "长度上限" in last_error:
                    raise ProviderError(f"{self.name} 分析失败：{last_error[:500]}", total_usage) from exc
                if attempt == 0:
                    payload["messages"].append(
                        {
                            "role": "user",
                            "content": (
                                "上一次输出没有通过结构检查。错误摘要："
                                f"{last_error[:500]}。请减少次要条目，只返回完整 JSON；"
                                "逐项修正字段类型、枚举值和必填字段，不能附加解释。"
                            ),
                        }
                    )
        raise ProviderError(f"{self.name} 分析失败：{last_error[:500]}", total_usage)


def _maximum_usage_from_events(events: list[dict[str, Any]]) -> ProviderUsage:
    """从 Codex JSON 事件中读取累计用量，缺失字段保持为零。"""

    maxima = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }

    def visit(value: Any) -> None:
        # Codex 事件结构会随版本增加外层字段，因此递归寻找标准用量键。
        if isinstance(value, dict):
            for key, item in value.items():
                if key in maxima and isinstance(item, (int, float)):
                    maxima[key] = max(maxima[key], int(item))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(events)
    cache_hit = max(maxima["cached_input_tokens"], maxima["cache_read_input_tokens"])
    input_tokens = maxima["input_tokens"]
    return ProviderUsage(
        input_tokens=input_tokens,
        output_tokens=maxima["output_tokens"],
        cache_hit_input_tokens=cache_hit,
        cache_miss_input_tokens=max(0, input_tokens - cache_hit),
    )


def _codex_event_error(events: list[dict[str, Any]]) -> str:
    """从 Codex JSON 事件中提取公开错误，不回显输入提示词。"""

    messages: list[str] = []
    for event in events:
        event_type = str(event.get("type", ""))
        if event_type not in {"error", "turn.failed", "item.failed"}:
            continue
        error = event.get("error")
        if isinstance(error, dict) and error.get("message"):
            messages.append(str(error["message"]))
        elif isinstance(error, str):
            messages.append(error)
        elif event.get("message"):
            messages.append(str(event["message"]))
    return "；".join(dict.fromkeys(messages))[:500]


def _strict_codex_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """生成 Codex 严格输出结构，每个对象都拒绝额外字段并列出全部属性。"""

    normalized = json.loads(json.dumps(schema))

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object":
                properties = value.get("properties")
                if not isinstance(properties, dict) and value.get("additionalProperties") is True:
                    # 严格结构不支持任意键对象，先让模型输出键值对数组，返回后再还原为字典。
                    value.clear()
                    value.update({
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "value": {
                                    "anyOf": [
                                        {"type": "string"}, {"type": "number"},
                                        {"type": "boolean"}, {"type": "null"},
                                    ]
                                },
                            },
                            "required": ["key", "value"],
                            "additionalProperties": False,
                        },
                    })
                    return
                if not isinstance(properties, dict):
                    properties = {}
                    value["properties"] = properties
                value["additionalProperties"] = False
                value["required"] = list(properties)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(normalized)
    return normalized


def _restore_codex_attribute_maps(value: Any) -> Any:
    """把严格结构使用的属性键值对数组还原为应用内部字典。"""

    if isinstance(value, dict):
        restored: dict[str, Any] = {}
        for key, item in value.items():
            if key == "attributes" and isinstance(item, list):
                restored[key] = {
                    str(pair["key"]): pair.get("value")
                    for pair in item
                    if isinstance(pair, dict) and pair.get("key")
                }
            else:
                restored[key] = _restore_codex_attribute_maps(item)
        return restored
    if isinstance(value, list):
        return [_restore_codex_attribute_maps(item) for item in value]
    return value


def _codex_version_tuple(text: str) -> tuple[int, int, int]:
    """把 CLI 版本转换为可比较数字，预发布后缀不影响基础版本选择。"""

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text)
    if match is None:
        return (0, 0, 0)
    return tuple(int(part) for part in match.groups())


@functools.lru_cache(maxsize=1)
def resolve_codex_cli() -> tuple[str | None, str]:
    """从 PATH 和 Windows 桌面应用中选择版本最高的可运行 Codex CLI。"""

    candidates: list[str] = []
    configured = os.getenv("NOVEL_CODEX_CLI", "").strip()
    if configured:
        candidates.append(configured)
    discovered = shutil.which("codex")
    if discovered:
        candidates.append(discovered)
    if os.name == "nt":
        try:
            located = subprocess.run(
                ["where.exe", "codex"], capture_output=True, text=True, timeout=5, check=False,
            )
            if located.returncode == 0:
                candidates.extend(line.strip() for line in located.stdout.splitlines() if line.strip())
        except (OSError, subprocess.SubprocessError):
            pass
    best_path: str | None = None
    best_version = ""
    best_tuple = (0, 0, 0)
    for candidate in dict.fromkeys(candidates):
        try:
            version = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=8, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        version_text = (version.stdout + version.stderr).strip()
        version_tuple = _codex_version_tuple(version_text)
        if version.returncode == 0 and (best_path is None or version_tuple > best_tuple):
            best_path = candidate
            best_version = version_text
            best_tuple = version_tuple
    return best_path, best_version


@functools.lru_cache(maxsize=1)
def _cached_codex_cli_status() -> dict[str, Any]:
    """每个应用进程只执行一次 CLI 登录预检，避免页面刷新反复启动外部进程。"""

    executable, version_text = resolve_codex_cli()
    if not executable:
        return {
            "available": False,
            "logged_in": False,
            "version": "",
            "model": "gpt-5.6-luna",
            "message": "本机没有找到 Codex CLI。",
        }
    try:
        login = subprocess.run(
            [executable, "login", "-c", 'service_tier="fast"', "status"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "logged_in": False,
            "version": "",
            "model": "gpt-5.6-luna",
            "message": f"Codex CLI 预检失败：{str(exc)[:180]}",
        }
    logged_in = login.returncode == 0 and "logged in" in (login.stdout + login.stderr).lower()
    return {
        "available": bool(executable and version_text and logged_in),
        "logged_in": logged_in,
        "version": version_text,
        "model": "gpt-5.6-luna",
        "message": "已经通过 ChatGPT 登录，可用于本机主动任务。" if logged_in else "Codex CLI 尚未通过 ChatGPT 登录。",
    }


def codex_cli_status() -> dict[str, Any]:
    """返回本机预检副本；安装或重新登录 CLI 后重启应用即可刷新。"""

    return dict(_cached_codex_cli_status())


class CodexCliProvider(OpenAICompatibleProvider):
    """通过本机 ChatGPT 登录运行 Luna，只处理用户主动启动的任务。"""

    def __init__(self, prompt_overrides: dict[str, str] | None = None) -> None:
        super().__init__(
            name="codex_luna",
            api_key="",
            base_url="",
            model="gpt-5.6-luna",
            json_schema=True,
            prompt_overrides=prompt_overrides,
        )
        self.auth_mode = "chatgpt_login"

    async def _request_json(
        self,
        system_prompt: str,
        user_prompt: str,
        result_type: type[BaseModel],
        schema_name: str,
        thinking_mode: str = "disabled",
    ) -> tuple[BaseModel, ProviderUsage, int]:
        """在隔离临时目录中运行一次非交互 Codex 结构化任务。"""

        del schema_name, thinking_mode
        status = codex_cli_status()
        if not status["available"]:
            raise ProviderError(str(status["message"]))
        executable, _ = resolve_codex_cli()
        if not executable:
            raise ProviderError("本机没有找到 Codex CLI。")
        instruction = (
            system_prompt
            + "\n\n<LOCAL_NOVEL_TASK>\n"
            + user_prompt
            + "\n</LOCAL_NOVEL_TASK>\n\n"
            + "只返回符合输出结构的最终 JSON。不要读取工作区文件，不要运行命令，不要修改任何文件。"
        )
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="novel-atlas-codex-") as temporary:
            temp_path = Path(temporary)
            schema_path = temp_path / "schema.json"
            output_path = temp_path / "result.json"
            schema_path.write_text(
                json.dumps(_strict_codex_schema(result_type.model_json_schema()), ensure_ascii=False),
                encoding="utf-8",
            )
            command = [
                executable, "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                "--skip-git-repo-check", "--sandbox", "read-only", "-m", self.model,
                "--output-schema", str(schema_path), "--output-last-message", str(output_path),
                "--json", "-C", str(temp_path), "-",
            ]
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(instruction.encode("utf-8")), timeout=300,
                )
            except TimeoutError as exc:
                process.kill()
                await process.wait()
                raise ProviderError("Codex Luna 单次试跑超过五分钟，已经安全终止。") from exc
            events: list[dict[str, Any]] = []
            for line in stdout.decode("utf-8", errors="replace").splitlines():
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    events.append(parsed)
            usage = _maximum_usage_from_events(events)
            if process.returncode != 0:
                error = stderr.decode("utf-8", errors="replace").strip()
                event_error = _codex_event_error(events)
                visible_error = event_error or error[:500] or "没有返回错误说明"
                raise ProviderError(f"Codex Luna 运行失败：{visible_error}", usage)
            if not output_path.exists():
                raise ProviderError("Codex Luna 没有生成结构化结果。", usage)
            content = output_path.read_text(encoding="utf-8")
            try:
                content = json.dumps(
                    _restore_codex_attribute_maps(json.loads(content)), ensure_ascii=False,
                )
            except json.JSONDecodeError:
                pass
        rejected_items: list[str] = []
        result = parse_structured_content(content, result_type, rejected_items)
        # 用量以 CLI 事件为准；耗时由上层运行清单记录，不能换算成虚假美元价格。
        del started
        return result, usage, len(rejected_items)


class MockProvider:
    """离线演示适配器，只识别显式测试标记并生成可验证事件。"""

    name = "mock"
    model = "evidence-demo-v1"
    auth_mode = "local"

    @staticmethod
    def prompt_version(task_key: str, base_version: str, fallback: str) -> str:
        """离线演示没有外部提示词调用，仍保留稳定版本号。"""

        del task_key, fallback
        return base_version

    async def extract(self, chapter_title: str, ordinal: int, text: str, context: str = "") -> ProviderResponse:
        """对普通小说只生成首句事件，不猜测人物身份。"""

        del context

        first_sentence = next((part.strip() + "。" for part in text.split("。") if part.strip()), "")
        if not first_sentence or first_sentence not in text:
            return ProviderResponse(ExtractionResult())
        entities: list[EntityCandidate] = []
        for name in re.findall(r"【人物：([^】]{1,20})】", text):
            quote = f"【人物：{name}】"
            entities.append(
                EntityCandidate(
                    name=name,
                    kind="person",
                    aliases=[],
                    summary="由离线测试标记声明的人物。",
                    importance=0.5,
                    evidence_quote=quote,
                )
            )
        event = EventCandidate(
            title=f"{chapter_title}：{first_sentence[:18]}",
            summary=first_sentence,
            narrative_order=ordinal,
            story_order=float(ordinal),
            temporal_kind="unknown",
            temporal_value="原文没有提供可归一化时间",
            location=None,
            transport="",
            participants=[],
            confidence=0.6,
            evidence_quote=first_sentence,
        )
        return ProviderResponse(ExtractionResult(entities=entities, events=[event]))

    async def review_knowledge(self, facts: str) -> GlobalReviewResponse:
        """离线模式不猜测跨章节结论。"""

        del facts
        return GlobalReviewResponse(GlobalReviewResult())

    async def regenerate_record(self, record_context: str) -> RecordRegenerationResponse:
        """离线模式从请求中的现有记录和首条证据生成可验收草稿。"""

        title_match = re.search(r"现有标题：(.+)", record_context)
        category_match = re.search(r"现有分类：(.+)", record_context)
        quote_match = re.search(
            r"<VERIFIED_QUOTES>\s*\n- (.*?)\n</VERIFIED_QUOTES>",
            record_context,
            flags=re.DOTALL,
        )
        title = title_match.group(1).strip() if title_match else "待整理条目"
        category = category_match.group(1).strip() if category_match else "other"
        quote_block = quote_match.group(1).strip() if quote_match else "原文证据"
        quote = quote_block.split("\n- ", 1)[0].strip()
        return RecordRegenerationResponse(
            RecordRegenerationResult(
                title=title,
                summary=quote,
                category=category,
                attributes={},
                evidence_quotes=[quote],
            )
        )

    async def review_connectivity(self, audit_payload: str) -> ConnectivityAuditResponse:
        """离线模式确认已经穷举窗口且没有显式测试关系的节点。"""

        try:
            payload = json.loads(audit_payload)
        except json.JSONDecodeError as exc:
            raise ProviderError("离线关系复审输入不是完整 JSON。") from exc
        decisions = [
            ConnectivityAuditDecision(
                entity_id=int(item["entity_id"]),
                status="confirmed_isolated",
                reason="已扫描提供的全部提及窗口，没有发现能够逐字验证的关系。",
                confidence=1.0,
                relations=[],
            )
            for item in payload.get("target_entities", [])
        ]
        return ConnectivityAuditResponse(ConnectivityAuditResult(decisions=decisions))


def _prompt_overrides(
    settings: Settings,
    book_id: int | None,
    extraction_bundle_id: int | None = None,
) -> dict[str, str]:
    """读取当前正式提示词，试跑时只替换片段抽取草稿。"""

    try:
        from app.control_plane import PROMPT_TASKS, render_prompt_bundle
        from app.db import connect

        with connect(settings.database_path) as connection:
            return {
                task_key: render_prompt_bundle(
                    connection,
                    book_id,
                    task_key,
                    extraction_bundle_id if task_key == "extraction" else None,
                ).system_prompt
                for task_key in PROMPT_TASKS
            }
    except (OSError, ValueError, sqlite3.Error):
        # 数据库尚未初始化时继续使用代码内置提示词，应用初始化会立刻补齐注册表。
        return {}


def _auto_provider_name(settings: Settings) -> str:
    """按已通过赛马的路由优先选择供应商，没有赛马结果时使用可用的保守顺序。"""

    available = {
        "deepseek": bool(settings.deepseek_api_key),
        "moonshot": bool(settings.moonshot_api_key),
        "codex_luna": bool(codex_cli_status()["available"]),
        "mock": True,
    }
    try:
        from app.db import connect

        with connect(settings.database_path) as connection:
            routes = connection.execute(
                """
                SELECT provider FROM model_routes
                WHERE enabled = 1 AND eligible = 1 AND consecutive_failures < 3
                  AND (circuit_open_until IS NULL OR circuit_open_until <= CURRENT_TIMESTAMP)
                ORDER BY priority, id
                """
            ).fetchall()
        eligible = [str(row["provider"]) for row in routes if available.get(str(row["provider"]))]
        non_mock = next((provider for provider in eligible if provider != "mock"), None)
        if non_mock:
            return non_mock
        return "mock"
    except (OSError, sqlite3.Error):
        pass
    for provider in ("deepseek", "moonshot", "codex_luna", "mock"):
        if available[provider]:
            return provider
    return "mock"


def create_provider(
    settings: Settings,
    name: str,
    book_id: int | None = None,
    extraction_bundle_id: int | None = None,
) -> Provider:
    """根据名称创建供应商，并明确区分 API 与本机 ChatGPT 登录通道。"""

    if name == "auto":
        name = _auto_provider_name(settings)
    prompts = _prompt_overrides(settings, book_id, extraction_bundle_id)

    if name == "mock":
        return MockProvider()
    if name == "deepseek":
        if not settings.deepseek_api_key:
            raise ProviderError("当前进程没有设置 DEEPSEEK_API_KEY。")
        return OpenAICompatibleProvider(
            name="deepseek",
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            json_schema=False,
            prompt_overrides=prompts,
        )
    if name == "moonshot":
        if not settings.moonshot_api_key:
            raise ProviderError("当前进程没有设置 MOONSHOT_API_KEY；Kimi Code 订阅密钥不能替代它。")
        return OpenAICompatibleProvider(
            name="moonshot",
            api_key=settings.moonshot_api_key,
            base_url=settings.moonshot_base_url,
            model=settings.moonshot_model,
            json_schema=True,
            prompt_overrides=prompts,
        )
    if name == "codex_luna":
        status = codex_cli_status()
        if not status["available"]:
            raise ProviderError(str(status["message"]))
        return CodexCliProvider(prompt_overrides=prompts)
    raise ProviderError("未知模型供应商。")
