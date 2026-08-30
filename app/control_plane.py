"""透明协作、分层提示词、运行清单与模型路由的共享服务。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.prompts import (
    CONNECTIVITY_AUDIT_PROMPT,
    GLOBAL_REVIEW_PROMPT,
    RECORD_REGENERATION_PROMPT,
    SYSTEM_PROMPT,
)


SCHEMA_VERSION = "novel-knowledge-v2.4"
CONTRACT_VERSION = "collaboration-contract-v1"
PROMPT_TASKS: dict[str, tuple[str, str]] = {
    "extraction": ("片段结构抽取", SYSTEM_PROMPT),
    "global_review": ("全书事实整理", GLOBAL_REVIEW_PROMPT),
    "record_regeneration": ("世界信息和条目再生成", RECORD_REGENERATION_PROMPT),
    "connectivity_audit": ("孤立人物与势力关系复审", CONNECTIVITY_AUDIT_PROMPT),
}


@dataclass(frozen=True)
class RenderedPrompt:
    """一次调用实际使用的提示词版本与可审计分层。"""

    bundle_id: int
    task_key: str
    version: str
    status: str
    system_prompt: str
    prompt_hash: str
    domain_rule_hash: str
    external_fact_hash: str
    estimated_tokens: int
    layers: list[dict[str, Any]]
    external_facts: list[dict[str, Any]]


def stable_hash(*parts: object) -> str:
    """生成不会暴露原文和密钥的稳定摘要。"""

    encoded = "\u241f".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_json(value: object, fallback: Any) -> Any:
    """读取数据库 JSON；旧记录损坏时返回安全默认值。"""

    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def ensure_control_plane_defaults(connection: sqlite3.Connection) -> None:
    """初始化产品合同、正式提示词和候选模型路由。"""

    quality = {
        "critical_gold_percent": 100,
        "overall_holdout_percent": 95,
        "quote_integrity_percent": 100,
        "critical_subject_failures": 0,
        "unresolved_critical_conflicts": 0,
        "minimum_confirmed_cases": 300,
        "holdout_percent": 20,
    }
    exclusions = [
        "不承诺任意小说天然达到百分之九十五",
        "证据不足时不猜测主体、方位、时间和关系",
        "Codex CLI 登录通道不用于公网或无人值守服务",
    ]
    connection.execute(
        """
        INSERT INTO product_contracts(version, title, goal, quality_json, exclusions_json, status, promoted_at)
        VALUES (?, '透明协作与质量闭环', ?, ?, ?, 'production', CURRENT_TIMESTAMP)
        ON CONFLICT(version) DO NOTHING
        """,
        (
            CONTRACT_VERSION,
            "让每条要求、提示词、模型调用、原文证据、纠正和发布门禁可查看、可比较、可回滚",
            json.dumps(quality, ensure_ascii=False),
            json.dumps(exclusions, ensure_ascii=False),
        ),
    )
    for task_key, (_, prompt) in PROMPT_TASKS.items():
        connection.execute(
            """
            INSERT INTO prompt_bundle_versions(
                task_key, version, status, core_text, task_text, change_note, prompt_hash, promoted_at
            ) VALUES (?, 'v1', 'production', ?, '', '从 v2.3 受证据约束提示词迁移', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(task_key, version) DO NOTHING
            """,
            (task_key, prompt, stable_hash(prompt)),
        )
    route_defaults = (
        ("mock", "evidence-demo-v1", "local", 1, 1, 999),
        ("deepseek", "deepseek-v4-flash", "api", 1, 0, 20),
        ("moonshot", "kimi-k2.5", "api", 1, 0, 30),
        ("codex_luna", "gpt-5.6-luna", "chatgpt_login", 1, 0, 10),
    )
    connection.executemany(
        """
        INSERT INTO model_routes(provider, model, auth_mode, enabled, eligible, priority)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET model = excluded.model, auth_mode = excluded.auth_mode
        """,
        route_defaults,
    )
    if int(connection.execute("SELECT COUNT(*) FROM collaboration_items").fetchone()[0]) == 0:
        historical_feedback = (
            (
                "关系图出现大量本应有关系的孤立人物",
                "默认把孤立节点视为待复核，扫描全书窗口后才能确认孤立",
                ["父母、师徒和组织关系存在明确证据时必须连线", "确认孤立必须记录扫描范围"],
                ["关系抽取", "孤立节点复审", "关系图"],
            ),
            (
                "石猴出世等早期事件被排在编年史中间",
                "故事时间使用偏序约束，叙事出现顺序不能直接替代故事顺序",
                ["倒叙和回忆不打乱主线编年", "冲突时间边显式隔离"],
                ["时间抽取", "编年算法"],
            ),
            (
                "地图没有方位、地点缺失并且标签互相遮挡",
                "地图必须同时表达原文明示方位、拓扑连接和标签避让",
                ["明示方位可核验", "无方位时不猜坐标", "节点和标签保持可见"],
                ["地点抽取", "地图布局"],
            ),
            (
                "左侧行程和右侧故事步骤分离",
                "地图人物位置、当前事件和步骤详情共享唯一播放状态",
                ["切换步骤时三处同时更新", "右侧不出现其他步骤内容"],
                ["地图播放", "步骤详情"],
            ),
            (
                "快速点击时人物反复从错误地点出发",
                "每次移动取消旧动画并从画面当前坐标衔接到新目标",
                ["连续点击不回跳", "最后一次点击决定最终位置"],
                ["地图动画", "竞态处理"],
            ),
            (
                "世界信息高度重复并且缺少完整管理能力",
                "世界信息需要语义去重、搜索、分类、增删改查、再生成和版本恢复",
                ["近似重复进入合并审查", "编辑和归档可回滚"],
                ["世界信息", "数据管理"],
            ),
            (
                "最后一步按钮一直转圈",
                "播放状态必须有结束、取消、失败和超时终态",
                ["最后一步立即进入已完成状态", "异常不会留下永久加载状态"],
                ["播放控制", "错误恢复"],
            ),
            (
                "书库需要文件夹管理和不重跑旧章节的增量更新",
                "书库支持文件夹和书籍全量管理，增量分析只处理新增或受影响片段",
                ["安全追加不重跑旧章节", "冲突可自动或人工解决"],
                ["书库", "增量更新", "冲突中心"],
            ),
            (
                "提示词、框架和执行过程像黑盒",
                "完整提示词、规则来源、模型调用、成本、证据和验收结果全部可见",
                ["用户能查看最终拼装提示词", "用户纠正能转为永久测试"],
                ["协作控制台", "提示词注册表", "运行清单"],
            ),
        )
        connection.executemany(
            """
            INSERT INTO collaboration_items(
                original_text, interpreted_goal, acceptance_json, impact_json,
                requires_confirmation, status
            ) VALUES (?, ?, ?, ?, 0, 'validating')
            """,
            [
                (
                    original, goal, json.dumps(acceptance, ensure_ascii=False),
                    json.dumps(impact, ensure_ascii=False),
                )
                for original, goal, acceptance, impact in historical_feedback
            ],
        )


def product_contract(connection: sqlite3.Connection) -> dict[str, Any]:
    """返回当前正式产品合同。"""

    row = connection.execute(
        "SELECT * FROM product_contracts WHERE status = 'production' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        ensure_control_plane_defaults(connection)
        row = connection.execute(
            "SELECT * FROM product_contracts WHERE status = 'production' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    item = dict(row)
    item["quality"] = parse_json(item.pop("quality_json"), {})
    item["exclusions"] = parse_json(item.pop("exclusions_json"), [])
    return item


def _active_bundle(
    connection: sqlite3.Connection,
    task_key: str,
    bundle_id: int | None = None,
) -> sqlite3.Row:
    """读取指定草稿或当前正式提示词。"""

    if task_key not in PROMPT_TASKS:
        raise ValueError("未知提示词任务。")
    if bundle_id is not None:
        row = connection.execute(
            "SELECT * FROM prompt_bundle_versions WHERE id = ? AND task_key = ?",
            (bundle_id, task_key),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT * FROM prompt_bundle_versions
            WHERE task_key = ? AND status = 'production' ORDER BY id DESC LIMIT 1
            """,
            (task_key,),
        ).fetchone()
    if row is None:
        raise ValueError("找不到可用提示词版本。")
    return row


