"""把模型候选转换为只有原文证据支持的数据库事实。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.consolidation import build_analysis_context, consolidate_book, normalized_name
from app.db import connect, transaction
from app.models import ExtractionResult
from app.relations import normalize_relation_semantics
from app.providers import Provider, ProviderError, create_provider


@dataclass(frozen=True)
class PersistStats:
    """单片段写入统计。"""

    accepted: int
    rejected_without_evidence: int


def find_quote(text: str, quote: str) -> tuple[int, int] | None:
    """逐字定位引文；只允许来源排版造成的空白差异。"""

    quote = quote.strip()
    if not quote:
        return None
    start = text.find(quote)
    if start >= 0:
        return start, start + len(quote)

    # Gutenberg 等来源会在句中强制换行；去掉空白后仍要求每个字和标点完全相同。
    normalized_text: list[str] = []
    source_positions: list[int] = []
    for index, character in enumerate(text):
        if not character.isspace():
            normalized_text.append(character)
            source_positions.append(index)
    normalized_quote = "".join(character for character in quote if not character.isspace())
    if not normalized_quote:
        return None
    normalized_start = "".join(normalized_text).find(normalized_quote)
    if normalized_start < 0:
        return None
    normalized_end = normalized_start + len(normalized_quote) - 1
    return source_positions[normalized_start], source_positions[normalized_end] + 1


def upsert_entity(
    connection: sqlite3.Connection,
    book_id: int,
    kind: str,
    name: str,
    summary: str,
    importance: float,
    first_segment: int,
    aliases: list[str] | None = None,
) -> int:
    """优先使用跨章节名称索引，再按规范名创建实体。"""

    clean_name = name.strip()
    key = normalized_name(clean_name)
    indexed = connection.execute(
        """
        SELECT entity_id FROM entity_keys
        WHERE book_id = ? AND kind = ? AND normalized_name = ?
        """,
        (book_id, kind, key),
    ).fetchone()
    if indexed is not None:
        entity_id = int(indexed["entity_id"])
        connection.execute(
            """
            UPDATE entities SET
                importance = MAX(importance, ?),
                first_segment = MIN(first_segment, ?),
                summary = CASE
                    WHEN created_by = 'model' AND LENGTH(?) > LENGTH(summary) THEN ?
                    ELSE summary
                END
            WHERE id = ?
            """,
            (importance, first_segment, summary.strip(), summary.strip(), entity_id),
        )
    else:
        connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'model')
            ON CONFLICT(book_id, kind, name) DO UPDATE SET
                importance = MAX(entities.importance, excluded.importance),
                first_segment = MIN(entities.first_segment, excluded.first_segment),
                summary = CASE
                    WHEN entities.created_by = 'model' AND LENGTH(excluded.summary) > LENGTH(entities.summary)
                    THEN excluded.summary ELSE entities.summary
                END
            """,
            (book_id, kind, clean_name, summary.strip(), importance, first_segment),
        )
        row = connection.execute(
            "SELECT id FROM entities WHERE book_id = ? AND kind = ? AND name = ?",
            (book_id, kind, clean_name),
        ).fetchone()
        if row is None:
            raise RuntimeError("实体写入后无法读取。")
        entity_id = int(row["id"])
        if key:
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_keys(book_id, entity_id, kind, normalized_name, source)
                VALUES (?, ?, ?, ?, 'canonical')
                """,
                (book_id, entity_id, kind, key),
            )

    for alias in aliases or []:
        clean_alias = alias.strip()
        if not clean_alias or clean_alias == clean_name:
            continue
        connection.execute("INSERT OR IGNORE INTO aliases(entity_id, alias) VALUES (?, ?)", (entity_id, clean_alias))
        alias_key = normalized_name(clean_alias)
        if alias_key:
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_keys(book_id, entity_id, kind, normalized_name, source)
                VALUES (?, ?, ?, ?, 'alias')
                """,
                (book_id, entity_id, kind, alias_key),
            )
    return entity_id


