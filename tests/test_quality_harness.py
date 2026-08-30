"""验证质量复核在供应商边界故障下仍能闭环。"""

from pathlib import Path

from app.db import initialize, transaction
from app.models import ConnectivityAuditDecision, ConnectivityRelationCandidate
from app.quality_harness import (
    _persist_audit_decisions,
    _single_target_length_fallback,
    repair_explicit_named_relations,
)


def test_single_target_length_failure_becomes_resolvable_ambiguity() -> None:
    """单项无法再拆分时应进入可处理歧义，而不是让整本书悬挂。"""

    result = _single_target_length_fallback({"target_entities": [{"entity_id": 42}]})

    assert result is not None
    assert result.decisions[0].entity_id == 42
    assert result.decisions[0].status == "ambiguous"
    assert result.decisions[0].relations == []


def test_multi_target_length_failure_still_requests_split() -> None:
    """多项批次仍由自适应拆分处理，不能提前降级。"""

    result = _single_target_length_fallback(
        {"target_entities": [{"entity_id": 1}, {"entity_id": 2}]}
    )

    assert result is None


def test_explicit_teacher_relation_repairs_missing_named_entity(tmp_path: Path) -> None:
    """逐字写明的师徒关系应补回漏抽取对象，并保存同一段原文证据。"""

    database = tmp_path / "quality.db"
    initialize(database)
    quote = "華光菩薩是火焰五光佛的徒弟。"
    with transaction(database) as connection:
        book_id = int(connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('测试书', 'txt', 'quality-test', 'test.txt', 1, ?)
            """,
            (len(quote),),
        ).lastrowid)
        segment_id = int(connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, 0, '第一章', 'test-segment', ?, 0, ?)
            """,
            (book_id, quote, len(quote)),
        ).lastrowid)
        subject_id = int(connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
            VALUES (?, 'person', '华光菩萨', '华光菩萨是火焰五光佛的徒弟。', 1, 0, 'model')
            """,
            (book_id,),
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end)
            VALUES (?, 'entity', ?, ?, ?, 0, ?)
            """,
            (book_id, subject_id, segment_id, quote, len(quote)),
        )

        repaired = repair_explicit_named_relations(connection, book_id)
        target = connection.execute(
            "SELECT id FROM entities WHERE book_id = ? AND name = '火焰五光佛'",
            (book_id,),
        ).fetchone()
        claim = connection.execute(
            """
            SELECT id FROM claims WHERE book_id = ? AND source_entity_id = ?
              AND target_entity_id = ? AND predicate = '徒弟'
            """,
            (book_id, subject_id, int(target["id"])),
        ).fetchone()

        assert repaired == 1
        assert target is not None
        assert claim is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE target_type = 'claim' AND target_id = ?",
            (claim["id"],),
        ).fetchone()[0] == 1


def test_model_relation_uses_current_entity_when_names_are_duplicated(tmp_path: Path) -> None:
    """同名人物与势力并存时，复核结果必须连接正在审核的那个节点。"""

    database = tmp_path / "duplicate.db"
    initialize(database)
    source_text = "大圣调出四个\n健将列阵迎敌。"
    model_quote = "大圣调出四个健将列阵迎敌。"
    with transaction(database) as connection:
        book_id = int(connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('测试书', 'txt', 'duplicate-test', 'test.txt', 1, ?)
            """,
            (len(source_text),),
        ).lastrowid)
        segment_id = int(connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, 0, '第一章', 'duplicate-segment', ?, 0, ?)
            """,
            (book_id, source_text, len(source_text)),
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
            VALUES (?, 'person', '四健将', '同名误抽取。', 0.2, 0, 'model')
            """,
            (book_id,),
        )
        reviewed_id = int(connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
            VALUES (?, 'faction', '四健将', '正在复核的势力。', 1, 0, 'model')
            """,
            (book_id,),
        ).lastrowid)
        target_id = int(connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
            VALUES (?, 'person', '孙悟空', '主角。', 1, 0, 'model')
            """,
            (book_id,),
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO entity_connectivity_reviews(book_id, entity_id, status, source_segment_count)
            VALUES (?, ?, 'ambiguous', 1)
            """,
            (book_id, reviewed_id),
        )
        decision = ConnectivityAuditDecision(
            entity_id=reviewed_id,
            status="connected",
            reason="原文明确受孙悟空调遣。",
            confidence=0.95,
            relations=[
                ConnectivityRelationCandidate(
                    source="四健将",
                    target="孙悟空",
                    predicate="隶属",
                    summary="四健将受孙悟空调遣迎敌。",
                    confidence=0.95,
                    segment_id=segment_id,
                    evidence_quote=model_quote,
                )
            ],
        )

        _persist_audit_decisions(connection, book_id, {reviewed_id: 1}, {reviewed_id: [decision]})

        claim = connection.execute(
            "SELECT source_entity_id, target_entity_id FROM claims WHERE book_id = ?",
            (book_id,),
        ).fetchone()
        assert claim is not None
        assert int(claim["source_entity_id"]) == reviewed_id
        assert int(claim["target_entity_id"]) == target_id
