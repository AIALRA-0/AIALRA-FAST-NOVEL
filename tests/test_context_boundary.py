"""验证模型运行上下文遵守当前片段边界，不把后文事实泄漏给前文。"""

from __future__ import annotations

from pathlib import Path

from app.consolidation import build_analysis_context
from app.db import initialize, transaction


def test_analysis_context_excludes_future_entities_events_and_stale_full_book_memory(tmp_path: Path) -> None:
    """重跑第一章时，即使全书记忆已经生成，也只能看到第一章之前的事实。"""

    database = tmp_path / "context-boundary.db"
    initialize(database)
    with transaction(database) as connection:
        book_id = int(connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('边界测试', 'txt', 'context-boundary', 'context.txt', 3, 18)
            """
        ).lastrowid)
        connection.executemany(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (book_id, 0, "第一章", "context-0", "开篇。", 0, 3),
                (book_id, 1, "第二章", "context-1", "中段。", 3, 6),
                (book_id, 2, "第三章", "context-2", "终章。", 6, 9),
            ],
        )
        connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, first_segment) VALUES (?, 'person', '后文人物', '第三章才出现', 2)",
            (book_id,),
        )
        connection.execute(
            """
            INSERT INTO events(book_id, title, summary, narrative_order, story_order,
                temporal_kind, temporal_value, confidence, first_segment)
            VALUES (?, '后文事件', '第三章才发生', 2, 2, 'relative', '第三日', 0.9, 2)
            """,
            (book_id,),
        )
        connection.execute(
            "INSERT INTO book_memory(book_id, through_segment, summary) VALUES (?, 2, '后文事件和后文人物')",
            (book_id,),
        )
        first_context = build_analysis_context(connection, book_id, 0)
        final_context = build_analysis_context(connection, book_id, 3)
    assert "后文人物" not in first_context
    assert "后文事件" not in first_context
    assert "后文人物" in final_context
    assert "后文事件" in final_context