def render_prompt_bundle(
    connection: sqlite3.Connection,
    book_id: int | None,
    task_key: str,
    bundle_id: int | None = None,
) -> RenderedPrompt:
    """拼装核心提示词和用户阅读规则，外部事实始终单独展示。"""

    bundle = _active_bundle(connection, task_key, bundle_id)
    params: list[Any] = [task_key]
    book_clause = "book_id IS NULL"
    if book_id is not None:
        book_clause = "(book_id IS NULL OR book_id = ?)"
        params.append(book_id)
    rules = [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT * FROM domain_rules
            WHERE active = 1 AND task_key IN ('all', ?) AND {book_clause}
            ORDER BY priority, id
            """,
            params,
        ).fetchall()
    ]
    facts: list[dict[str, Any]] = []
    report_language = "follow_source"
    if book_id is not None:
        book = connection.execute(
            "SELECT report_language FROM books WHERE id = ?", (book_id,)
        ).fetchone()
        if book is not None:
            report_language = str(book["report_language"] or "follow_source")
        facts = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM external_facts WHERE book_id = ? AND active = 1 ORDER BY id",
                (book_id,),
            ).fetchall()
        ]
    core_text = str(bundle["core_text"]).strip()
    task_text = str(bundle["task_text"]).strip()
    system_parts = [core_text]
    if task_text:
        system_parts.append("<TASK_RULES>\n" + task_text + "\n</TASK_RULES>")
    if rules:
        statements = "\n".join(f"{index + 1}. {item['statement']}" for index, item in enumerate(rules))
        system_parts.append(
            "<USER_READING_RULES>\n"
            "以下内容只规定阅读方法，不能替代小说原文证据：\n"
            f"{statements}\n"
            "</USER_READING_RULES>"
        )
    language_rules = {
        "zh-CN": (
            "所有面向读者的摘要、说明、报告标题和生成字段使用简体中文；"
            "人物、地点、作品名和逐字证据保持原文写法；不得翻译或改写引文"
        ),
        "en": (
            "Write every reader-facing summary, explanation, report title, and generated field in English; "
            "preserve names, titles, and verbatim evidence in the source text; never translate or rewrite quotations"
        ),
        "follow_source": (
            "面向读者的摘要、说明、报告标题和生成字段使用当前原文的主要语言；"
            "人物、地点、作品名和逐字证据保持原文写法；不得翻译或改写引文"
        ),
    }
    language_text = language_rules.get(report_language, language_rules["follow_source"])
    system_parts.append("<OUTPUT_LANGUAGE>\n" + language_text + "\n</OUTPUT_LANGUAGE>")
    final_prompt = "\n\n".join(part for part in system_parts if part)
    rule_hash = stable_hash(*(f"{item['id']}:{item['version']}:{item['statement']}" for item in rules))
    fact_hash = stable_hash(*(f"{item['id']}:{item['statement']}:{item['source_label']}" for item in facts))
    prompt_hash = stable_hash(final_prompt)
    layers = [
        {"key": "core", "label": "核心约束", "editable": False, "text": core_text},
        {"key": "task", "label": "任务规则", "editable": True, "text": task_text},
        {
            "key": "domain_rules",
            "label": "阅读规则",
            "editable": True,
            "text": "\n".join(str(item["statement"]) for item in rules),
            "count": len(rules),
        },
        {
            "key": "output_language",
            "label": "生成语言",
            "editable": False,
            "text": language_text,
            "value": report_language,
        },
        {
            "key": "external_facts",
            "label": "外部事实",
            "editable": True,
            "text": "\n".join(str(item["statement"]) for item in facts),
            "count": len(facts),
            "injected": False,
        },
    ]
    return RenderedPrompt(
        bundle_id=int(bundle["id"]),
        task_key=task_key,
        version=str(bundle["version"]),
        status=str(bundle["status"]),
        system_prompt=final_prompt,
        prompt_hash=prompt_hash,
        domain_rule_hash=rule_hash,
        external_fact_hash=fact_hash,
        estimated_tokens=max(1, (len(final_prompt) + 2) // 3),
        layers=layers,
        external_facts=facts,
    )


def prompt_bundle_payload(rendered: RenderedPrompt) -> dict[str, Any]:
    """把渲染结果转换成不会丢失分层信息的接口对象。"""

    return {
        "id": rendered.bundle_id,
        "task_key": rendered.task_key,
        "task_label": PROMPT_TASKS[rendered.task_key][0],
        "version": rendered.version,
        "status": rendered.status,
        "system_prompt": rendered.system_prompt,
        "prompt_hash": rendered.prompt_hash,
        "domain_rule_hash": rendered.domain_rule_hash,
        "external_fact_hash": rendered.external_fact_hash,
        "estimated_tokens": rendered.estimated_tokens,
        "layers": rendered.layers,
        "external_facts": rendered.external_facts,
        "external_facts_injected": False,
    }


def suite_version(connection: sqlite3.Connection, book_id: int | None) -> str:
    """根据已确认金标准生成评估集版本。"""

    if book_id is None:
        rows = connection.execute(
            """
            SELECT id, updated_at, expected_json FROM quality_benchmark_cases
            WHERE confirmed_by_user = 1
              AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
            ORDER BY id
            """
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT id, updated_at, expected_json FROM quality_benchmark_cases
            WHERE book_id = ? AND confirmed_by_user = 1
              AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
            ORDER BY id
            """,
            (book_id,),
        ).fetchall()
    digest = stable_hash(*(f"{row['id']}:{row['updated_at']}:{row['expected_json']}" for row in rows))[:12]
    return f"gold-{len(rows)}-{digest}"


