"""Low-cost causal memory derived from evidence-backed event frames."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from typing import Any


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _thread_key(value: str) -> str:
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", value.casefold())
    return hashlib.sha256(normalized.encode()).hexdigest()[:24]


def _unique_text(parts: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = str(part or "").strip().strip("。；;，,")
        key = re.sub(r"\s+", "", cleaned).casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def compose_event_narrative(event: dict[str, Any]) -> str:
    """Compose readable prose without a model call or unsupported connective facts."""

    frame = event.get("narrative_frame") or {}
    clean = lambda value: str(value or "").strip().rstrip("。；;，,")
    cause = clean(frame.get("cause") or frame.get("trigger") or "")
    action = clean(frame.get("action") or event.get("summary") or "")
    outcome = clean(frame.get("outcome") or "")
    changes = _unique_text([str(item) for item in frame.get("state_changes") or []])
    open_threads = _unique_text([str(item) for item in frame.get("open_threads") or []])
    sentences: list[str] = []
    if cause and action:
        sentences.append(f"{cause}，{action}")
    elif action:
        sentences.append(action)
    elif cause:
        sentences.append(cause)
    if outcome:
        sentences.append(outcome)
    if changes:
        sentences.append("随后，" + "；".join(changes))
    if open_threads:
        sentences.append("尚未解决的是" + "、".join(open_threads))
    if not sentences:
        return str(event.get("summary") or "")
    return "；".join(sentence.rstrip("。；") for sentence in sentences)


def rebuild_narrative_memory(connection: sqlite3.Connection, book_id: int) -> None:
    events = [dict(row) for row in connection.execute(
        """
        SELECT e.*, f.cause, f.trigger_text, f.goal, f.action, f.outcome,
            f.state_changes_json, f.open_threads_json, f.resolved_threads_json,
            s.chapter_title
        FROM events e
        LEFT JOIN event_narrative_frames f ON f.event_id = e.id
        LEFT JOIN segments s ON s.book_id = e.book_id AND s.ordinal = e.first_segment
        WHERE e.book_id = ?
        ORDER BY e.story_order, e.narrative_order, e.id
        """,
        (book_id,),
    )]
    participants: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT ep.event_id, ep.entity_id, ep.role, entity.name
        FROM event_participants ep
        JOIN events event ON event.id = ep.event_id
        JOIN entities entity ON entity.id = ep.entity_id
        WHERE event.book_id = ?
        ORDER BY event.story_order, event.id, entity.id
        """,
        (book_id,),
    ):
        participants[int(row["event_id"])].append(dict(row))

    # Rebuilding derived memory must not erase a user's previous task decision
    # or route note; source-derived fields are recomputed below, while the
    # explicit review metadata is carried forward by the stable thread key.
    previous_threads = {
        str(row["thread_key"]): dict(row)
        for row in connection.execute(
            "SELECT * FROM open_threads WHERE book_id = ?", (book_id,)
        ).fetchall()
    }
    connection.execute("DELETE FROM character_states WHERE book_id = ?", (book_id,))
    connection.execute("DELETE FROM open_threads WHERE book_id = ?", (book_id,))
    connection.execute("DELETE FROM arc_memories WHERE book_id = ?", (book_id,))
    character_accumulator: dict[int, dict[str, Any]] = {}
    open_thread_rows: dict[str, dict[str, Any]] = {}
    chapter_events: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)

    event_evidence: dict[int, list[int]] = defaultdict(list)
    for row in connection.execute(
        "SELECT DISTINCT target_id AS event_id, segment_id FROM evidence WHERE book_id = ? AND target_type = 'event'",
        (book_id,),
    ).fetchall():
        event_evidence[int(row["event_id"])].append(int(row["segment_id"]))

    for event in events:
        event_id = int(event["id"])
        state_changes = _json_list(event.get("state_changes_json"))
        opened = _json_list(event.get("open_threads_json"))
        resolved = _json_list(event.get("resolved_threads_json"))
        frame = {
            "cause": event.get("cause") or "",
            "trigger": event.get("trigger_text") or "",
            "goal": event.get("goal") or "",
            "action": event.get("action") or event.get("summary") or "",
            "outcome": event.get("outcome") or "",
            "state_changes": state_changes,
            "open_threads": opened,
        }
        event["narrative_frame"] = frame
        event["narrative_text"] = compose_event_narrative(event)
        chapter_events[(int(event["first_segment"]), str(event.get("chapter_title") or "未命名章节"))].append(event)
        for participant in participants.get(event_id, []):
            entity_id = int(participant["entity_id"])
            current = character_accumulator.setdefault(entity_id, {
                "through_event_id": event_id,
                "location_entity_id": event.get("location_entity_id"),
                "goal": "",
                "states": [],
                "source_event_ids": [],
                "source_segments": [],
            })
            current["through_event_id"] = event_id
            if event.get("location_entity_id") is not None:
                current["location_entity_id"] = int(event["location_entity_id"])
            if event.get("goal"):
                current["goal"] = str(event["goal"])
            current["states"] = _unique_text([*current["states"], *[str(item) for item in state_changes]])[-12:]
            current["source_event_ids"] = [*current["source_event_ids"], event_id][-24:]
            current["source_segments"] = [*current["source_segments"], int(event["first_segment"])][-24:]
        for title in opened:
            key = _thread_key(str(title))
            evidence_segments = event_evidence.get(event_id, [])
            actionable = bool(evidence_segments) and bool(str(title).strip())
            rebuilt = {
                "title": str(title), "status": "open", "opened_event_id": event_id,
                "resolved_event_id": None, "evidence": [event_id],
                "evidence_segment_ids": evidence_segments,
                "actionability": "actionable" if actionable else "informational",
                "resolution_route": "source_and_review" if actionable else "",
                "recommended_action": "打开原文并进入处理" if actionable else "",
            }
            previous = previous_threads.get(key)
            if previous is not None:
                # Keep explicit human routing choices when a deterministic
                # rebuild discovers the same thread again.
                if str(previous.get("actionability") or "") in {"actionable", "informational"}:
                    rebuilt["actionability"] = str(previous["actionability"])
                if str(previous.get("resolution_route") or "").strip():
                    rebuilt["resolution_route"] = str(previous["resolution_route"])
                if str(previous.get("recommended_action") or "").strip():
                    rebuilt["recommended_action"] = str(previous["recommended_action"])
            open_thread_rows[key] = rebuilt
        for title in resolved:
            key = _thread_key(str(title))
            row = open_thread_rows.setdefault(key, {
                "title": str(title), "status": "resolved", "opened_event_id": None,
                "resolved_event_id": event_id, "evidence": [],
                "actionability": "informational", "resolution_route": "",
                "recommended_action": "",
            })
            row["status"] = "resolved"
            row["resolved_event_id"] = event_id
            row["evidence"] = _unique_text([*map(str, row["evidence"]), str(event_id)])

    for entity_id, state in character_accumulator.items():
        connection.execute(
            """
            INSERT INTO character_states(
                book_id, entity_id, through_event_id, location_entity_id, goal,
                state_json, source_event_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, entity_id, state["through_event_id"], state["location_entity_id"],
                state["goal"], json.dumps(state["states"], ensure_ascii=False),
                json.dumps(state["source_event_ids"]),
            ),
        )
    for key, thread in open_thread_rows.items():
        connection.execute(
            """
            INSERT INTO open_threads(
                book_id, thread_key, title, status, opened_event_id, resolved_event_id,
                evidence_json, actionability, resolution_route, evidence_segment_ids_json,
                recommended_action
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id, key, thread["title"], thread["status"], thread["opened_event_id"],
                thread["resolved_event_id"], json.dumps(thread["evidence"], ensure_ascii=False),
                thread.get("actionability", "informational"), thread.get("resolution_route", ""),
                json.dumps(thread.get("evidence_segment_ids", []), ensure_ascii=False), thread.get("recommended_action", ""),
            ),
        )
    for (segment_ordinal, chapter_title), grouped_events in chapter_events.items():
        event_ids = [int(item["id"]) for item in grouped_events]
        narratives = _unique_text([str(item["narrative_text"]) for item in grouped_events])
        summary = " ".join(narratives)
        source_hash = hashlib.sha256(json.dumps(event_ids).encode()).hexdigest()
        connection.execute(
            """
            INSERT INTO arc_memories(
                book_id, arc_key, start_segment, end_segment, summary,
                event_ids_json, source_hash, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'local')
            """,
            (
                book_id, f"segment:{segment_ordinal}:{chapter_title}", segment_ordinal,
                segment_ordinal, summary, json.dumps(event_ids), source_hash,
            ),
        )