def add_evidence(
    connection: sqlite3.Connection,
    book_id: int,
    target_type: str,
    target_id: int,
    segment_id: int,
    text: str,
    quote: str,
) -> bool:
    """验证引文后建立证据边。"""

    located = find_quote(text, quote)
    if located is None:
        return False
    connection.execute(
        """
        INSERT OR IGNORE INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (book_id, target_type, target_id, segment_id, text[located[0] : located[1]], located[0], located[1]),
    )
    return True


def persist_extraction(
    connection: sqlite3.Connection,
    book_id: int,
    segment: sqlite3.Row,
    extraction: ExtractionResult,
) -> PersistStats:
    """写入一个片段；缺少逐字证据的候选不会进入事实表。"""

    accepted = 0
    rejected = 0
    entity_ids: dict[str, int] = {}
    for candidate in extraction.entities:
        if find_quote(segment["text"], candidate.evidence_quote) is None:
            rejected += 1
            continue
        entity_id = upsert_entity(
            connection,
            book_id,
            candidate.kind,
            candidate.name,
            candidate.summary,
            candidate.importance,
            segment["ordinal"],
            candidate.aliases,
        )
        entity_ids[candidate.name] = entity_id
        for alias in candidate.aliases:
            if alias.strip():
                entity_ids.setdefault(alias.strip(), entity_id)
        add_evidence(connection, book_id, "entity", entity_id, segment["id"], segment["text"], candidate.evidence_quote)
        accepted += 1

    existing = connection.execute(
        """
        SELECT e.id, e.name, a.alias FROM entities e
        LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? AND e.first_segment <= ?
        """,
        (book_id, segment["ordinal"]),
    ).fetchall()
    for row in existing:
        entity_ids.setdefault(row["name"], int(row["id"]))
        if row["alias"]:
            entity_ids.setdefault(row["alias"], int(row["id"]))

    # 事件、方位和行程引用的地点也属于明确结构。模型漏掉地点实体卡时，
    # 直接使用同一条逐字证据补建地点，避免整条地图事实在写入阶段被静默丢弃。
    referenced_places: dict[str, tuple[str, str]] = {}
    for candidate in extraction.place_relations:
        referenced_places.setdefault(
            candidate.source,
            (f"原文明确提到的地点；用于记录与{candidate.target}的空间关系。", candidate.evidence_quote),
        )
        referenced_places.setdefault(
            candidate.target,
            (f"原文明确提到的参照地点；用于定位{candidate.source}。", candidate.evidence_quote),
        )
    for candidate in extraction.events:
        if candidate.location:
            referenced_places.setdefault(
                candidate.location,
                (f"事件“{candidate.title}”发生的地点。", candidate.evidence_quote),
            )
    for candidate in extraction.journey_legs:
        for place_name in [candidate.from_location, *candidate.via_locations, candidate.to_location]:
            if place_name:
                referenced_places.setdefault(
                    place_name,
                    (f"原文明示行程经过的地点；{candidate.summary}", candidate.evidence_quote),
                )
    for place_name, (place_summary, evidence_quote) in referenced_places.items():
        if find_quote(segment["text"], evidence_quote) is None:
            continue
        place_row = connection.execute(
            """
            SELECT e.id FROM entities e
            LEFT JOIN aliases a ON a.entity_id = e.id
            WHERE e.book_id = ? AND e.kind = 'place' AND (e.name = ? OR a.alias = ?)
            ORDER BY e.first_segment, e.id LIMIT 1
            """,
            (book_id, place_name, place_name),
        ).fetchone()
        place_id = int(place_row["id"]) if place_row is not None else upsert_entity(
            connection, book_id, "place", place_name, place_summary, 0.65,
            int(segment["ordinal"]), [],
        )
        entity_ids[place_name] = place_id
        add_evidence(
            connection, book_id, "entity", place_id, int(segment["id"]),
            str(segment["text"]), evidence_quote,
        )

    for candidate in extraction.relations:
        location = find_quote(segment["text"], candidate.evidence_quote)
        source_id = entity_ids.get(candidate.source)
        target_id = entity_ids.get(candidate.target)
        if location is None or source_id is None or target_id is None:
            rejected += 1
            continue
        directionality, reverse_predicate = normalize_relation_semantics(
            candidate.predicate, candidate.directionality, candidate.reverse_predicate,
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO claims(
                book_id, source_entity_id, target_entity_id, predicate, directionality,
                reverse_predicate, temporal_scope, summary, confidence, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'model')
            """,
            (
                book_id,
                source_id,
                target_id,
                candidate.predicate,
                directionality,
                reverse_predicate,
                candidate.temporal_scope,
                candidate.summary,
                candidate.confidence,
                segment["ordinal"],
            ),
        )
        claim = connection.execute(
            """
            SELECT id FROM claims
            WHERE book_id = ? AND source_entity_id = ? AND target_entity_id = ?
              AND predicate = ? AND first_segment = ?
            """,
            (book_id, source_id, target_id, candidate.predicate, segment["ordinal"]),
        ).fetchone()
        if claim is not None:
            add_evidence(connection, book_id, "claim", int(claim["id"]), segment["id"], segment["text"], candidate.evidence_quote)
            accepted += 1

    # 地点方位单独保存，地图只把原文明确支持的方向当作地理约束。
    for candidate in extraction.place_relations:
        location = find_quote(segment["text"], candidate.evidence_quote)
        source_id = entity_ids.get(candidate.source)
        target_id = entity_ids.get(candidate.target)
        if location is None or source_id is None or target_id is None or source_id == target_id:
            rejected += 1
            continue
        kinds = connection.execute(
            "SELECT id, kind FROM entities WHERE id IN (?, ?)",
            (source_id, target_id),
        ).fetchall()
        if len(kinds) != 2 or any(item["kind"] != "place" for item in kinds):
            rejected += 1
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO place_relations(
                book_id, source_entity_id, target_entity_id, relative_position,
                summary, confidence, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'model')
            """,
            (
                book_id, source_id, target_id, candidate.relative_position,
                candidate.summary, candidate.confidence, segment["ordinal"],
            ),
        )
        relation = connection.execute(
            """
            SELECT id FROM place_relations
            WHERE book_id = ? AND source_entity_id = ? AND target_entity_id = ?
              AND relative_position = ? AND first_segment = ?
            """,
            (book_id, source_id, target_id, candidate.relative_position, segment["ordinal"]),
        ).fetchone()
        if relation is not None:
            add_evidence(
                connection, book_id, "place_relation", int(relation["id"]),
                segment["id"], segment["text"], candidate.evidence_quote,
            )
            accepted += 1

    pending_event_edges: list[tuple[int, str, str, float]] = []
    pending_causal_edges: list[tuple[int, str, str, float, str]] = []
    for candidate in extraction.events:
        quote_location = find_quote(segment["text"], candidate.evidence_quote)
        if quote_location is None:
            rejected += 1
            continue
        location_id = entity_ids.get(candidate.location or "")
        global_narrative_order = int(segment["ordinal"]) * 100 + min(candidate.narrative_order, 99)
        global_story_order = float(global_narrative_order)
        connection.execute(
            """
            INSERT OR IGNORE INTO events(
                book_id, title, summary, narrative_order, story_order, temporal_kind, temporal_value,
                temporal_start, temporal_end, confidence, location_entity_id, transport,
                first_segment, created_by, narrative_phase, narrative_offset
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'model', ?, ?)
            """,
            (
                book_id,
                candidate.title,
                candidate.summary,
                global_narrative_order,
                global_story_order,
                candidate.temporal_kind,
                candidate.temporal_value,
                candidate.temporal_start,
                candidate.temporal_end,
                candidate.confidence,
                location_id,
                candidate.transport,
                segment["ordinal"],
                candidate.narrative_phase,
                quote_location[0],
            ),
        )
        event = connection.execute(
            "SELECT id FROM events WHERE book_id = ? AND title = ? AND narrative_order = ? AND first_segment = ?",
            (book_id, candidate.title, global_narrative_order, segment["ordinal"]),
        ).fetchone()
        if event is None:
            continue
        event_id = int(event["id"])
        add_evidence(connection, book_id, "event", event_id, segment["id"], segment["text"], candidate.evidence_quote)
        frame = candidate.narrative_frame
        verified_frame_quotes = [
            quote for quote in frame.evidence_quotes
            if find_quote(segment["text"], quote) is not None
        ]
        if frame.evidence_quotes and len(verified_frame_quotes) != len(frame.evidence_quotes):
            rejected += len(frame.evidence_quotes) - len(verified_frame_quotes)
        frame_has_content = any([
            frame.cause, frame.trigger, frame.goal, frame.action, frame.outcome,
            frame.state_changes, frame.open_threads, frame.resolved_threads,
        ])
        frame_supported = bool(verified_frame_quotes) and len(verified_frame_quotes) == len(frame.evidence_quotes)
        if frame_has_content and not frame_supported:
            rejected += 1
        connection.execute(
            """
            INSERT INTO event_narrative_frames(
                event_id, book_id, cause, trigger_text, goal, action, outcome,
                state_changes_json, open_threads_json, resolved_threads_json, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'model')
            ON CONFLICT(event_id) DO UPDATE SET
                cause = excluded.cause,
                trigger_text = excluded.trigger_text,
                goal = excluded.goal,
                action = excluded.action,
                outcome = excluded.outcome,
                state_changes_json = excluded.state_changes_json,
                open_threads_json = excluded.open_threads_json,
                resolved_threads_json = excluded.resolved_threads_json,
                created_by = excluded.created_by,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                event_id, book_id, frame.cause if frame_supported else "",
                frame.trigger if frame_supported else "", frame.goal if frame_supported else "",
                (frame.action if frame_supported else "") or candidate.summary,
                frame.outcome if frame_supported else "",
                json.dumps(frame.state_changes if frame_supported else [], ensure_ascii=False),
                json.dumps(frame.open_threads if frame_supported else [], ensure_ascii=False),
                json.dumps(frame.resolved_threads if frame_supported else [], ensure_ascii=False),
            ),
        )
        for quote in verified_frame_quotes if frame_supported else []:
            add_evidence(connection, book_id, "narrative_frame", event_id, segment["id"], segment["text"], quote)
        for reference in frame.causal_references:
            if find_quote(segment["text"], reference.evidence_quote) is not None:
                pending_causal_edges.append(
                    (event_id, reference.target_event, reference.relation, candidate.confidence, reference.evidence_quote)
                )
            else:
                rejected += 1
        for participant in candidate.participants:
            participant_id = entity_ids.get(participant.name)
            if participant_id is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO event_participants(event_id, entity_id, role) VALUES (?, ?, ?)",
                    (event_id, participant_id, participant.role),
                )
        if candidate.reference_event and candidate.relation_to_reference != "unknown":
            pending_event_edges.append(
                (event_id, candidate.reference_event, candidate.relation_to_reference, candidate.confidence)
            )
        accepted += 1

    for event_id, reference_title, relation, confidence in pending_event_edges:
        reference = connection.execute(
            """
            SELECT id FROM events WHERE book_id = ? AND title = ? AND id != ?
            ORDER BY first_segment DESC, id DESC LIMIT 1
            """,
            (book_id, reference_title, event_id),
        ).fetchone()
        if reference is None or relation in {"during", "same"}:
            continue
        reference_id = int(reference["id"])
        earlier_id, later_id = (event_id, reference_id) if relation == "before" else (reference_id, event_id)
        connection.execute(
            """
            INSERT OR IGNORE INTO event_order_edges(
                book_id, earlier_event_id, later_event_id, relation, confidence, created_by
            ) VALUES (?, ?, ?, ?, ?, 'model')
            """,
            (book_id, earlier_id, later_id, relation, confidence),
        )

    for source_event_id, target_title, relation, confidence, quote in pending_causal_edges:
        target = connection.execute(
            """
            SELECT id FROM events WHERE book_id = ? AND title = ? AND id != ?
            ORDER BY story_order DESC, id DESC LIMIT 1
            """,
            (book_id, target_title, source_event_id),
        ).fetchone()
        if target is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO event_causal_links(
                book_id, source_event_id, target_event_id, relation, confidence,
                evidence_json, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, 'model')
            """,
            (
                book_id, source_event_id, int(target["id"]), relation, confidence,
                json.dumps([quote], ensure_ascii=False),
            ),
        )

    # 行程与普通事件分开保存；人物、起点和终点都必须能落到已有实体。
    journey_ordinal = 0
    for candidate in extraction.journey_legs:
        if find_quote(segment["text"], candidate.evidence_quote) is None:
            rejected += 1
            continue
        subject_ids = sorted({entity_ids[name] for name in candidate.subject_names if name in entity_ids})
        route_names = [candidate.from_location, *candidate.via_locations, candidate.to_location]
        route_names = [name for index, name in enumerate(route_names) if name and (index == 0 or name != route_names[index - 1])]
        route_ids = [entity_ids.get(name) for name in route_names]
        if not subject_ids or not route_names or any(entity_id is None for entity_id in route_ids):
            rejected += 1
            continue
        if len(route_ids) == 1:
            pairs = [(route_ids[0], route_ids[0])]
        else:
            pairs = list(zip(route_ids, route_ids[1:], strict=False))
        for subject_id in subject_ids:
            for from_id, to_id in pairs:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO journey_legs(
                        book_id, subject_entity_id, from_entity_id, to_entity_id, ordinal,
                        transport, summary, gap_status, confidence, first_segment, created_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'complete', ?, ?, 'model')
                    """,
                    (
                        book_id, subject_id, from_id, to_id,
                        int(segment["ordinal"]) * 100 + journey_ordinal,
                        candidate.transport or "未说明", candidate.summary,
                        candidate.confidence, segment["ordinal"],
                    ),
                )
                if cursor.rowcount:
                    leg_id = int(cursor.lastrowid)
                    add_evidence(
                        connection, book_id, "journey_leg", leg_id, segment["id"],
                        segment["text"], candidate.evidence_quote,
                    )
                    accepted += 1
                journey_ordinal += 1

    for candidate in extraction.world_notes:
        if find_quote(segment["text"], candidate.evidence_quote) is None:
            rejected += 1
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO world_notes(book_id, category, title, summary, confidence, first_segment, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 'model')
            """,
            (book_id, candidate.category, candidate.title, candidate.summary, candidate.confidence, segment["ordinal"]),
        )
        note = connection.execute(
            "SELECT id FROM world_notes WHERE book_id = ? AND category = ? AND title = ? AND first_segment = ?",
            (book_id, candidate.category, candidate.title, segment["ordinal"]),
        ).fetchone()
        if note is not None:
            add_evidence(connection, book_id, "world_note", int(note["id"]), segment["id"], segment["text"], candidate.evidence_quote)
            accepted += 1

    for candidate in extraction.entries:
        if find_quote(segment["text"], candidate.evidence_quote) is None:
            rejected += 1
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO entries(
                book_id, category, name, summary, attributes_json, confidence, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'model')
            """,
            (
                book_id,
                candidate.category,
                candidate.name,
                candidate.summary,
                json.dumps(candidate.attributes, ensure_ascii=False),
                candidate.confidence,
                segment["ordinal"],
            ),
        )
        entry = connection.execute(
            "SELECT id FROM entries WHERE book_id = ? AND category = ? AND name = ? AND first_segment = ?",
            (book_id, candidate.category, candidate.name, segment["ordinal"]),
        ).fetchone()
        if entry is not None:
            add_evidence(connection, book_id, "entry", int(entry["id"]), segment["id"], segment["text"], candidate.evidence_quote)
            accepted += 1
    return PersistStats(accepted=accepted, rejected_without_evidence=rejected)


