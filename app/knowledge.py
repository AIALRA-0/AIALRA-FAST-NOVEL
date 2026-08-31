"""Three-layer knowledge projection: claims, concepts, and reader-facing facets."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any


SYSTEM_CATEGORIES = {
    "person": "人物", "place": "地点", "faction": "势力", "item": "物品",
    "skill": "技能", "power": "力量体系", "rule": "规则", "creature": "种族",
    "background": "历史与背景", "quest": "任务", "term": "术语",
    "geography": "地理", "culture": "文化", "attribute": "属性",
    "parameter": "参数", "other": "待归类",
}


def _safe_json(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def record_revision(
    connection: sqlite3.Connection,
    book_id: int,
    target_type: str,
    target_id: int,
    action: str,
    before: Any,
    after: Any,
) -> None:
    connection.execute(
        """
        INSERT INTO knowledge_revisions(
            book_id, target_type, target_id, action, before_json, after_json, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, 'human')
        """,
        (
            book_id, target_type, target_id, action,
            json.dumps(before or {}, ensure_ascii=False, default=str),
            json.dumps(after or {}, ensure_ascii=False, default=str),
        ),
    )


def revision_payload(
    connection: sqlite3.Connection,
    book_id: int,
    target_type: str = "",
    target_id: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["book_id = ?"]
    params: list[Any] = [book_id]
    if target_type:
        clauses.append("target_type = ?")
        params.append(target_type)
    if target_id is not None:
        clauses.append("target_id = ?")
        params.append(target_id)
    params.append(limit)
    rows = connection.execute(
        f"""
        SELECT * FROM knowledge_revisions WHERE {' AND '.join(clauses)}
        ORDER BY id DESC LIMIT ?
        """,
        params,
    ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["before"] = _safe_json(item.pop("before_json"), {})
        item["after"] = _safe_json(item.pop("after_json"), {})
    return result


def sync_knowledge_projection(connection: sqlite3.Connection, book_id: int) -> None:
    roots: dict[str, int] = {}
    for category, label in SYSTEM_CATEGORIES.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO concepts(
                book_id, scheme, category, preferred_label, description, custom, created_by
            ) VALUES (?, 'system', ?, ?, ?, 0, 'system')
            """,
            (book_id, category, label, f"{label}分类"),
        )
        row = connection.execute(
            "SELECT id FROM concepts WHERE book_id = ? AND scheme = 'system' AND category = ? AND preferred_label = ?",
            (book_id, category, label),
        ).fetchone()
        if row is not None:
            roots[category] = int(row["id"])

    records: list[tuple[str, int, str, str, str, float]] = []
    for row in connection.execute(
        "SELECT id, category, title, summary, confidence FROM world_notes WHERE book_id = ?",
        (book_id,),
    ):
        records.append(("world_note", int(row["id"]), str(row["category"]), str(row["title"]), str(row["summary"]), float(row["confidence"])))
    for row in connection.execute(
        "SELECT id, category, name, summary, confidence FROM entries WHERE book_id = ?",
        (book_id,),
    ):
        records.append(("entry", int(row["id"]), str(row["category"]), str(row["name"]), str(row["summary"]), float(row["confidence"])))
    for row in connection.execute(
        "SELECT id, kind, name, summary, importance FROM entities WHERE book_id = ?",
        (book_id,),
    ):
        records.append(("entity", int(row["id"]), str(row["kind"]), str(row["name"]), str(row["summary"]), float(row["importance"])))

    for subject_type, subject_id, raw_category, label, summary, confidence in records:
        category = raw_category if raw_category in SYSTEM_CATEGORIES else "other"
        status = "needs_classification" if category == "other" else "active"
        connection.execute(
            """
            INSERT OR IGNORE INTO concepts(
                book_id, scheme, category, preferred_label, description, custom, status, created_by
            ) VALUES (?, 'book', ?, ?, ?, 0, ?, 'projection')
            """,
            (book_id, category, label, summary, status),
        )
        concept = connection.execute(
            """
            SELECT id FROM concepts
            WHERE book_id = ? AND scheme = 'book' AND category = ? AND preferred_label = ?
            """,
            (book_id, category, label),
        ).fetchone()
        if concept is None:
            continue
        concept_id = int(concept["id"])
        root_id = roots.get(category) or roots.get("other")
        if root_id is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO concept_relations(
                    book_id, source_concept_id, target_concept_id, relation, created_by
                ) VALUES (?, ?, ?, 'broader', 'projection')
                """,
                (book_id, concept_id, root_id),
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO knowledge_claims(
                book_id, concept_id, subject_type, subject_id, predicate, value_json,
                status, confidence, source_kind, created_by
            ) VALUES (?, ?, ?, ?, 'summary', ?, 'accepted', ?, 'original_text', 'projection')
            """,
            (book_id, concept_id, subject_type, subject_id, json.dumps(summary, ensure_ascii=False), confidence),
        )
        claim = connection.execute(
            """
            SELECT id FROM knowledge_claims
            WHERE book_id = ? AND subject_type = ? AND subject_id = ? AND predicate = 'summary'
              AND value_json = ?
            """,
            (book_id, subject_type, subject_id, json.dumps(summary, ensure_ascii=False)),
        ).fetchone()
        if claim is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_claim_evidence(knowledge_claim_id, evidence_id)
                SELECT ?, id FROM evidence WHERE book_id = ? AND target_type = ? AND target_id = ?
                """,
                (int(claim["id"]), book_id, subject_type, subject_id),
            )


def concept_payload(
    connection: sqlite3.Connection,
    book_id: int,
    *,
    query: str = "",
    category: str = "",
    status: str = "active",
    limit: int = 200,
) -> list[dict[str, Any]]:
    sync_knowledge_projection(connection, book_id)
    clauses = ["concept.book_id = ?"]
    params: list[Any] = [book_id]
    if query:
        clauses.append("(concept.preferred_label LIKE ? OR concept.description LIKE ? OR concept.aliases_json LIKE ?)")
        pattern = f"%{query}%"
        params.extend([pattern, pattern, pattern])
    if category:
        clauses.append("concept.category = ?")
        params.append(category)
    if status:
        clauses.append("concept.status = ?")
        params.append(status)
    params.append(limit)
    rows = connection.execute(
        f"""
        SELECT concept.*,
            COUNT(DISTINCT claim.id) AS claim_count,
            COUNT(DISTINCT evidence.id) AS evidence_count,
            MIN(segment.ordinal) AS first_segment,
            parent.target_concept_id AS parent_concept_id,
            parent_concept.preferred_label AS parent_label
        FROM concepts concept
        LEFT JOIN knowledge_claims claim ON claim.concept_id = concept.id
        LEFT JOIN knowledge_claim_evidence link ON link.knowledge_claim_id = claim.id
        LEFT JOIN evidence ON evidence.id = link.evidence_id
        LEFT JOIN segments segment ON segment.id = evidence.segment_id
        LEFT JOIN concept_relations parent ON parent.source_concept_id = concept.id AND parent.relation = 'broader'
        LEFT JOIN concepts parent_concept ON parent_concept.id = parent.target_concept_id
        WHERE {' AND '.join(clauses)}
        GROUP BY concept.id
        ORDER BY concept.scheme = 'system' DESC, concept.category, concept.preferred_label
        LIMIT ?
        """,
        params,
    ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["aliases"] = _safe_json(item.pop("aliases_json"), [])
    return result


def knowledge_claim_payload(connection: sqlite3.Connection, book_id: int, concept_id: int | None = None) -> list[dict[str, Any]]:
    sync_knowledge_projection(connection, book_id)
    params: list[Any] = [book_id]
    concept_filter = ""
    if concept_id is not None:
        concept_filter = " AND claim.concept_id = ?"
        params.append(concept_id)
    rows = connection.execute(
        f"""
        SELECT claim.*, concept.preferred_label AS concept_label, concept.category,
            COUNT(DISTINCT link.evidence_id) AS evidence_count
        FROM knowledge_claims claim
        JOIN concepts concept ON concept.id = claim.concept_id
        LEFT JOIN knowledge_claim_evidence link ON link.knowledge_claim_id = claim.id
        WHERE claim.book_id = ?{concept_filter}
        GROUP BY claim.id ORDER BY concept.category, concept.preferred_label, claim.id
        """,
        params,
    ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        raw_value = item.pop("value_json")
        item["value"] = _safe_json(raw_value, raw_value)
        qualifiers = connection.execute(
            "SELECT qualifier_key, qualifier_value_json FROM claim_qualifiers WHERE knowledge_claim_id = ? ORDER BY id",
            (item["id"],),
        ).fetchall()
        item["qualifiers"] = {str(row["qualifier_key"]): _safe_json(row["qualifier_value_json"], row["qualifier_value_json"]) for row in qualifiers}
    return result


def facet_payload(connection: sqlite3.Connection, book_id: int) -> dict[str, Any]:
    concepts = concept_payload(connection, book_id, status="", limit=10_000)
    category_counts = Counter(str(item["category"]) for item in concepts if item["scheme"] != "system")
    status_counts = Counter(str(item["status"]) for item in concepts if item["scheme"] != "system")
    evidence_total = sum(int(item["evidence_count"] or 0) for item in concepts if item["scheme"] != "system")
    return {
        "categories": [{"key": key, "label": SYSTEM_CATEGORIES.get(key, key), "count": count} for key, count in sorted(category_counts.items())],
        "statuses": [{"key": key, "count": count} for key, count in sorted(status_counts.items())],
        "concept_count": sum(1 for item in concepts if item["scheme"] != "system"),
        "evidence_link_count": evidence_total,
        "needs_classification": status_counts.get("needs_classification", 0),
    }
