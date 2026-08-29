"""在分析结果发布前执行可追溯的关系和地点完整性复审。"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import defaultdict
from typing import Any

from app.config import Settings
from app.cost_control import adaptive_budget_limits
from app.db import connect, transaction
from app.models import ConnectivityAuditDecision, ConnectivityAuditResult
from app.pipeline import add_evidence, find_quote
from app.pricing import calculate_cost_usd, pricing_for
from app.providers import Provider, ProviderError
from app.relations import normalize_relation_semantics


QUALITY_AUDIT_VERSION = "connectivity-audit-v2-shared-windows"
WINDOW_RADIUS = 240
MAX_WINDOWS_PER_PART = 48
MAX_TARGETS_PER_PART = 10
MAX_PAYLOAD_CHARACTERS = 48_000
EXPLICIT_RELATION_WORDS = "徒弟|弟子|师父|師父|父亲|父親|母亲|母親|丈夫|妻子|哥哥|姐姐|弟弟|妹妹|儿子|兒子|女儿|女兒"


def _entity_names(connection: sqlite3.Connection, book_id: int) -> dict[int, list[str]]:
    """返回人物和势力的规范名及别名，较长名称优先匹配。"""

    rows = connection.execute(
        """
        SELECT e.id, e.name, a.alias
        FROM entities e LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? AND e.kind IN ('person', 'faction')
        ORDER BY e.id
        """,
        (book_id,),
    ).fetchall()
    result: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        result[int(row["id"])].add(str(row["name"]).strip())
        if row["alias"]:
            result[int(row["id"])].add(str(row["alias"]).strip())
    return {
        entity_id: sorted((name for name in names if name), key=lambda value: (-len(value), value))
        for entity_id, names in result.items()
    }


def _multi_entity_windows(
    connection: sqlite3.Connection,
    book_id: int,
    names_by_entity: dict[int, list[str]],
    analyzed_ordinals: set[int] | None = None,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, int]]]:
    """单次扫描全部目标名称，并合并同一段内互相重叠的证据窗口。"""

    segments = connection.execute(
        "SELECT id, ordinal, chapter_title, text FROM segments WHERE book_id = ? ORDER BY ordinal",
        (book_id,),
    ).fetchall()
    if analyzed_ordinals is not None:
        segments = [segment for segment in segments if int(segment["ordinal"]) in analyzed_ordinals]
    windows: list[dict[str, Any]] = []
    stats = {
        entity_id: {"mention_count": 0, "scanned_segment_count": 0, "window_count": 0}
        for entity_id in names_by_entity
    }
    evidence_by_segment: dict[int, list[tuple[int, str]]] = defaultdict(list)
    if names_by_entity:
        placeholders = ",".join("?" for _ in names_by_entity)
        evidence_rows = connection.execute(
            f"""
            SELECT target_id, segment_id, quote FROM evidence
            WHERE book_id = ? AND target_type = 'entity'
              AND target_id IN ({placeholders})
            ORDER BY segment_id, id
            """,  # noqa: S608
            (book_id, *names_by_entity.keys()),
        ).fetchall()
        for evidence in evidence_rows:
            evidence_by_segment[int(evidence["segment_id"])].append(
                (int(evidence["target_id"]), str(evidence["quote"]))
            )
    for segment in segments:
        segment_text = str(segment["text"])
        intervals: list[tuple[int, int, int]] = []
        entities_in_segment: set[int] = set()
        for entity_id, names in names_by_entity.items():
            entity_matches: list[tuple[int, int]] = []
            for name in names:
                if name:
                    entity_matches.extend(
                        (match.start(), match.end())
                        for match in re.finditer(re.escape(name), segment_text)
                    )
            if not entity_matches:
                continue
            entities_in_segment.add(entity_id)
            stats[entity_id]["mention_count"] += len(entity_matches)
            for start, end in entity_matches:
                intervals.append((max(0, start - WINDOW_RADIUS), min(len(segment_text), end + WINDOW_RADIUS), entity_id))
        # “某人的母亲”等描述性实体名称可能不逐字出现；实体自身的已验证证据仍必须进入复审。
        evidence_mentions: set[tuple[int, int, int]] = set()
        for entity_id, quote in evidence_by_segment.get(int(segment["id"]), []):
            quote_start = segment_text.find(quote)
            if quote_start < 0:
                continue
            quote_end = quote_start + len(quote)
            evidence_mentions.add((entity_id, quote_start, quote_end))
        for entity_id, start, end in evidence_mentions:
            if entity_id not in entities_in_segment:
                stats[entity_id]["mention_count"] += 1
            entities_in_segment.add(entity_id)
            intervals.append((max(0, start - WINDOW_RADIUS), min(len(segment_text), end + WINDOW_RADIUS), entity_id))
        for entity_id in entities_in_segment:
            stats[entity_id]["scanned_segment_count"] += 1
        if not intervals:
            continue

        # 同一片段内互相覆盖的窗口合并，目标编号随窗口一起传给模型。
        intervals.sort(key=lambda item: (item[0], item[1], item[2]))
        merged: list[tuple[int, int, set[int]]] = []
        for start, end, entity_id in intervals:
            if merged and start <= merged[-1][1]:
                previous_start, previous_end, previous_entities = merged[-1]
                previous_entities.add(entity_id)
                merged[-1] = (previous_start, max(previous_end, end), previous_entities)
            else:
                merged.append((start, end, {entity_id}))
        for window_start, window_end, target_ids in merged:
            for entity_id in target_ids:
                stats[entity_id]["window_count"] += 1
            windows.append(
                {
                    "segment_id": int(segment["id"]),
                    "ordinal": int(segment["ordinal"]),
                    "chapter_title": str(segment["chapter_title"]),
                    "target_entity_ids": sorted(target_ids),
                    "text": segment_text[window_start:window_end],
                }
            )
    return windows, stats


def _mention_windows(
    connection: sqlite3.Connection,
    book_id: int,
    names: list[str],
) -> tuple[list[dict[str, Any]], int, int]:
    """兼容单实体调用，并复用合并窗口扫描器。"""

    windows, stats = _multi_entity_windows(connection, book_id, {0: names})
    entity_stats = stats[0]
    return windows, entity_stats["mention_count"], entity_stats["scanned_segment_count"]


def repair_explicit_named_relations(connection: sqlite3.Connection, book_id: int) -> int:
    """补回“甲是乙的徒弟”这类逐字明确、但乙漏抽取的关系。"""

    entity_rows = connection.execute(
        """
        SELECT entity.id, entity.name, entity.summary, alias.alias
        FROM entities entity LEFT JOIN aliases alias ON alias.entity_id = entity.id
        WHERE entity.book_id = ? AND entity.kind IN ('person', 'faction')
        """,
        (book_id,),
    ).fetchall()
    names_by_id: dict[int, set[str]] = defaultdict(set)
    name_to_id: dict[str, int] = {}
    canonical_by_id: dict[int, str] = {}
    summary_by_id: dict[int, str] = {}
    for row in entity_rows:
        entity_id = int(row["id"])
        canonical_by_id[entity_id] = str(row["name"])
        summary_by_id[entity_id] = str(row["summary"])
        for value in (row["name"], row["alias"]):
            name = str(value or "").strip()
            if name:
                names_by_id[entity_id].add(name)
                name_to_id.setdefault(name, entity_id)
    evidence_rows = connection.execute(
        """
        SELECT evidence_row.target_id, evidence_row.segment_id, evidence_row.quote,
            segment.ordinal, segment.text
        FROM evidence evidence_row JOIN segments segment ON segment.id = evidence_row.segment_id
        WHERE evidence_row.book_id = ? AND evidence_row.target_type = 'entity'
        ORDER BY segment.ordinal, evidence_row.id
        """,
        (book_id,),
    ).fetchall()
    repaired = 0
    for row in evidence_rows:
        entity_id = int(row["target_id"])
        quote = str(row["quote"])
        source_text = str(row["text"])
        if find_quote(source_text, quote) is None:
            continue
        subject_names = set(names_by_id.get(entity_id, ()))
        canonical_name = canonical_by_id.get(entity_id, "")
        summary_match = re.search(
            rf"{re.escape(canonical_name)}是([^，。；：！？“”「」\n]{{2,20}}?)的({EXPLICIT_RELATION_WORDS})",
            summary_by_id.get(entity_id, ""),
        ) if canonical_name else None
        evidence_match = re.search(
            rf"([\u3400-\u9fff·]{{2,20}}?)是([^，。；：！？“”「」\n]{{2,20}}?)的({EXPLICIT_RELATION_WORDS})",
            quote,
        )
        if summary_match is not None and evidence_match is not None:
            summary_relation = summary_match.group(2).replace("師", "师").replace("親", "亲").replace("兒", "儿")
            evidence_relation = evidence_match.group(3).replace("師", "师").replace("親", "亲").replace("兒", "儿")
            if summary_match.group(1) == evidence_match.group(2) and summary_relation == evidence_relation:
                subject_names.add(evidence_match.group(1))
        for subject_name in sorted(subject_names, key=lambda value: -len(value)):
            match = re.search(
                rf"{re.escape(subject_name)}是([^，。；：！？“”「」\n]{{2,20}}?)的({EXPLICIT_RELATION_WORDS})",
                quote,
            )
            if match is None:
                continue
            other_name = match.group(1).strip()
            predicate = match.group(2).replace("師", "师").replace("親", "亲").replace("兒", "儿")
            if not re.fullmatch(r"[\u3400-\u9fff·]{2,20}", other_name):
                continue
            other_id = name_to_id.get(other_name)
            if other_id is None:
                other_id = int(connection.execute(
                    """
                    INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
                    VALUES (?, 'person', ?, ?, 0.4, ?, 'quality_review')
                    """,
                    (
                        book_id, other_name,
                        f"原文明确记载其与{subject_name}存在{predicate}关系。",
                        int(row["ordinal"]),
                    ),
                ).lastrowid)
                name_to_id[other_name] = other_id
                names_by_id[other_id].add(other_name)
                add_evidence(
                    connection, book_id, "entity", other_id, int(row["segment_id"]),
                    source_text, quote,
                )
            if other_id == entity_id:
                continue
            directionality, reverse_predicate = normalize_relation_semantics(predicate)
            connection.execute(
                """
                INSERT OR IGNORE INTO claims(
                    book_id, source_entity_id, target_entity_id, predicate, directionality,
                    reverse_predicate, summary, confidence, status, first_segment, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'accepted', ?, 'quality_review')
                """,
                (
                    book_id, entity_id, other_id, predicate, directionality, reverse_predicate,
                    f"原文明确记载{subject_name}是{other_name}的{predicate}。",
                    int(row["ordinal"]),
                ),
            )
            claim = connection.execute(
                """
                SELECT id FROM claims WHERE book_id = ? AND source_entity_id = ?
                  AND target_entity_id = ? AND predicate = ? AND first_segment = ?
                ORDER BY id LIMIT 1
                """,
                (book_id, entity_id, other_id, predicate, int(row["ordinal"])),
            ).fetchone()
            if claim is not None and add_evidence(
                connection, book_id, "claim", int(claim["id"]), int(row["segment_id"]),
                source_text, quote,
            ):
                repaired += 1
            break
    return repaired


def repair_explicit_event_locations(connection: sqlite3.Connection, book_id: int) -> int:
    """用事件标题或摘要点名且原文章节逐字存在的唯一地点补齐位置。"""

    place_names: dict[int, set[str]] = defaultdict(set)
    for row in connection.execute(
        """
        SELECT entity.id,
            COALESCE(cluster.canonical_entity_id, entity.id) AS canonical_id,
            entity.name, alias.alias
        FROM entities entity LEFT JOIN aliases alias ON alias.entity_id = entity.id
        LEFT JOIN identity_cluster_members member ON member.entity_id = entity.id
        LEFT JOIN identity_clusters cluster ON cluster.id = member.cluster_id AND cluster.status = 'active'
        WHERE entity.book_id = ? AND entity.kind = 'place'
        """,
        (book_id,),
    ).fetchall():
        place_names[int(row["canonical_id"])].add(str(row["name"]).strip())
        if row["alias"]:
            place_names[int(row["canonical_id"])].add(str(row["alias"]).strip())
    repaired = 0
    events = connection.execute(
        "SELECT id, title, summary FROM events WHERE book_id = ? AND location_entity_id IS NULL",
        (book_id,),
    ).fetchall()
    for event in events:
        evidence_rows = connection.execute(
            """
            SELECT evidence_row.quote, segment.id AS segment_id, segment.text
            FROM evidence evidence_row JOIN segments segment ON segment.id = evidence_row.segment_id
            WHERE evidence_row.book_id = ? AND evidence_row.target_type = 'event'
              AND evidence_row.target_id = ?
            ORDER BY segment.ordinal, evidence_row.id
            """,
            (book_id, event["id"]),
        ).fetchall()
        event_text = f"{event['title']}\n{event['summary']}"
        evidence_text = "\n".join(str(row["quote"]) for row in evidence_rows)
        event_candidates = {
            place_id
            for place_id, names in place_names.items()
            if any(len(name) >= 2 and name in event_text for name in names)
        }
        evidence_candidates = {
            place_id
            for place_id, names in place_names.items()
            if any(len(name) >= 2 and name in evidence_text for name in names)
        }
        named_candidates = event_candidates or evidence_candidates
        if len(named_candidates) != 1:
            continue
        place_id = next(iter(named_candidates))
        matched_segment: sqlite3.Row | None = None
        matched_name = ""
        matched_quote = ""
        for segment in evidence_rows:
            for name in sorted(place_names[place_id], key=lambda value: (-len(value), value)):
                if len(name) >= 2 and name in str(segment["quote"]) and name in str(segment["text"]):
                    matched_segment = segment
                    matched_name = name
                    matched_quote = str(segment["quote"])
                    break
            if matched_segment is not None:
                break
        if matched_segment is None and place_id in event_candidates:
            for segment in evidence_rows:
                source_text = str(segment["text"])
                for name in sorted(place_names[place_id], key=lambda value: (-len(value), value)):
                    name_start = source_text.find(name)
                    if len(name) < 2 or name_start < 0:
                        continue
                    quote_start = max(0, name_start - 60)
                    quote_end = min(len(source_text), name_start + len(name) + 100)
                    matched_segment = segment
                    matched_name = name
                    matched_quote = source_text[quote_start:quote_end]
                    break
                if matched_segment is not None:
                    break
        if matched_segment is None:
            continue
        source_text = str(matched_segment["text"])
        quote = matched_quote
        connection.execute(
            "UPDATE events SET location_entity_id = ? WHERE id = ?",
            (place_id, event["id"]),
        )
        add_evidence(
            connection, book_id, "event", int(event["id"]), int(matched_segment["segment_id"]),
            source_text, quote,
        )
        repaired += 1
    return repaired


def refresh_local_reviews(
    connection: sqlite3.Connection,
    book_id: int,
    analyzed_ordinals: set[int] | None = None,
) -> dict[str, int]:
    """用数据库事实建立初始复审状态，并给无地点事件记录可解释的沿用位置。"""

    repaired_relations = repair_explicit_named_relations(connection, book_id)
    repaired_locations = repair_explicit_event_locations(connection, book_id)
    # 上次因为预算中断而没有收齐全部分片的节点应继续自动复审，不能伪装成内容歧义。
    connection.execute(
        """
        UPDATE entity_connectivity_reviews
        SET status = 'pending', reason = '上次复审未覆盖全部分片，等待从缓存继续。',
            updated_at = CURRENT_TIMESTAMP
        WHERE book_id = ? AND status = 'ambiguous'
          AND reason = '专项复审没有返回全部分片的裁定，节点需要重试或人工处理。'
        """,
        (book_id,),
    )
    names_by_entity = _entity_names(connection, book_id)
    connected_ids = {
        int(row["entity_id"])
        for row in connection.execute(
            """
            SELECT source_entity_id AS entity_id FROM claims
            WHERE book_id = ? AND status != 'rejected'
            UNION
            SELECT target_entity_id AS entity_id FROM claims
            WHERE book_id = ? AND status != 'rejected'
            """,
            (book_id, book_id),
        ).fetchall()
        if row["entity_id"] is not None
    }
    source_segment_count = (
        len(analyzed_ordinals)
        if analyzed_ordinals is not None
        else int(connection.execute(
            "SELECT COUNT(*) FROM segments WHERE book_id = ?",
            (book_id,),
        ).fetchone()[0])
    )
    existing_by_entity = {
        int(row["entity_id"]): row
        for row in connection.execute(
            "SELECT * FROM entity_connectivity_reviews WHERE book_id = ?",
            (book_id,),
        ).fetchall()
    }
    scan_names: dict[int, list[str]] = {}
    for entity_id, names in names_by_entity.items():
        existing = existing_by_entity.get(entity_id)
        can_reuse_scan = (
            existing is not None
            and int(existing["source_segment_count"] or 0) == source_segment_count
            and str(existing["status"]) in {"connected", "confirmed_isolated", "ambiguous", "pending"}
        )
        if entity_id not in connected_ids and not can_reuse_scan:
            scan_names[entity_id] = names
    _, scan_stats = (
        _multi_entity_windows(connection, book_id, scan_names, analyzed_ordinals)
        if scan_names else ([], {})
    )
    pending = 0
    for entity_id, names in names_by_entity.items():
        existing = existing_by_entity.get(entity_id)
        can_reuse_scan = (
            existing is not None
            and int(existing["source_segment_count"] or 0) == source_segment_count
            and str(existing["status"]) in {"connected", "confirmed_isolated", "ambiguous", "pending"}
        )
        if entity_id in connected_ids or can_reuse_scan:
            window_count = 0
            mention_count = int(existing["mention_count"] or 0) if existing is not None else 0
            scanned_segments = int(existing["scanned_segment_count"] or 0) if existing is not None else 0
        else:
            entity_stats = scan_stats.get(
                entity_id,
                {"mention_count": 0, "scanned_segment_count": 0, "window_count": 0},
            )
            window_count = int(entity_stats["window_count"])
            mention_count = int(entity_stats["mention_count"])
            scanned_segments = int(entity_stats["scanned_segment_count"])
        if entity_id in connected_ids:
            status = "connected"
            method = "deterministic"
            confidence = 1.0
            reason = "至少一条未被拒绝的关系事实已经连接该节点。"
        elif existing is not None and str(existing["status"]) == "pending" and can_reuse_scan:
            status = "pending"
            method = str(existing["review_method"])
            confidence = float(existing["confidence"])
            reason = str(existing["reason"])
        elif (
            existing is not None
            and str(existing["review_method"]) in {"human", "model"}
            and str(existing["status"]) in {"confirmed_isolated", "ambiguous"}
            and can_reuse_scan
        ):
            status = str(existing["status"])
            method = str(existing["review_method"])
            confidence = float(existing["confidence"])
            reason = str(existing["reason"])
        else:
            status = "pending" if window_count else "ambiguous"
            method = "deterministic"
            confidence = 0.0
            reason = (
                "等待专项模型复审全部提及窗口。"
                if window_count
                else "原文中找不到规范名或别名，可能是误抽取或名称尚未统一。"
            )
        if status == "pending":
            pending += 1
        connection.execute(
            """
            INSERT INTO entity_connectivity_reviews(
                book_id, entity_id, status, mention_count, scanned_segment_count, source_segment_count,
                confidence, reason, review_method, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(book_id, entity_id) DO UPDATE SET
                status = excluded.status,
                mention_count = excluded.mention_count,
                scanned_segment_count = excluded.scanned_segment_count,
                source_segment_count = excluded.source_segment_count,
                confidence = excluded.confidence,
                reason = excluded.reason,
                review_method = excluded.review_method,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                book_id, entity_id, status, mention_count, scanned_segments, source_segment_count,
                confidence, reason, method,
            ),
        )

    connection.execute(
        "DELETE FROM entity_connectivity_reviews WHERE book_id = ? AND entity_id NOT IN (SELECT id FROM entities WHERE book_id = ?)",
        (book_id, book_id),
    )
    previous_location: int | None = None
    location_counts = defaultdict(int)
    events = connection.execute(
        """
        SELECT id, location_entity_id FROM events
        WHERE book_id = ? ORDER BY story_order, narrative_order, id
        """,
        (book_id,),
    ).fetchall()
    for event in events:
        event_id = int(event["id"])
        if event["location_entity_id"] is not None:
            previous_location = int(event["location_entity_id"])
            status = "explicit"
            reason = "事件记录带有逐字证据支持的明确地点。"
        elif previous_location is not None:
            status = "inherited"
            reason = "原文没有再次点名地点，界面沿用此前最后一个明确地点且不生成移动。"
        else:
            status = "unresolved"
            reason = "此前没有可核验地点，地图保持未知。"
        location_counts[status] += 1
        connection.execute(
            """
            INSERT INTO event_location_reviews(event_id, book_id, status, effective_location_entity_id, reason, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(event_id) DO UPDATE SET
                status = excluded.status,
                effective_location_entity_id = excluded.effective_location_entity_id,
                reason = excluded.reason,
                updated_at = CURRENT_TIMESTAMP
            """,
            (event_id, book_id, status, previous_location, reason),
        )
    return {
        "connectivity_pending": pending,
        "relations_repaired": repaired_relations,
        "locations_repaired": repaired_locations,
        "locations_explicit": location_counts["explicit"],
        "locations_inherited": location_counts["inherited"],
        "locations_unresolved": location_counts["unresolved"],
    }


