"""低频全书整理，把跨章节推断绑定到已经核验的事实。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.consolidation import consolidate_book
from app.db import connect, transaction
from app.models import GlobalReviewResult
from app.providers import Provider


GLOBAL_REVIEW_VERSION = "global-review-v2-natural-dedup"


@dataclass(frozen=True)
class FactReference:
    """全书整理输入中的稳定键和数据库目标。"""

    key: str
    target_type: str
    target_id: int
    first_segment: int
    line: str


def _fact_references(connection: sqlite3.Connection, book_id: int) -> list[FactReference]:
    """把规范化数据库记录压缩成按章节排列的事实行。"""

    result: list[FactReference] = []
    entities = connection.execute(
        """
        SELECT e.*, GROUP_CONCAT(a.alias, '、') AS aliases
        FROM entities e LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? GROUP BY e.id
        """,
        (book_id,),
    ).fetchall()
    entity_names = {int(item["id"]): item["name"] for item in entities}
    for item in entities:
        key = f"E{item['id']}"
        line = f"{key}|实体|{item['kind']}|{item['name']}|别名:{item['aliases'] or '无'}|{item['summary']}"
        result.append(FactReference(key, "entity", int(item["id"]), int(item["first_segment"]), line))

    claims = connection.execute(
        "SELECT * FROM claims WHERE book_id = ? AND status != 'rejected'",
        (book_id,),
    ).fetchall()
    for item in claims:
        key = f"C{item['id']}"
        line = (
            f"{key}|关系|{entity_names.get(item['source_entity_id'], '?')}"
            f"--{item['predicate']}-->{entity_names.get(item['target_entity_id'], '?')}|{item['summary']}"
        )
        result.append(FactReference(key, "claim", int(item["id"]), int(item["first_segment"]), line))

    place_relations = connection.execute(
        "SELECT * FROM place_relations WHERE book_id = ?",
        (book_id,),
    ).fetchall()
    for item in place_relations:
        key = f"G{item['id']}"
        line = (
            f"{key}|地点方位|{entity_names.get(item['source_entity_id'], '?')}"
            f"--{item['relative_position']}-->{entity_names.get(item['target_entity_id'], '?')}|{item['summary']}"
        )
        result.append(FactReference(key, "place_relation", int(item["id"]), int(item["first_segment"]), line))

    events = connection.execute(
        """
        SELECT v.*, p.name AS location_name FROM events v
        LEFT JOIN entities p ON p.id = v.location_entity_id WHERE v.book_id = ?
        """,
        (book_id,),
    ).fetchall()
    for item in events:
        key = f"V{item['id']}"
        line = (
            f"{key}|事件|故事序:{item['story_order']}|叙事序:{item['narrative_order']}|"
            f"{item['title']}|时间:{item['temporal_value'] or '未知'}|地点:{item['location_name'] or '未知'}|{item['summary']}"
        )
        result.append(FactReference(key, "event", int(item["id"]), int(item["first_segment"]), line))

    notes = connection.execute(
        "SELECT * FROM world_notes WHERE book_id = ? AND created_by != 'synthesis'",
        (book_id,),
    ).fetchall()
    for item in notes:
        key = f"W{item['id']}"
        line = f"{key}|世界事实|{item['category']}|{item['title']}|{item['summary']}"
        result.append(FactReference(key, "world_note", int(item["id"]), int(item["first_segment"]), line))

    entries = connection.execute("SELECT * FROM entries WHERE book_id = ?", (book_id,)).fetchall()
    for item in entries:
        key = f"D{item['id']}"
        line = f"{key}|数据库条目|{item['category']}|{item['name']}|{item['summary']}|属性:{item['attributes_json']}"
        result.append(FactReference(key, "entry", int(item["id"]), int(item["first_segment"]), line))
    return sorted(result, key=lambda item: (item.first_segment, item.key))


def _review_batches(facts: list[FactReference], max_characters: int = 30_000) -> list[str]:
    """按字符上限分批，并为每批附上紧凑的全局实体索引。"""

    entity_index = "\n".join(item.line for item in facts if item.target_type == "entity")[:10_000]
    prefix = f"全局实体索引：\n{entity_index or '暂无'}\n\n本批事实：\n"
    batches: list[str] = []
    current = prefix
    for fact in facts:
        addition = fact.line + "\n"
        if len(current) + len(addition) > max_characters and current != prefix:
            batches.append(current)
            current = prefix
        current += addition
    if current != prefix:
        batches.append(current)
    return batches


def _entity_by_name(connection: sqlite3.Connection, book_id: int, kind: str, name: str) -> sqlite3.Row | None:
    """按规范名或已登记别名查找实体。"""

    return connection.execute(
        """
        SELECT DISTINCT e.* FROM entities e LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? AND e.kind = ? AND (e.name = ? OR a.alias = ?)
        ORDER BY e.first_segment, e.id LIMIT 1
        """,
        (book_id, kind, name, name),
    ).fetchone()


def persist_global_review(
    connection: sqlite3.Connection,
    book_id: int,
    result: GlobalReviewResult,
    facts: dict[str, FactReference],
) -> None:
    """保存综合说明和建议，所有综合说明至少绑定一条原始事实证据。"""

    for synthesis in result.syntheses:
        bases = [facts[key] for key in synthesis.basis_keys if key in facts]
        if not bases:
            continue
        first_segment = max(item.first_segment for item in bases)
        connection.execute(
            """
            INSERT OR IGNORE INTO world_notes(
                book_id, category, title, summary, confidence, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, 'synthesis')
            """,
            (
                book_id, synthesis.category, synthesis.title,
                synthesis.summary, synthesis.confidence, first_segment,
            ),
        )
        note = connection.execute(
            """
            SELECT id FROM world_notes
            WHERE book_id = ? AND category = ? AND title = ? AND first_segment = ?
            """,
            (book_id, synthesis.category, synthesis.title, first_segment),
        ).fetchone()
        if note is None:
            continue
        note_id = int(note["id"])
        for basis in bases:
            connection.execute(
                """
                INSERT OR IGNORE INTO synthesis_basis(world_note_id, basis_type, basis_id)
                VALUES (?, ?, ?)
                """,
                (note_id, basis.target_type, basis.target_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence(
                    book_id, target_type, target_id, segment_id, quote, quote_start, quote_end
                )
                SELECT book_id, 'world_note', ?, segment_id, quote, quote_start, quote_end
                FROM evidence WHERE target_type = ? AND target_id = ?
                """,
                (note_id, basis.target_type, basis.target_id),
            )

    for suggestion in result.merge_suggestions:
        left = _entity_by_name(connection, book_id, suggestion.kind, suggestion.left_name)
        right = _entity_by_name(connection, book_id, suggestion.kind, suggestion.right_name)
        if left is None or right is None or left["id"] == right["id"]:
            continue
        left_id, right_id = sorted((int(left["id"]), int(right["id"])))
        connection.execute(
            """
            INSERT OR IGNORE INTO entity_merge_candidates(
                book_id, left_entity_id, right_entity_id, reason, confidence
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (book_id, left_id, right_id, suggestion.reason, suggestion.confidence),
        )

    for suggestion in result.order_suggestions:
        earlier = connection.execute(
            "SELECT id FROM events WHERE book_id = ? AND title = ? ORDER BY first_segment, id LIMIT 1",
            (book_id, suggestion.earlier_event_title),
        ).fetchone()
        later = connection.execute(
            "SELECT id FROM events WHERE book_id = ? AND title = ? ORDER BY first_segment, id LIMIT 1",
            (book_id, suggestion.later_event_title),
        ).fetchone()
        if earlier is None or later is None or earlier["id"] == later["id"]:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO event_order_edges(
                book_id, earlier_event_id, later_event_id, relation, confidence, created_by
            ) VALUES (?, ?, ?, 'global_review', ?, 'synthesis')
            """,
            (book_id, earlier["id"], later["id"], suggestion.confidence),
        )

    for contradiction in result.contradictions:
        left = facts.get(contradiction.left_key)
        right = facts.get(contradiction.right_key)
        if left is None or right is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO contradictions(
                book_id, left_type, left_id, right_type, right_id, summary, confidence
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, left.target_type, left.target_id, right.target_type, right.target_id,
                contradiction.summary, contradiction.confidence,
            ),
        )

    if result.protagonist_name:
        protagonist = _entity_by_name(connection, book_id, "person", result.protagonist_name)
        existing = connection.execute("SELECT * FROM book_settings WHERE book_id = ?", (book_id,)).fetchone()
        if protagonist is not None and (existing is None or existing["auto_protagonist"]):
            connection.execute(
                """
                INSERT INTO book_settings(book_id, protagonist_entity_id, auto_protagonist, updated_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(book_id) DO UPDATE SET
                    protagonist_entity_id = excluded.protagonist_entity_id,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (book_id, protagonist["id"]),
            )


async def review_book(settings: Settings, provider: Provider, book_id: int) -> dict[str, int]:
    """分批执行全书整理，成功批次按内容哈希复用。"""

    if provider.name == "mock":
        return {
            "batches": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hit_input_tokens": 0,
            "cache_miss_input_tokens": 0,
        }
    with connect(settings.database_path) as connection:
        fact_list = _fact_references(connection, book_id)
    if not fact_list:
        return {
            "batches": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_hit_input_tokens": 0,
            "cache_miss_input_tokens": 0,
        }
    fact_map = {item.key: item for item in fact_list}
    batches = _review_batches(fact_list)
    completed = 0
    input_tokens = 0
    output_tokens = 0
    cache_hit_input_tokens = 0
    cache_miss_input_tokens = 0
    for batch in batches:
        batch_hash = hashlib.sha256(batch.encode("utf-8")).hexdigest()
        with transaction(settings.database_path) as connection:
            cached = connection.execute(
                """
                SELECT * FROM global_review_batches
                WHERE book_id = ? AND batch_hash = ? AND provider = ? AND model = ?
                  AND prompt_version = ? AND status = 'completed'
                """,
                (book_id, batch_hash, provider.name, provider.model, GLOBAL_REVIEW_VERSION),
            ).fetchone()
            if cached is not None:
                completed += 1
                input_tokens += int(cached["input_tokens"] or 0)
                output_tokens += int(cached["output_tokens"] or 0)
                cache_hit_input_tokens += int(cached["cache_hit_input_tokens"] or 0)
                cache_miss_input_tokens += int(cached["cache_miss_input_tokens"] or 0)
                continue
            connection.execute(
                """
                INSERT INTO global_review_batches(
                    book_id, batch_hash, provider, model, prompt_version, status
                ) VALUES (?, ?, ?, ?, ?, 'running')
                ON CONFLICT(book_id, batch_hash, provider, model, prompt_version)
                DO UPDATE SET status = 'running', error = ''
                """,
                (book_id, batch_hash, provider.name, provider.model, GLOBAL_REVIEW_VERSION),
            )
        try:
            response = await provider.review_knowledge(batch)
            with transaction(settings.database_path) as connection:
                persist_global_review(connection, book_id, response.result, fact_map)
                consolidate_book(connection, book_id, max(item.first_segment for item in fact_list))
                connection.execute(
                    """
                    UPDATE global_review_batches SET status = 'completed', input_tokens = ?,
                        output_tokens = ?, cache_hit_input_tokens = ?, cache_miss_input_tokens = ?,
                        completed_at = ?, error = ''
                    WHERE book_id = ? AND batch_hash = ? AND provider = ? AND model = ?
                      AND prompt_version = ?
                    """,
                    (
                        response.input_tokens, response.output_tokens,
                        response.cache_hit_input_tokens, response.cache_miss_input_tokens,
                        datetime.now(timezone.utc).isoformat(),
                        book_id, batch_hash, provider.name, provider.model, GLOBAL_REVIEW_VERSION,
                    ),
                )
            completed += 1
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            cache_hit_input_tokens += response.cache_hit_input_tokens
            cache_miss_input_tokens += response.cache_miss_input_tokens
        except Exception as exc:
            with connect(settings.database_path) as connection:
                connection.execute(
                    """
                    UPDATE global_review_batches SET status = 'failed', error = ?
                    WHERE book_id = ? AND batch_hash = ? AND provider = ? AND model = ?
                      AND prompt_version = ?
                    """,
                    (
                        str(exc)[:600], book_id, batch_hash, provider.name, provider.model,
                        GLOBAL_REVIEW_VERSION,
                    ),
                )
            raise
    return {
        "batches": completed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_hit_input_tokens": cache_hit_input_tokens,
        "cache_miss_input_tokens": cache_miss_input_tokens,
    }