def _bounded_character_states(
    connection: sqlite3.Connection,
    book_id: int,
    through_segment: int,
    from_segment: int = 0,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT e.id AS event_id, e.location_entity_id, e.first_segment,
            f.goal, f.state_changes_json, ep.entity_id, entity.name,
            entity.importance, place.name AS location_name
        FROM events e
        JOIN event_participants ep ON ep.event_id = e.id
        JOIN entities entity ON entity.id = ep.entity_id
        LEFT JOIN entities place ON place.id = e.location_entity_id
        LEFT JOIN event_narrative_frames f ON f.event_id = e.id
        WHERE e.book_id = ? AND e.first_segment <= ? AND entity.kind = 'person'
        ORDER BY e.story_order, e.narrative_order, e.id
        """,
        (book_id, through_segment),
    ).fetchall()
    states: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        entity_id = int(row["entity_id"])
        current = states.setdefault(entity_id, {
            "entity_id": entity_id,
            "name": row["name"],
            "importance": row["importance"],
            "through_event_id": row["event_id"],
            "location_entity_id": None,
            "location_name": None,
            "goal": "",
            "states": [],
            "source_event_ids": [],
            "source_segments": [],
        })
        current["through_event_id"] = int(row["event_id"])
        if row["location_entity_id"] is not None:
            current["location_entity_id"] = int(row["location_entity_id"])
            current["location_name"] = row["location_name"]
        if row["goal"]:
            current["goal"] = str(row["goal"])
        current["states"] = _unique_text([
            *current["states"], *[str(item) for item in _json_list(row["state_changes_json"])],
        ])[-12:]
        current["source_event_ids"] = [*current["source_event_ids"], int(row["event_id"])][-24:]
        current["source_segments"] = [*current["source_segments"], int(row["first_segment"] )][-24:]
    for item in states.values():
        item["context_only"] = not any(
            int(segment) >= int(from_segment)
            for segment in item.get("source_segments", [])
        )
        item.pop("source_segments", None)
    return sorted(states.values(), key=lambda item: (-float(item["importance"]), int(item["entity_id"])))


def _bounded_threads(
    connection: sqlite3.Connection,
    book_id: int,
    through_segment: int,
    from_segment: int = 0,
) -> list[dict[str, Any]]:
    frames = connection.execute(
        """
        SELECT e.id, e.first_segment, f.open_threads_json, f.resolved_threads_json
        FROM events e JOIN event_narrative_frames f ON f.event_id = e.id
        WHERE e.book_id = ? AND e.first_segment <= ?
        ORDER BY e.story_order, e.narrative_order, e.id
        """,
        (book_id, through_segment),
    ).fetchall()
    threads: dict[str, dict[str, Any]] = {}
    for frame in frames:
        event_id = int(frame["id"])
        for title in _json_list(frame["open_threads_json"]):
            key = _thread_key(str(title))
            evidence_segment_ids = [int(row["segment_id"]) for row in connection.execute(
                "SELECT DISTINCT segment_id FROM evidence WHERE book_id = ? AND target_type = 'event' AND target_id = ?",
                (book_id, event_id),
            ).fetchall()]
            evidence_exists = bool(evidence_segment_ids)
            current = threads.get(key)
            if current is None:
                current = {
                    "thread_key": key, "title": str(title), "status": "open",
                    "opened_event_id": event_id, "resolved_event_id": None,
                    "evidence": [], "evidence_segment_ids": [],
                    "actionability": "informational", "resolution_route": "",
                    "recommended_action": "", "context_only": True,
                }
                threads[key] = current
            current["status"] = "open"
            current["opened_event_id"] = current.get("opened_event_id") or event_id
            current["evidence"] = [*current.get("evidence", []), event_id]
            current["evidence_segment_ids"] = sorted(set(
                [*current.get("evidence_segment_ids", []), *evidence_segment_ids]
            ))
            if evidence_exists:
                current["actionability"] = "actionable"
                current["resolution_route"] = "source_and_review"
                current["recommended_action"] = "打开原文并进入处理"
            current["context_only"] = bool(current.get("context_only", True) and int(frame["first_segment"]) < int(from_segment))
        for title in _json_list(frame["resolved_threads_json"]):
            key = _thread_key(str(title))
            current = threads.setdefault(key, {
                "thread_key": key, "title": str(title), "status": "resolved",
                "opened_event_id": None, "resolved_event_id": event_id, "evidence": [],
                "evidence_segment_ids": [], "actionability": "informational",
                "resolution_route": "", "recommended_action": "",
            })
            current["status"] = "resolved"
            current["resolved_event_id"] = event_id
            current["evidence"] = [*current["evidence"], event_id]
    return sorted(threads.values(), key=lambda item: (item["status"] != "open", item["title"]))


def narrative_memory_payload(
    connection: sqlite3.Connection,
    book_id: int,
    through_segment: int | None = None,
    from_segment: int = 0,
) -> dict[str, Any]:
    rebuild_narrative_memory(connection, book_id)
    boundary = 1_000_000 if through_segment is None else through_segment
    recent = [dict(row) for row in connection.execute(
        """
        SELECT e.id, e.title, e.summary, e.story_order, e.first_segment,
            f.cause, f.trigger_text, f.goal, f.action, f.outcome,
            f.state_changes_json, f.open_threads_json, f.resolved_threads_json
        FROM events e LEFT JOIN event_narrative_frames f ON f.event_id = e.id
        WHERE e.book_id = ? AND e.first_segment <= ?
        ORDER BY e.story_order DESC, e.id DESC LIMIT 24
        """,
        (book_id, boundary),
    )]
    for event in recent:
        event["context_only"] = int(event.get("first_segment") or 0) < int(from_segment)
        event["narrative_frame"] = {
            "cause": event.pop("cause") or "",
            "trigger": event.pop("trigger_text") or "",
            "goal": event.pop("goal") or "",
            "action": event.pop("action") or event["summary"],
            "outcome": event.pop("outcome") or "",
            "state_changes": _json_list(event.pop("state_changes_json")),
            "open_threads": _json_list(event.pop("open_threads_json")),
            "resolved_threads": _json_list(event.pop("resolved_threads_json")),
        }
        event["narrative_text"] = compose_event_narrative(event)
    characters = _bounded_character_states(connection, book_id, boundary, from_segment)
    threads = _bounded_threads(connection, book_id, boundary, from_segment)
    arcs = [dict(row) for row in connection.execute(
        "SELECT * FROM arc_memories WHERE book_id = ? AND end_segment <= ? ORDER BY start_segment, id",
        (book_id, boundary),
    )]
    for arc in arcs:
        arc["context_only"] = int(arc.get("end_segment") or 0) < int(from_segment)
    causal_links = [dict(row) for row in connection.execute(
        """
        SELECT link.*, source.first_segment AS source_first_segment,
            target.first_segment AS target_first_segment
        FROM event_causal_links link
        JOIN events source ON source.id = link.source_event_id
        JOIN events target ON target.id = link.target_event_id
        WHERE link.book_id = ? AND link.status != 'deprecated'
          AND source.first_segment <= ? AND target.first_segment <= ?
        ORDER BY link.id
        """,
        (book_id, boundary, boundary),
    )]
    for link in causal_links:
        source_segment = int(link.pop("source_first_segment") or 0)
        target_segment = int(link.pop("target_first_segment") or 0)
        link["context_only"] = max(source_segment, target_segment) < int(from_segment)
    actionable_threads = [
        item for item in threads
        if item.get("status") == "open" and item.get("actionability") == "actionable"
    ]
    return {
        "memory_version": "causal-memory-v2",
        "through_segment": boundary,
        "from_segment": max(0, int(from_segment)),
        "recent_scenes": list(reversed(recent)),
        "character_states": characters,
        "open_threads": actionable_threads,
        "internal_open_threads": threads,
        "arc_memories": arcs,
        "causal_links": causal_links,
        "generation_policy": "local_first_cached_arc_review",
    }
