"""Deterministic and reversible story-unit and story-world partitioning."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict, deque
from typing import Any


_GENERIC_CHAPTER = re.compile(
    r"^(?:第\s*[0-9一二三四五六七八九十百千万零〇两]+\s*[章节回卷部篇]|chapter\s+\d+|book\s+\d+)",
    re.IGNORECASE,
)


def _segment_entities(connection: sqlite3.Connection, book_id: int) -> dict[int, set[int]]:
    result: dict[int, set[int]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT segment.ordinal, evidence.target_id AS entity_id
        FROM evidence
        JOIN segments segment ON segment.id = evidence.segment_id
        WHERE evidence.book_id = ? AND evidence.target_type = 'entity'
        UNION
        SELECT segment.ordinal, participant.entity_id
        FROM evidence
        JOIN segments segment ON segment.id = evidence.segment_id
        JOIN event_participants participant ON participant.event_id = evidence.target_id
        WHERE evidence.book_id = ? AND evidence.target_type = 'event'
        """,
        (book_id, book_id),
    ).fetchall():
        result[int(row["ordinal"])].add(int(row["entity_id"]))
    return result


def _entity_kinds(connection: sqlite3.Connection, book_id: int) -> dict[int, str]:
    return {int(row["id"]): str(row["kind"]) for row in connection.execute(
        "SELECT id, kind FROM entities WHERE book_id = ?",
        (book_id,),
    )}


def _components(unit_entities: list[set[int]], unit_places: list[set[int]]) -> list[list[int]]:
    graph: dict[int, set[int]] = defaultdict(set)
    for left in range(len(unit_entities)):
        for right in range(left + 1, len(unit_entities)):
            if unit_entities[left] & unit_entities[right] or unit_places[left] & unit_places[right]:
                graph[left].add(right)
                graph[right].add(left)
    remaining = set(range(len(unit_entities)))
    result: list[list[int]] = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        pending = deque([seed])
        component = []
        while pending:
            current = pending.popleft()
            component.append(current)
            for neighbor in sorted(graph[current]):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        result.append(sorted(component))
    return result


