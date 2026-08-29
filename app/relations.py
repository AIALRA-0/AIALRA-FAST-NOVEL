"""Deterministic relation direction rules shared by extraction, migration, and editing."""

from __future__ import annotations

from typing import Literal


RelationDirection = Literal["directed", "bidirectional"]

# These predicates are symmetric by definition when the quoted text establishes the relation.
SYMMETRIC_PREDICATES = {
    "配偶", "夫妻", "伴侣", "兄弟", "姐妹", "兄妹", "姐弟", "同胞", "亲属",
}

# These role labels have a deterministic reverse label. The original evidence is kept on the
# same claim, so the reverse arrow never becomes an independent unsupported fact.
SAFE_REVERSE_PREDICATES = {
    "父亲": "子女",
    "母亲": "子女",
    "父母": "子女",
    "儿子": "父母",
    "女儿": "父母",
    "子女": "父母",
    "师父": "徒弟",
    "师傅": "徒弟",
    "徒弟": "师父",
    "丈夫": "妻子",
    "妻子": "丈夫",
}


def normalize_relation_semantics(
    predicate: str,
    directionality: str | None = None,
    reverse_predicate: str | None = None,
) -> tuple[RelationDirection, str | None]:
    """Return a safe display direction without expanding the evidence boundary."""

    predicate = predicate.strip()
    reverse = (reverse_predicate or "").strip() or None
    requested = directionality if directionality in {"directed", "bidirectional"} else "directed"

    if predicate in SYMMETRIC_PREDICATES:
        return "bidirectional", reverse or predicate
    if predicate in SAFE_REVERSE_PREDICATES:
        return "bidirectional", reverse or SAFE_REVERSE_PREDICATES[predicate]
    if requested == "bidirectional" and reverse:
        return "bidirectional", reverse
    return "directed", None
