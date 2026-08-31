"""Evidence-bounded hierarchy, order, and current-story knowledge projections."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def systems_payload(
    connection: sqlite3.Connection,
    book_id: int,
    through_segment: int | None = None,
    from_segment: int = 0,
) -> list[dict[str, Any]]:
    """Return systems with nodes, relations, and literal evidence metadata."""

    systems = [dict(row) for row in connection.execute(
        "SELECT * FROM world_systems WHERE book_id = ? ORDER BY status != 'active', category, name",
        (book_id,),
    )]
    for system in systems:
        system_id = int(system["id"])
        nodes = [dict(row) for row in connection.execute(
            """
            SELECT node.*, concept.preferred_label AS concept_label
            FROM world_system_nodes node
            LEFT JOIN concepts concept ON concept.id = node.concept_id
            WHERE node.system_id = ? AND node.status != 'deprecated'
            ORDER BY node.rank_value IS NULL, node.rank_value, node.label
            """,
            (system_id,),
        )]
        relations = [dict(row) for row in connection.execute(
            """
            SELECT relation.*, evidence.quote AS evidence_quote,
                   evidence.segment_id AS evidence_segment_id
            FROM world_system_relations relation
            LEFT JOIN evidence ON evidence.id = relation.evidence_id
            WHERE relation.system_id = ? AND relation.status != 'deprecated'
            ORDER BY relation.id
            """,
            (system_id,),
        )]
        system["nodes"] = nodes
        system["relations"] = relations
        system["evidence_count"] = sum(1 for item in relations if item.get("evidence_id"))
        system["comparable"] = system["structure_type"] in {"ordered", "hierarchical"}
        if through_segment is not None:
            system["nodes"] = [
                node for node in nodes
                if int(node.get("effective_from_segment") or 0) <= through_segment
                and (node.get("effective_to_segment") is None or int(node["effective_to_segment"]) >= from_segment)
            ]
            for node in system["nodes"]:
                node["context_only"] = int(node.get("effective_from_segment") or 0) < from_segment
            system["relations"] = [
                relation for relation in relations
                if relation.get("evidence_segment_id") is None
                or _segment_ordinal(connection, int(relation["evidence_segment_id"])) <= through_segment
            ]
            for relation in system["relations"]:
                evidence_segment = relation.get("evidence_segment_id")
                if evidence_segment is not None:
                    relation["context_only"] = _segment_ordinal(
                        connection, int(evidence_segment)
                    ) < from_segment
    return systems


def _segment_ordinal(connection: sqlite3.Connection, segment_id: int) -> int:
    row = connection.execute("SELECT ordinal FROM segments WHERE id = ?", (segment_id,)).fetchone()
    return int(row["ordinal"]) if row is not None else 10**9


def story_knowledge_context(
    connection: sqlite3.Connection,
    book_id: int,
    event_id: int,
    through_segment: int,
    from_segment: int = 0,
) -> dict[str, Any]:
    """Build a spoiler-safe reader capsule from already accepted facts only."""

    event = connection.execute(
        """
        SELECT event.*, location.name AS location_name, segment.chapter_title
        FROM events event
        LEFT JOIN entities location ON location.id = event.location_entity_id
        JOIN segments segment ON segment.book_id = event.book_id AND segment.ordinal = event.first_segment
        WHERE event.id = ? AND event.book_id = ? AND event.first_segment <= ?
        """,
        (event_id, book_id, through_segment),
    ).fetchone()
    if event is None:
        raise ValueError("当前步骤不存在，或已经超过防剧透进度")

    entity_ids = [int(row["entity_id"]) for row in connection.execute(
        "SELECT entity_id FROM event_participants WHERE event_id = ?",
        (event_id,),
    )]
    if event["location_entity_id"] is not None:
        entity_ids.append(int(event["location_entity_id"]))
    entity_ids = sorted(set(entity_ids))

    items: list[dict[str, Any]] = []
    if entity_ids:
        marks = ",".join("?" for _ in entity_ids)
        rows = connection.execute(
            f"""
            SELECT entity.id AS entity_id, entity.name, entity.kind, concept.id AS concept_id,
                   concept.description, claim.id AS claim_id, claim.predicate, claim.value_json,
                   claim.source_kind, claim.confidence, COUNT(proof.id) AS evidence_count
            FROM entities entity
            LEFT JOIN concepts concept ON concept.book_id = entity.book_id
                AND concept.scheme = 'book' AND concept.preferred_label = entity.name
            LEFT JOIN knowledge_claims claim ON claim.concept_id = concept.id
                AND claim.status IN ('accepted', 'parallel')
            LEFT JOIN knowledge_claim_evidence link ON link.knowledge_claim_id = claim.id
            LEFT JOIN evidence proof ON proof.id = link.evidence_id
                AND proof.segment_id IN (
                    SELECT id FROM segments WHERE book_id = ? AND ordinal <= ?
                )
            WHERE entity.book_id = ? AND entity.id IN ({marks})
              AND entity.first_segment <= ?
            GROUP BY entity.id, concept.id, claim.id
            ORDER BY entity.kind, entity.name, claim.id
            """,
            (book_id, through_segment, book_id, *entity_ids, through_segment),
        ).fetchall()
        for row in rows:
            item = dict(row)
            raw = item.pop("value_json", None)
            if raw is not None:
                try:
                    item["value"] = json.loads(str(raw))
                except ValueError:
                    item["value"] = raw
            else:
                item["value"] = item.get("description") or ""
            # Original-text claims without an in-range evidence link are not reader-visible.
            if item.get("source_kind") == "original_text" and int(item.get("evidence_count") or 0) == 0:
                continue
            item["context_only"] = bool(
                item.get("evidence_count") and int(event["first_segment"]) < int(from_segment)
            )
            items.append(item)

    systems = []
    for system in systems_payload(connection, book_id, through_segment, from_segment):
        visible_nodes = [
            node for node in system["nodes"]
            if int(node["effective_from_segment"] or 0) <= through_segment
            and (node["effective_to_segment"] is None or int(node["effective_to_segment"]) >= from_segment)
        ]
        if visible_nodes:
            systems.append({**system, "nodes": visible_nodes})

    return {
        "event": {
            "id": int(event["id"]),
            "title": str(event["title"]),
            "summary": str(event["summary"]),
            "segment": int(event["first_segment"]),
            "chapter_title": str(event["chapter_title"]),
            "location_name": event["location_name"],
        },
        "through_segment": through_segment,
        "from_segment": from_segment,
        "items": items,
        "systems": systems,
        "missing_explanations": [
            {"entity_id": entity_id, "reason": "当前剧情涉及该对象，但还没有可读且带证据的知识说明"}
            for entity_id in entity_ids
            if not any(int(item["entity_id"]) == entity_id and str(item.get("value") or "").strip() for item in items)
        ],
    }