def rebuild_narrative_structure(
    connection: sqlite3.Connection,
    book_id: int,
    force: bool = False,
) -> dict[str, Any]:
    """Create soft partitions only when explicit boundaries and entity resets agree."""

    book = connection.execute("SELECT id, title FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        raise ValueError("找不到这本书")
    preserved = connection.execute(
        "SELECT COUNT(*) FROM narrative_units WHERE book_id = ? AND created_by = 'human'",
        (book_id,),
    ).fetchone()[0]
    if preserved and not force:
        return get_narrative_structure(connection, book_id)
    if force:
        connection.execute("DELETE FROM narrative_unit_links WHERE book_id = ?", (book_id,))
        connection.execute("DELETE FROM narrative_units WHERE book_id = ?", (book_id,))
        connection.execute("DELETE FROM story_worlds WHERE book_id = ?", (book_id,))
    elif connection.execute("SELECT 1 FROM narrative_units WHERE book_id = ? LIMIT 1", (book_id,)).fetchone():
        return get_narrative_structure(connection, book_id)

    segments = [dict(row) for row in connection.execute(
        "SELECT ordinal, chapter_title FROM segments WHERE book_id = ? ORDER BY ordinal",
        (book_id,),
    )]
    if not segments:
        return {"book_id": book_id, "worlds": [], "units": [], "links": [], "scope_options": []}
    entities_by_segment = _segment_entities(connection, book_id)
    kinds = _entity_kinds(connection, book_id)
    boundaries = [0]
    boundary_evidence: dict[int, list[str]] = {}
    for index in range(1, len(segments)):
        previous_entities = entities_by_segment.get(int(segments[index - 1]["ordinal"]), set())
        current_entities = entities_by_segment.get(int(segments[index]["ordinal"]), set())
        explicit_title = bool(str(segments[index]["chapter_title"]).strip()) and not _GENERIC_CHAPTER.match(str(segments[index]["chapter_title"]).strip())
        disconnected = bool(previous_entities and current_entities and not previous_entities & current_entities)
        if explicit_title and disconnected:
            boundaries.append(index)
            boundary_evidence[index] = ["明确的独立标题", "相邻故事没有共享已确认人物或地点"]
    boundaries.append(len(segments))

    unit_specs: list[dict[str, Any]] = []
    for unit_index, (start_index, end_index) in enumerate(zip(boundaries, boundaries[1:])):
        start_ordinal = int(segments[start_index]["ordinal"])
        end_ordinal = int(segments[end_index - 1]["ordinal"])
        title = str(segments[start_index]["chapter_title"] or f"故事 {unit_index + 1}")
        entity_ids = set().union(*(entities_by_segment.get(int(item["ordinal"]), set()) for item in segments[start_index:end_index]))
        unit_specs.append({
            "title": title,
            "start_segment": start_ordinal,
            "end_segment": end_ordinal,
            "entity_ids": entity_ids,
            "place_ids": {entity_id for entity_id in entity_ids if kinds.get(entity_id) == "place"},
            "evidence": boundary_evidence.get(start_index, ["整本书保持为一个连续故事单元"]),
            "confidence": 0.86 if start_index in boundary_evidence else 0.72,
        })
    world_components = _components(
        [set(item["entity_ids"]) for item in unit_specs],
        [set(item["place_ids"]) for item in unit_specs],
    )
    world_id_by_unit: dict[int, int] = {}
    for world_index, unit_indices in enumerate(world_components):
        first_unit = unit_specs[unit_indices[0]]
        world_name = str(book["title"]) if len(world_components) == 1 else str(first_unit["title"])
        cursor = connection.execute(
            """
            INSERT INTO story_worlds(book_id, name, status, confidence, evidence_json, created_by)
            VALUES (?, ?, 'suggested', ?, ?, 'local_partition')
            """,
            (
                book_id,
                world_name,
                min(float(unit_specs[index]["confidence"]) for index in unit_indices),
                json.dumps(["故事单元之间共享已确认人物或地点"] if len(unit_indices) > 1 else first_unit["evidence"], ensure_ascii=False),
            ),
        )
        world_id = int(cursor.lastrowid)
        for unit_index in unit_indices:
            world_id_by_unit[unit_index] = world_id
    unit_ids: list[int] = []
    for unit_index, spec in enumerate(unit_specs):
        cursor = connection.execute(
            """
            INSERT INTO narrative_units(
                book_id, world_id, title, start_segment, end_segment, unit_kind,
                status, confidence, evidence_json, created_by
            ) VALUES (?, ?, ?, ?, ?, 'story', 'suggested', ?, ?, 'local_partition')
            """,
            (
                book_id, world_id_by_unit[unit_index], spec["title"],
                spec["start_segment"], spec["end_segment"], spec["confidence"],
                json.dumps(spec["evidence"], ensure_ascii=False),
            ),
        )
        unit_ids.append(int(cursor.lastrowid))
    for left in range(len(unit_specs)):
        for right in range(left + 1, len(unit_specs)):
            shared = sorted(unit_specs[left]["entity_ids"] & unit_specs[right]["entity_ids"])
            if not shared:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO narrative_unit_links(
                    book_id, source_unit_id, target_unit_id, relation, evidence_json, confidence
                ) VALUES (?, ?, ?, 'shared_entities', ?, 0.9)
                """,
                (book_id, unit_ids[left], unit_ids[right], json.dumps(shared, ensure_ascii=False)),
            )
    return get_narrative_structure(connection, book_id)


def get_narrative_structure(connection: sqlite3.Connection, book_id: int) -> dict[str, Any]:
    entity_by_segment = _segment_entities(connection, book_id)
    kinds = _entity_kinds(connection, book_id)
    events = [dict(row) for row in connection.execute(
        "SELECT id, first_segment FROM events WHERE book_id = ? ORDER BY first_segment, id",
        (book_id,),
    ).fetchall()]
    worlds = []
    for row in connection.execute("SELECT * FROM story_worlds WHERE book_id = ? ORDER BY id", (book_id,)).fetchall():
        item = dict(row)
        item["evidence"] = json.loads(str(item.pop("evidence_json")))
        worlds.append(item)
    units = []
    for row in connection.execute("SELECT * FROM narrative_units WHERE book_id = ? ORDER BY start_segment, id", (book_id,)).fetchall():
        item = dict(row)
        item["evidence"] = json.loads(str(item.pop("evidence_json")))
        ordinals = range(int(item["start_segment"]), int(item["end_segment"]) + 1)
        entity_ids = sorted(set().union(*(entity_by_segment.get(ordinal, set()) for ordinal in ordinals)))
        item["entity_ids"] = entity_ids
        item["place_ids"] = [entity_id for entity_id in entity_ids if kinds.get(entity_id) == "place"]
        item["event_ids"] = [int(event["id"]) for event in events if int(item["start_segment"]) <= int(event["first_segment"]) <= int(item["end_segment"])]
        units.append(item)
    links = []
    for row in connection.execute("SELECT * FROM narrative_unit_links WHERE book_id = ? ORDER BY id", (book_id,)).fetchall():
        item = dict(row)
        item["evidence"] = json.loads(str(item.pop("evidence_json")))
        links.append(item)
    progress_boundaries = [
        {
            "ordinal": int(row["ordinal"]),
            "chapter_title": str(row["chapter_title"] or ""),
        }
        for row in connection.execute(
            "SELECT ordinal, chapter_title FROM segments WHERE book_id = ? ORDER BY ordinal",
            (book_id,),
        ).fetchall()
    ]
    # 普通章节只属于阅读进度，不应伪装成可切换的独立剧情范围；
    # 仅保留有明确边界证据的人工或系统故事单元
    selectable_units = [
        item for item in units
        if str(item.get("unit_kind") or "story") != "chapter"
        and float(item.get("confidence") or 0.0) >= 0.8
        and not (
            len(item.get("evidence") or []) == 1
            and "整本书保持为一个连续故事单元" in str((item.get("evidence") or [""])[0])
        )
    ]
    selectable_world_ids = {int(item["world_id"]) for item in selectable_units if item.get("world_id") is not None}
    # 单一的系统默认世界只是书籍容器，不是一个需要读者切换的剧情范围；
    # 只有存在独立故事证据、人工确认或多个候选世界时才进入范围选择器
    if len(worlds) > 1:
        visible_worlds = worlds
    else:
        visible_worlds = [
            item for item in worlds
            if int(item["id"]) in selectable_world_ids
            or str(item.get("created_by") or "") == "human"
            or str(item.get("status") or "") == "confirmed"
        ]
    return {
        "book_id": book_id,
        "worlds": worlds,
        "units": units,
        "links": links,
        "progress_boundaries": progress_boundaries,
        "scope_options": [
            {"kind": "book", "id": book_id, "label": "整本书"},
            *({"kind": "world", "id": int(item["id"]), "label": str(item["name"])} for item in visible_worlds),
            *({"kind": "unit", "id": int(item["id"]), "label": str(item["title"])} for item in selectable_units),
        ],
    }


def merge_story_worlds(connection: sqlite3.Connection, book_id: int, world_ids: list[int], name: str) -> dict[str, Any]:
    if len(set(world_ids)) < 2:
        raise ValueError("至少选择两个世界分区")
    rows = connection.execute(
        f"SELECT id FROM story_worlds WHERE book_id = ? AND id IN ({','.join('?' for _ in world_ids)})",
        (book_id, *world_ids),
    ).fetchall()
    if len(rows) != len(set(world_ids)):
        raise ValueError("选择的世界分区不属于当前书籍")
    target = min(set(world_ids))
    connection.execute(
        "UPDATE story_worlds SET name = ?, status = 'confirmed', created_by = 'human', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (name.strip() or "合并世界", target),
    )
    for world_id in set(world_ids) - {target}:
        connection.execute("UPDATE narrative_units SET world_id = ?, status = 'confirmed', created_by = 'human' WHERE world_id = ?", (target, world_id))
        connection.execute("DELETE FROM story_worlds WHERE id = ?", (world_id,))
    return get_narrative_structure(connection, book_id)


def split_story_world(
    connection: sqlite3.Connection,
    book_id: int,
    unit_ids: list[int],
    name: str,
) -> dict[str, Any]:
    """Move selected units into a new reversible human-confirmed world."""

    selected = sorted({int(unit_id) for unit_id in unit_ids})
    if not selected:
        raise ValueError("至少选择一个故事单元")
    placeholders = ",".join("?" for _ in selected)
    rows = connection.execute(
        f"SELECT id, world_id FROM narrative_units WHERE book_id = ? AND id IN ({placeholders})",  # noqa: S608
        (book_id, *selected),
    ).fetchall()
    if len(rows) != len(selected):
        raise ValueError("选择的故事单元不属于当前书籍")
    cursor = connection.execute(
        """
        INSERT INTO story_worlds(book_id, name, status, confidence, evidence_json, created_by)
        VALUES (?, ?, 'confirmed', 1, ?, 'human')
        """,
        (book_id, name.strip(), json.dumps(["用户根据阅读结构拆分"], ensure_ascii=False)),
    )
    new_world_id = int(cursor.lastrowid)
    connection.execute(
        f"""
        UPDATE narrative_units
        SET world_id = ?, status = 'confirmed', created_by = 'human', updated_at = CURRENT_TIMESTAMP
        WHERE book_id = ? AND id IN ({placeholders})
        """,  # noqa: S608
        (new_world_id, book_id, *selected),
    )
    connection.execute(
        """
        DELETE FROM story_worlds
        WHERE book_id = ? AND id != ?
          AND NOT EXISTS (SELECT 1 FROM narrative_units WHERE narrative_units.world_id = story_worlds.id)
        """,
        (book_id, new_world_id),
    )
    return get_narrative_structure(connection, book_id)


def move_narrative_unit(connection: sqlite3.Connection, unit_id: int, world_id: int) -> dict[str, Any]:
    unit = connection.execute("SELECT book_id FROM narrative_units WHERE id = ?", (unit_id,)).fetchone()
    world = connection.execute("SELECT book_id FROM story_worlds WHERE id = ?", (world_id,)).fetchone()
    if unit is None or world is None or int(unit["book_id"]) != int(world["book_id"]):
        raise ValueError("故事单元和世界分区不匹配")
    connection.execute(
        "UPDATE narrative_units SET world_id = ?, status = 'confirmed', created_by = 'human', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (world_id, unit_id),
    )
    return get_narrative_structure(connection, int(unit["book_id"]))
