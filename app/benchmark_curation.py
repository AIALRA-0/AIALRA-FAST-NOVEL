"""把已有原文证据整理为待人工确认的金标准候选。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.semantic import select_main_subject


def _candidate(
    connection: sqlite3.Connection,
    *,
    book_id: int,
    case_type: str,
    subject: str,
    expected: dict[str, Any],
    source_segment: int,
    note: str,
    critical: bool,
    evidence: list[dict[str, Any]] | None = None,
) -> bool:
    """幂等保存一条候选，候选状态始终保持待人工确认。"""

    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO benchmark_candidates(
            book_id, case_type, subject, expected_json, source_segment, note, critical,
            candidate_origin, evidence_json, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """,
        (
            book_id,
            case_type,
            subject,
            json.dumps(expected, ensure_ascii=False),
            source_segment,
            note,
            int(critical),
            "evidence_index" if evidence else "local_check",
            json.dumps(evidence or [], ensure_ascii=False),
        ),
    )
    # 已存在的待确认候选只补齐缺失引文，不改动人工的处理结论。
    if evidence:
        connection.execute(
            """
            UPDATE benchmark_candidates
            SET candidate_origin = 'evidence_index', evidence_json = ?
            WHERE book_id = ? AND case_type = ? AND subject = ? AND source_segment = ?
              AND status = 'pending' AND evidence_json = '[]'
            """,
            (json.dumps(evidence, ensure_ascii=False), book_id, case_type, subject, source_segment),
        )
    return bool(cursor.rowcount)


def _source_evidence(
    connection: sqlite3.Connection,
    *,
    book_id: int,
    targets: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    """读取最多四条逐字引文，供人工在候选确认前回看。"""

    collected: list[dict[str, Any]] = []
    for target_type, target_id in targets:
        rows = connection.execute(
            """
            SELECT segment.ordinal, segment.chapter_title, evidence.quote
            FROM evidence
            JOIN segments segment ON segment.id = evidence.segment_id
            WHERE evidence.book_id = ? AND evidence.target_type = ? AND evidence.target_id = ?
            ORDER BY segment.ordinal, evidence.quote_start
            LIMIT 2
            """,
            (book_id, target_type, target_id),
        ).fetchall()
        for row in rows:
            collected.append(
                {
                    "target_type": target_type,
                    "target_id": target_id,
                    "segment": int(row["ordinal"]),
                    "chapter_title": str(row["chapter_title"]),
                    "quote": str(row["quote"]),
                }
            )
            if len(collected) >= 4:
                return collected
    return collected


def refresh_benchmark_candidates(connection: sqlite3.Connection, book_id: int) -> dict[str, int]:
    """从已保存事实生成候选，不调用模型，也不把候选计入正式评估。"""

    book = connection.execute(
        "SELECT segment_count FROM books WHERE id = ?", (book_id,)
    ).fetchone()
    if book is None:
        raise ValueError("找不到这本书。")
    last_segment = max(0, int(book["segment_count"] or 1) - 1)
    created = 0

    aliases = connection.execute(
        """
        SELECT entity.id AS entity_id, entity.name, alias.alias, entity.first_segment
        FROM aliases alias JOIN entities entity ON entity.id = alias.entity_id
        WHERE entity.book_id = ?
        ORDER BY entity.importance DESC, entity.first_segment, alias.id
        LIMIT 40
        """,
        (book_id,),
    ).fetchall()
    for row in aliases:
        created += _candidate(
            connection,
            book_id=book_id,
            case_type="identity_same",
            subject=f"{row['name']}={row['alias']}",
            expected={"left": row["name"], "right": row["alias"]},
            source_segment=min(last_segment, int(row["first_segment"])),
            note="候选来自已保存的别名及原文证据，请核对两种称呼是否确指同一实体。",
            critical=True,
            evidence=_source_evidence(
                connection, book_id=book_id, targets=[("entity", int(row["entity_id"]))]
            ),
        )

    events = connection.execute(
        """
        SELECT id, title, first_segment, story_order FROM events
        WHERE book_id = ?
        ORDER BY first_segment, id LIMIT 48
        """,
        (book_id,),
    ).fetchall()
    for row in events:
        created += _candidate(
            connection,
            book_id=book_id,
            case_type="event_present",
            subject=f"事件出现：{row['title']}",
            expected={"title": row["title"], "max_segment": min(last_segment, int(row["first_segment"]))},
            source_segment=min(last_segment, int(row["first_segment"])),
            note="候选来自已保存事件及其原文证据，请核对事件名称和最早章节。",
            critical=True,
            evidence=_source_evidence(
                connection, book_id=book_id, targets=[("event", int(row["id"]))]
            ),
        )
    ordered_events = sorted(events, key=lambda row: (float(row["story_order"]), int(row["first_segment"]), int(row["id"])))
    for earlier, later in zip(ordered_events, ordered_events[1:], strict=False):
        created += _candidate(
            connection,
            book_id=book_id,
            case_type="event_before",
            subject=f"时间顺序：{earlier['title']}早于{later['title']}",
            expected={"earlier": earlier["title"], "later": later["title"]},
            source_segment=min(last_segment, int(later["first_segment"])),
            note="候选来自当前编年排序，请回看两处原文，确认它不是插叙、回忆或并行事件。",
            critical=True,
            evidence=_source_evidence(
                connection,
                book_id=book_id,
                targets=[("event", int(earlier["id"])), ("event", int(later["id"]))],
            ),
        )

    subject_id = select_main_subject(connection, book_id)
    if subject_id is not None:
        subject = connection.execute(
            "SELECT id, name, first_segment FROM entities WHERE id = ?", (subject_id,)
        ).fetchone()
        if subject is not None:
            created += _candidate(
                connection,
                book_id=book_id,
                case_type="main_subject",
                subject=f"主线人物包含{subject['name']}",
                expected={"name": subject["name"]},
                source_segment=min(last_segment, int(subject["first_segment"])),
                note="候选来自当前主线人物评分，请核对全书主线是否确由该人物承载。",
                critical=True,
                evidence=_source_evidence(
                    connection, book_id=book_id, targets=[("entity", int(subject["id"]))]
                ),
            )

    for case_type, subject, expected in (
        ("segment_accounting", "全书片段无缺漏", {"percent": 100}),
        ("fact_evidence", "正式事实全部有证据", {"percent": 100}),
        ("quote_integrity", "证据逐字存在于原文", {"percent": 100}),
    ):
        created += _candidate(
            connection,
            book_id=book_id,
            case_type=case_type,
            subject=subject,
            expected=expected,
            source_segment=last_segment,
            note="候选来自本地确定性核验，确认后会持续作为发布门禁的一部分。",
            critical=False,
        )

    pending = int(connection.execute(
        "SELECT COUNT(*) FROM benchmark_candidates WHERE book_id = ? AND status = 'pending'", (book_id,)
    ).fetchone()[0])
    return {"created": created, "pending": pending}


def candidate_payload(row: sqlite3.Row) -> dict[str, Any]:
    """把数据库行转换为前端可直接展示的候选内容。"""

    item = dict(row)
    item["expected"] = json.loads(str(item.pop("expected_json")))
    item["evidence"] = json.loads(str(item.pop("evidence_json") or "[]"))
    item["critical"] = bool(item["critical"])
    item["accepted_benchmark_id"] = item.get("accepted_benchmark_id")
    return item
