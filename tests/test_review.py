"""验证全书整理结果必须绑定已有事实和原文证据。"""

from __future__ import annotations

from pathlib import Path

from app.db import connect, initialize, transaction
from app.models import GlobalReviewResult, GlobalSynthesisCandidate
from app.review import FactReference, persist_global_review


def test_global_synthesis_copies_basis_evidence(tmp_path: Path) -> None:
    """综合世界说明会继承基础事实的逐字引文。"""

    path = tmp_path / "review.db"
    initialize(path)
    with transaction(path) as connection:
        book_id = int(connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('整理测试', 'txt', 'review-hash', 'review.txt', 1, 8)
            """
        ).lastrowid)
        segment_id = int(connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, 0, '第一章', 'review-seg', '雾门只能开启一次。', 0, 9)
            """,
            (book_id,),
        ).lastrowid)
        entity_id = int(connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment)
            VALUES (?, 'other', '雾门', '一次性通道。', 0.8, 0)
            """,
            (book_id,),
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end)
            VALUES (?, 'entity', ?, ?, '雾门只能开启一次。', 0, 9)
            """,
            (book_id, entity_id, segment_id),
        )
        fact = FactReference("E1", "entity", entity_id, 0, "E1|实体|other|雾门|一次性通道")
        result = GlobalReviewResult(
            syntheses=[
                GlobalSynthesisCandidate(
                    category="rule",
                    title="雾门使用限制",
                    summary="雾门属于一次性通道。",
                    basis_keys=["E1"],
                    confidence=0.9,
                )
            ]
        )
        persist_global_review(connection, book_id, result, {"E1": fact})
    with connect(path) as connection:
        note = connection.execute("SELECT * FROM world_notes WHERE created_by = 'synthesis'").fetchone()
        evidence = connection.execute(
            "SELECT * FROM evidence WHERE target_type = 'world_note' AND target_id = ?",
            (note["id"],),
        ).fetchall()
    assert note["title"] == "雾门使用限制"
    assert evidence[0]["quote"] == "雾门只能开启一次。"