def _payload_parts(
    connection: sqlite3.Connection,
    book_id: int,
    statuses: tuple[str, ...] = ("pending",),
    analyzed_ordinals: set[int] | None = None,
) -> list[dict[str, Any]]:
    """一次扫描全部待审实体，共享重叠证据并拆成可控请求。"""

    names_by_entity = _entity_names(connection, book_id)
    entity_rows = {
        int(row["id"]): dict(row)
        for row in connection.execute(
            "SELECT id, kind, name, summary FROM entities WHERE book_id = ? AND kind IN ('person', 'faction')",
            (book_id,),
        ).fetchall()
    }
    placeholders = ",".join("?" for _ in statuses)
    pending_ids = [
        int(row["entity_id"])
        for row in connection.execute(
            f"SELECT entity_id FROM entity_connectivity_reviews WHERE book_id = ? AND status IN ({placeholders}) ORDER BY entity_id",  # noqa: S608
            (book_id, *statuses),
        ).fetchall()
    ]
    target_names = {entity_id: names_by_entity.get(entity_id, []) for entity_id in pending_ids}
    windows, _ = _multi_entity_windows(connection, book_id, target_names, analyzed_ordinals)
    parts: list[dict[str, Any]] = []
    current_windows: list[dict[str, Any]] = []
    current_target_ids: set[int] = set()

    def flush() -> None:
        if not current_windows:
            return
        searchable = " ".join(str(window["text"]) for window in current_windows)
        relevant_ids = set(current_target_ids)
        for other_id, aliases in names_by_entity.items():
            if any(alias and alias in searchable for alias in aliases):
                relevant_ids.add(other_id)
        parts.append(
            {
                "target_entities": [
                    {
                        "entity_id": entity_id,
                        "name": entity_rows[entity_id]["name"],
                        "kind": entity_rows[entity_id]["kind"],
                        "summary": entity_rows[entity_id]["summary"],
                        "aliases": names_by_entity.get(entity_id, []),
                    }
                    for entity_id in sorted(current_target_ids)
                ],
                "entity_index": [
                    {
                        "entity_id": entity_id,
                        "name": entity_rows[entity_id]["name"],
                        "kind": entity_rows[entity_id]["kind"],
                        "aliases": names_by_entity.get(entity_id, []),
                    }
                    for entity_id in sorted(relevant_ids)
                    if entity_id in entity_rows
                ],
                "mention_windows": list(current_windows),
            }
        )

    for window in windows:
        window_targets = {int(entity_id) for entity_id in window.get("target_entity_ids", [])}
        candidate_targets = current_target_ids | window_targets
        candidate_windows = [*current_windows, window]
        approximate_size = len(json.dumps(candidate_windows, ensure_ascii=False))
        exceeds_limit = (
            len(candidate_windows) > MAX_WINDOWS_PER_PART
            or approximate_size > MAX_PAYLOAD_CHARACTERS
            or len(candidate_targets) > MAX_TARGETS_PER_PART
        )
        if current_windows and exceeds_limit:
            flush()
            current_windows.clear()
            current_target_ids.clear()
        current_windows.append(window)
        current_target_ids.update(window_targets)
    flush()

    # 每个目标可能跨多个请求；明确分片总数，防止模型只审第一批就宣称孤立。
    part_counts: dict[int, int] = defaultdict(int)
    for part in parts:
        for target in part["target_entities"]:
            part_counts[int(target["entity_id"])] += 1
    part_indexes: dict[int, int] = defaultdict(int)
    for part in parts:
        for target in part["target_entities"]:
            entity_id = int(target["entity_id"])
            part_indexes[entity_id] += 1
            target["part"] = part_indexes[entity_id]
            target["part_count"] = part_counts[entity_id]
    return parts