def recover_cached_extractions(connection: sqlite3.Connection, book_id: int) -> dict[str, int]:
    """用已付费缓存重新写入曾被旧落库规则丢弃的结构，不产生模型调用。"""

    cached_rows = connection.execute(
        """
        SELECT DISTINCT cache.cache_key, cache.response_json
        FROM model_call_ledger ledger
        JOIN extraction_cache cache ON cache.cache_key = ledger.request_hash
        WHERE ledger.book_id = ? AND ledger.purpose = 'segment_extraction'
          AND ledger.status = 'completed'
        ORDER BY ledger.id
        """,
        (book_id,),
    ).fetchall()
    segments = connection.execute(
        "SELECT * FROM segments WHERE book_id = ? ORDER BY ordinal",
        (book_id,),
    ).fetchall()
    replayed = 0
    unmatched = 0
    for cached in cached_rows:
        try:
            extraction = ExtractionResult.model_validate_json(str(cached["response_json"]))
        except ValueError:
            unmatched += 1
            continue
        event_titles = [item.title for item in extraction.events]
        matched_ordinals = {
            int(row["first_segment"])
            for title in event_titles
            for row in connection.execute(
                "SELECT first_segment FROM events WHERE book_id = ? AND title = ?",
                (book_id, title),
            ).fetchall()
        }
        segment = None
        if len(matched_ordinals) == 1:
            ordinal = next(iter(matched_ordinals))
            segment = next((item for item in segments if int(item["ordinal"]) == ordinal), None)
        if segment is None:
            quotes = [
                item.evidence_quote
                for group in (
                    extraction.entities, extraction.relations, extraction.place_relations,
                    extraction.events, extraction.journey_legs, extraction.world_notes, extraction.entries,
                )
                for item in group
            ]
            scored = [
                (sum(1 for quote in quotes if find_quote(str(item["text"]), quote) is not None), item)
                for item in segments
            ]
            score, segment = max(scored, key=lambda pair: pair[0], default=(0, None))
            if score == 0:
                segment = None
        if segment is None:
            unmatched += 1
            continue
        persist_extraction(connection, book_id, segment, extraction)
        replayed += 1
    return {"cached_extractions": len(cached_rows), "replayed": replayed, "unmatched": unmatched}