def create_run_manifest(
    connection: sqlite3.Connection,
    *,
    book_id: int | None,
    job_id: int | None,
    run_kind: str,
    provider: str,
    model: str,
    auth_mode: str,
    prompt: RenderedPrompt,
    input_scope: dict[str, Any],
    input_hash: str,
) -> int:
    """在调用模型前保存可复现运行清单。"""

    contract = product_contract(connection)
    cursor = connection.execute(
        """
        INSERT INTO run_manifests(
            book_id, job_id, run_kind, provider, model, auth_mode, contract_version,
            prompt_bundle_id, prompt_hash, domain_rule_hash, external_fact_hash,
            schema_version, eval_suite_version, input_scope_json, input_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book_id, job_id, run_kind, provider, model, auth_mode, contract["version"],
            prompt.bundle_id, prompt.prompt_hash, prompt.domain_rule_hash,
            prompt.external_fact_hash, SCHEMA_VERSION, suite_version(connection, book_id),
            json.dumps(input_scope, ensure_ascii=False), input_hash,
        ),
    )
    return int(cursor.lastrowid)


def complete_run_manifest(
    connection: sqlite3.Connection,
    manifest_id: int,
    *,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float | None = None,
    duration_ms: int = 0,
    validation: dict[str, Any] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> None:
    """完成运行清单并保留验证和冲突结果。"""

    connection.execute(
        """
        UPDATE run_manifests SET status = ?, input_tokens = ?, output_tokens = ?,
            estimated_cost_usd = ?, duration_ms = ?, validation_json = ?, conflict_json = ?,
            completed_at = ? WHERE id = ?
        """,
        (
            status, input_tokens, output_tokens, estimated_cost_usd, duration_ms,
            json.dumps(validation or {}, ensure_ascii=False),
            json.dumps(conflicts or [], ensure_ascii=False),
            datetime.now(UTC).isoformat(), manifest_id,
        ),
    )


def manifest_payload(row: sqlite3.Row) -> dict[str, Any]:
    """返回用户可读的运行清单，不暴露原文和密钥。"""

    item = dict(row)
    for source, target, fallback in (
        ("input_scope_json", "input_scope", {}),
        ("validation_json", "validation", {}),
        ("conflict_json", "conflicts", []),
    ):
        item[target] = parse_json(item.pop(source), fallback)
    return item


def read_registry_file(path: Path) -> dict[str, Any]:
    """读取 JSON 兼容的 YAML 注册表，文件缺失时返回空对象。"""

    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