def _group_parts(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """共享窗口扫描器已经完成分组，这里保留稳定接口。"""

    return parts


def _split_payload_targets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """只拆分触及输出上限的目标清单，每个目标仍保留自己的全部证据窗口。"""

    targets = list(payload["target_entities"])
    if len(targets) < 2:
        return []
    midpoint = max(1, len(targets) // 2)
    result: list[dict[str, Any]] = []
    for target_part in (targets[:midpoint], targets[midpoint:]):
        target_ids = {int(target["entity_id"]) for target in target_part}
        windows: list[dict[str, Any]] = []
        for window in payload["mention_windows"]:
            matched_ids = [
                int(entity_id)
                for entity_id in window.get("target_entity_ids", [])
                if int(entity_id) in target_ids
            ]
            if not matched_ids:
                continue
            windows.append({**window, "target_entity_ids": matched_ids})
        result.append(
            {
                "target_entities": target_part,
                "entity_index": payload["entity_index"],
                "mention_windows": windows,
            }
        )
    return result


def _single_target_length_fallback(payload: dict[str, Any]) -> ConnectivityAuditResult | None:
    """把无法继续拆分的长度失败转成明确歧义，不让任务永久悬挂。"""

    targets = list(payload.get("target_entities") or [])
    if len(targets) != 1:
        return None
    return ConnectivityAuditResult(
        decisions=[
            ConnectivityAuditDecision(
                entity_id=int(targets[0]["entity_id"]),
                status="ambiguous",
                reason="模型输出达到长度上限，单项复审仍无法形成完整裁定。",
                confidence=0.0,
                relations=[],
            )
        ]
    )


def _persist_audit_decisions(
    connection: sqlite3.Connection,
    book_id: int,
    parts_by_entity: dict[int, int],
    decisions: dict[int, list[Any]],
) -> None:
    """逐字验证模型找到的关系，只有全部分片完成后才确认孤立。"""

    entity_rows = connection.execute(
        "SELECT id, name FROM entities WHERE book_id = ? AND kind IN ('person', 'faction')",
        (book_id,),
    ).fetchall()
    entity_ids_by_name: dict[str, list[int]] = defaultdict(list)
    for row in entity_rows:
        entity_ids_by_name[str(row["name"])].append(int(row["id"]))
    segments = {
        int(row["id"]): row
        for row in connection.execute("SELECT id, ordinal, text FROM segments WHERE book_id = ?", (book_id,)).fetchall()
    }
    for entity_id, expected_parts in parts_by_entity.items():
        entity_decisions = decisions.get(entity_id, [])
        relation_ids: list[int] = []
        evidence_summary: list[dict[str, Any]] = []
        for decision in entity_decisions:
            for relation in decision.relations:
                source_candidates = entity_ids_by_name.get(relation.source, [])
                target_candidates = entity_ids_by_name.get(relation.target, [])
                if entity_id in source_candidates:
                    source_id = entity_id
                    target_id = next((value for value in target_candidates if value != entity_id), None)
                elif entity_id in target_candidates:
                    source_id = next((value for value in source_candidates if value != entity_id), None)
                    target_id = entity_id
                else:
                    source_id = None
                    target_id = None
                segment = segments.get(int(relation.segment_id))
                if (
                    source_id is None
                    or target_id is None
                    or entity_id not in {source_id, target_id}
                    or source_id == target_id
                    or segment is None
                    or find_quote(str(segment["text"]), relation.evidence_quote) is None
                ):
                    continue
                directionality, reverse_predicate = normalize_relation_semantics(
                    relation.predicate, relation.directionality, relation.reverse_predicate,
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO claims(
                        book_id, source_entity_id, target_entity_id, predicate, directionality,
                        reverse_predicate, summary, confidence, status, first_segment, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, 'quality_review')
                    """,
                    (
                        book_id, source_id, target_id, relation.predicate, directionality,
                        reverse_predicate, relation.summary,
                        relation.confidence, int(segment["ordinal"]),
                    ),
                )
                claim = connection.execute(
                    """
                    SELECT id FROM claims WHERE book_id = ? AND source_entity_id = ?
                      AND target_entity_id = ? AND predicate = ? AND first_segment = ?
                    """,
                    (book_id, source_id, target_id, relation.predicate, int(segment["ordinal"])),
                ).fetchone()
                if claim is None:
                    continue
                claim_id = int(claim["id"])
                if add_evidence(
                    connection, book_id, "claim", claim_id, int(segment["id"]),
                    str(segment["text"]), relation.evidence_quote,
                ):
                    relation_ids.append(claim_id)
                    evidence_summary.append({"claim_id": claim_id, "segment_id": int(segment["id"])})
        if relation_ids:
            status = "connected"
            confidence = max(float(item.confidence) for item in entity_decisions)
            reason = f"专项复审找到并逐字验证了 {len(set(relation_ids))} 条遗漏关系。"
        elif len(entity_decisions) < expected_parts:
            status = "pending"
            confidence = 0.0
            reason = "专项复审没有返回全部分片的裁定，节点等待从缓存继续。"
        elif all(item.status == "confirmed_isolated" for item in entity_decisions):
            status = "confirmed_isolated"
            confidence = min(float(item.confidence) for item in entity_decisions)
            reason = "全部提及窗口均已复审，没有发现能够逐字验证的关系。"
        else:
            status = "ambiguous"
            confidence = min((float(item.confidence) for item in entity_decisions), default=0.0)
            reason = "专项复审仍存在主体、名称或关系方向歧义。"
        connection.execute(
            """
            UPDATE entity_connectivity_reviews SET status = ?, candidate_count = ?, confidence = ?,
                reason = ?, review_method = 'model', evidence_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND entity_id = ?
            """,
            (
                status, len(set(relation_ids)), confidence, reason,
                json.dumps(evidence_summary, ensure_ascii=False), book_id, entity_id,
            ),
        )


async def run_quality_harness(
    settings: Settings,
    provider: Provider,
    book_id: int,
    job_id: int,
    include_ambiguous: bool = False,
) -> dict[str, Any]:
    """运行本地检查和按需模型复审，返回门禁前的执行摘要。"""

    with transaction(settings.database_path) as connection:
        analyzed_ordinals = {
            int(row["ordinal"])
            for row in connection.execute(
                """
                SELECT DISTINCT job_segment.ordinal
                FROM analysis_job_segments job_segment
                JOIN analysis_jobs job ON job.id = job_segment.job_id
                WHERE job.book_id = ? AND job_segment.status = 'completed'
                """,
                (book_id,),
            ).fetchall()
        }
        local_summary = refresh_local_reviews(connection, book_id, analyzed_ordinals)
        parts = _payload_parts(
            connection,
            book_id,
            ("pending", "ambiguous") if include_ambiguous else ("pending",),
            analyzed_ordinals,
        )
    groups = _group_parts(parts)
    parts_by_entity: dict[int, int] = defaultdict(int)
    for part in parts:
        for target in part["target_entities"]:
            parts_by_entity[int(target["entity_id"])] += 1
    decisions: dict[int, list[Any]] = defaultdict(list)
    usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_hit_input_tokens": 0, "cache_miss_input_tokens": 0}
    stopped_for_budget = False
    pending_groups = list(groups)
    scheduled_groups = len(groups)
    while pending_groups:
        payload = pending_groups.pop(0)
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        cache_key = hashlib.sha256(
            "\u241f".join((provider.name, provider.model, QUALITY_AUDIT_VERSION, payload_json)).encode("utf-8")
        ).hexdigest()
        with connect(settings.database_path) as connection:
            cached = connection.execute(
                "SELECT response_json FROM quality_audit_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            previous_length_failure = connection.execute(
                """
                SELECT 1 FROM model_call_ledger
                WHERE request_hash = ? AND status = 'failed' AND error LIKE '%长度上限%'
                LIMIT 1
                """,
                (cache_key,),
            ).fetchone()
            job = connection.execute("SELECT * FROM analysis_jobs WHERE id = ?", (job_id,)).fetchone()
            ledger_totals = connection.execute(
                """
                SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0) AS estimated_cost_usd
                FROM model_call_ledger
                WHERE job_id = ? AND status IN ('completed', 'cache_reused', 'failed')
                """,
                (job_id,),
            ).fetchone()
        if cached is None and previous_length_failure is not None:
            split_payloads = _split_payload_targets(payload)
            if split_payloads:
                pending_groups[0:0] = split_payloads
                scheduled_groups += 1
                continue
        if cached is not None:
            result = ConnectivityAuditResult.model_validate_json(str(cached["response_json"]))
            with transaction(settings.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO model_call_ledger(
                        book_id, job_id, purpose, provider, model, prompt_version,
                        request_hash, status, cache_hit, estimated_cost_usd
                    ) VALUES (?, ?, 'connectivity_audit', ?, ?, ?, ?, 'cache_reused', 1, 0)
                    """,
                    (book_id, job_id, provider.name, provider.model, QUALITY_AUDIT_VERSION, cache_key),
                )
        else:
            estimated_input = max(1_200, math.ceil(len(payload_json) * 0.8) + 2_800)
            # 严格结构中的孤立裁定通常很短，按每个目标 280 令牌预留；关系说明仍由 900 下限兜底。
            estimated_output = max(900, len(payload["target_entities"]) * 280)
            provider_call_cost = calculate_cost_usd(
                0, estimated_input, estimated_output, pricing_for(provider.name, provider.model),
            )
            current_input = max(int(job["input_tokens"] or 0), int(ledger_totals["input_tokens"] or 0)) if job is not None else 0
            current_output = max(int(job["output_tokens"] or 0), int(ledger_totals["output_tokens"] or 0)) if job is not None else 0
            current_cost = max(float(job["estimated_cost_usd"] or 0), float(ledger_totals["estimated_cost_usd"] or 0)) if job is not None else 0
            amount_fits = (
                provider_call_cost is None
                or job is None
                or current_cost + provider_call_cost <= float(job["max_cost_usd"])
            )
            token_fits = bool(
                job is not None
                and current_input + estimated_input <= int(job["max_input_tokens"])
                and current_output + estimated_output <= int(job["max_output_tokens"])
            )
            if job is not None and (not token_fits or not amount_fits):
                expansion = adaptive_budget_limits(
                    job,
                    required_input_tokens=current_input + estimated_input,
                    required_output_tokens=current_output + estimated_output,
                )
                if expansion is not None:
                    max_cost_usd, max_input_tokens, max_output_tokens, _ = expansion
                    with transaction(settings.database_path) as connection:
                        connection.execute(
                            """
                            UPDATE analysis_jobs SET max_cost_usd = ?, max_input_tokens = ?,
                                max_output_tokens = ?, budget_status = 'auto_expanded',
                                budget_adjustments = budget_adjustments + 1,
                                updated_at = CURRENT_TIMESTAMP WHERE id = ?
                            """,
                            (max_cost_usd, max_input_tokens, max_output_tokens, job_id),
                        )
                    token_fits = True
                    amount_fits = provider_call_cost is None or current_cost + provider_call_cost <= max_cost_usd
            if job is None or not token_fits or not amount_fits:
                stopped_for_budget = True
                break
            response = None
            try:
                response = await provider.review_connectivity(payload_json)
            except ProviderError as exc:
                failed_usage = exc.usage
                if failed_usage.input_tokens or failed_usage.output_tokens:
                    snapshot = pricing_for(provider.name, provider.model)
                    failed_cost = calculate_cost_usd(
                        failed_usage.cache_hit_input_tokens,
                        failed_usage.cache_miss_input_tokens,
                        failed_usage.output_tokens,
                        snapshot,
                    )
                    usage["calls"] += 1
                    for field in ("input_tokens", "output_tokens", "cache_hit_input_tokens", "cache_miss_input_tokens"):
                        usage[field] += int(getattr(failed_usage, field))
                    with transaction(settings.database_path) as connection:
                        connection.execute(
                            """
                            INSERT INTO model_call_ledger(
                                book_id, job_id, purpose, provider, model, prompt_version,
                                request_hash, status, input_tokens, output_tokens,
                                cache_hit_input_tokens, cache_miss_input_tokens,
                                estimated_cost_usd, error
                            ) VALUES (?, ?, 'connectivity_audit', ?, ?, ?, ?, 'failed', ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                book_id, job_id, provider.name, provider.model, QUALITY_AUDIT_VERSION,
                                cache_key, failed_usage.input_tokens, failed_usage.output_tokens,
                                failed_usage.cache_hit_input_tokens, failed_usage.cache_miss_input_tokens,
                                failed_cost, str(exc)[:500],
                            ),
                        )
                        connection.execute(
                            """
                            INSERT INTO analysis_job_quality_usage(
                                job_id, calls, input_tokens, output_tokens,
                                cache_hit_input_tokens, cache_miss_input_tokens
                            ) VALUES (?, 1, ?, ?, ?, ?)
                            ON CONFLICT(job_id) DO UPDATE SET
                                calls = calls + 1,
                                input_tokens = input_tokens + excluded.input_tokens,
                                output_tokens = output_tokens + excluded.output_tokens,
                                cache_hit_input_tokens = cache_hit_input_tokens + excluded.cache_hit_input_tokens,
                                cache_miss_input_tokens = cache_miss_input_tokens + excluded.cache_miss_input_tokens
                            """,
                            (
                                job_id, failed_usage.input_tokens, failed_usage.output_tokens,
                                failed_usage.cache_hit_input_tokens, failed_usage.cache_miss_input_tokens,
                            ),
                        )
                split_payloads = _split_payload_targets(payload) if "长度上限" in str(exc) else []
                if split_payloads:
                    # 失败批次不落库；子批次插回队首，已经成功的其他批次继续使用缓存。
                    pending_groups[0:0] = split_payloads
                    scheduled_groups += 1
                    continue
                if "长度上限" in str(exc) and len(payload["target_entities"]) == 1:
                    # 单项已经无法继续拆分时，保留为可自动重试、也可人工处理的歧义项。
                    # 这样整个质量任务仍能完成，其余实体的有效裁定也不会被回滚。
                    result = _single_target_length_fallback(payload)
                    if result is None:
                        raise
                else:
                    raise
            if response is not None:
                result = response.result
                usage["calls"] += 1
                for field in ("input_tokens", "output_tokens", "cache_hit_input_tokens", "cache_miss_input_tokens"):
                    usage[field] += int(getattr(response, field))
                snapshot = pricing_for(provider.name, provider.model)
                call_cost = calculate_cost_usd(
                    response.cache_hit_input_tokens,
                    response.cache_miss_input_tokens,
                    response.output_tokens,
                    snapshot,
                )
                with transaction(settings.database_path) as connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO quality_audit_cache(cache_key, provider, model, prompt_version, response_json) VALUES (?, ?, ?, ?, ?)",
                        (cache_key, provider.name, provider.model, QUALITY_AUDIT_VERSION, result.model_dump_json()),
                    )
                    connection.execute(
                        """
                        INSERT INTO model_call_ledger(
                            book_id, job_id, purpose, provider, model, prompt_version,
                            request_hash, status, input_tokens, output_tokens,
                            cache_hit_input_tokens, cache_miss_input_tokens, estimated_cost_usd
                        ) VALUES (?, ?, 'connectivity_audit', ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)
                        """,
                        (
                            book_id, job_id, provider.name, provider.model, QUALITY_AUDIT_VERSION, cache_key,
                            response.input_tokens, response.output_tokens, response.cache_hit_input_tokens,
                            response.cache_miss_input_tokens, call_cost,
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO analysis_job_quality_usage(
                            job_id, calls, input_tokens, output_tokens,
                            cache_hit_input_tokens, cache_miss_input_tokens
                        ) VALUES (?, 1, ?, ?, ?, ?)
                        ON CONFLICT(job_id) DO UPDATE SET
                            calls = calls + 1,
                            input_tokens = input_tokens + excluded.input_tokens,
                            output_tokens = output_tokens + excluded.output_tokens,
                            cache_hit_input_tokens = cache_hit_input_tokens + excluded.cache_hit_input_tokens,
                            cache_miss_input_tokens = cache_miss_input_tokens + excluded.cache_miss_input_tokens
                        """,
                        (
                            job_id, response.input_tokens, response.output_tokens,
                            response.cache_hit_input_tokens, response.cache_miss_input_tokens,
                        ),
                    )
        expected_ids = {int(item["entity_id"]) for item in payload["target_entities"]}
        returned: set[int] = set()
        for decision in result.decisions:
            if decision.entity_id in expected_ids and decision.entity_id not in returned:
                decisions[decision.entity_id].append(decision)
                returned.add(decision.entity_id)
    with transaction(settings.database_path) as connection:
        _persist_audit_decisions(connection, book_id, dict(parts_by_entity), decisions)
        refresh_local_reviews(connection, book_id, analyzed_ordinals)
    return {
        **local_summary,
        "audit_groups": scheduled_groups,
        "stopped_for_budget": stopped_for_budget,
        **usage,
    }