async def analyze_book(
    settings: Settings,
    book_id: int,
    provider_name: str,
    start_segment: int,
    max_segments: int,
    provider_override: Provider | None = None,
) -> dict[str, int | str]:
    """顺序处理有限片段，并记录令牌、失败和证据拒绝数。"""

    provider = provider_override or create_provider(settings, provider_name, book_id)
    with connect(settings.database_path) as connection:
        segments = connection.execute(
            """
            SELECT * FROM segments WHERE book_id = ? AND ordinal >= ?
            ORDER BY ordinal LIMIT ?
            """,
            (book_id, start_segment, max_segments),
        ).fetchall()
        if not segments:
            raise ProviderError("没有可分析的原文片段。")
        cursor = connection.execute(
            """
            INSERT INTO analysis_runs(book_id, provider, model, status, start_segment, segments_requested)
            VALUES (?, ?, ?, 'running', ?, ?)
            """,
            (book_id, provider.name, provider.model, start_segment, len(segments)),
        )
        run_id = int(cursor.lastrowid)

    succeeded = 0
    input_tokens = 0
    output_tokens = 0
    accepted = 0
    rejected = 0
    error = ""
    try:
        for segment in segments:
            with connect(settings.database_path) as context_connection:
                context = build_analysis_context(context_connection, book_id, int(segment["ordinal"]))
            response = await provider.extract(
                segment["chapter_title"], segment["ordinal"], segment["text"], context
            )
            with transaction(settings.database_path) as connection:
                stats = persist_extraction(connection, book_id, segment, response.extraction)
                consolidate_book(connection, book_id, int(segment["ordinal"]))
            succeeded += 1
            input_tokens += response.input_tokens
            output_tokens += response.output_tokens
            accepted += stats.accepted
            rejected += stats.rejected_without_evidence
        status = "completed"
    except Exception as exc:
        status = "failed"
        error = str(exc)[:600]
    with connect(settings.database_path) as connection:
        connection.execute(
            """
            UPDATE analysis_runs SET status = ?, segments_succeeded = ?, input_tokens = ?,
                output_tokens = ?, error = ?, completed_at = ? WHERE id = ?
            """,
            (
                status,
                succeeded,
                input_tokens,
                output_tokens,
                error,
                datetime.now(timezone.utc).isoformat(),
                run_id,
            ),
        )
    if status == "failed":
        raise ProviderError(error)
    return {
        "run_id": run_id,
        "status": status,
        "segments_succeeded": succeeded,
        "accepted_facts": accepted,
        "rejected_without_evidence": rejected,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
