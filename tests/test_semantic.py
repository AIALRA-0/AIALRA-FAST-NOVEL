"""验证身份、时间、行程和成本门禁的关键不变量。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from app import main
from app.db import connect, initialize, transaction
from app.jobs import create_job
from app.semantic import (
    auto_resolve_identity_candidates,
    ensure_identity_clusters,
    rebuild_derived_journey,
    recompute_chronology_dag,
    repair_explicit_kinship,
    undo_identity_decision,
)


def test_explicit_parent_name_creates_evidenced_relationship(tmp_path: Path) -> None:
    """“雷的父亲”已经明确关系方向，系统应连接父亲与别名为雷的人物。"""

    path = tmp_path / "kinship.db"
    book_id = create_semantic_book(path)
    with transaction(path) as connection:
        child = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '修雷·诺赞', '雷。', 1, 0)",
            (book_id,),
        ).lastrowid)
        connection.execute("INSERT INTO aliases(entity_id, alias) VALUES (?, '雷')", (child,))
        parent = int(connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '雷的父亲', '雷的父亲应召入伍。', 0.6, 0)",
            (book_id,),
        ).lastrowid)
        segment_id = connection.execute(
            "SELECT id FROM segments WHERE book_id = ? AND ordinal = 0", (book_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end) VALUES (?, 'entity', ?, ?, '爸爸应召入伍', 0, 6)",
            (book_id, parent, segment_id),
        )
        assert repair_explicit_kinship(connection, book_id) == 1
        claim = connection.execute(
            "SELECT * FROM claims WHERE source_entity_id = ? AND target_entity_id = ?",
            (parent, child),
        ).fetchone()
        evidence = connection.execute(
            "SELECT COUNT(*) FROM evidence WHERE target_type = 'claim' AND target_id = ?",
            (claim["id"],),
        ).fetchone()[0]
    assert claim["predicate"] == "父亲"
    assert claim["status"] == "accepted"
    assert evidence == 1


def create_semantic_book(path: Path) -> int:
    """建立包含三个章节的最小真实结构。"""

    initialize(path)
    with transaction(path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('身份时间测试', 'txt', 'semantic-book', 'semantic.txt', 3, 30)
            """
        )
        book_id = int(cursor.lastrowid)
        connection.executemany(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (book_id, 0, '第一章', 'chapter-1', '石猴出世。', 0, 5),
                (book_id, 1, '第二章', 'chapter-2', '玄奘离开长安。', 5, 13),
                (book_id, 2, '第三章', 'chapter-3', '唐僧到达两界山。', 13, 22),
            ],
        )
    return book_id


def test_identity_merge_is_reversible_and_keeps_raw_entities(tmp_path: Path) -> None:
    """明确别名会自动归入同一身份，撤销后两条原始记录都仍存在。"""

    path = tmp_path / "identity.db"
    book_id = create_semantic_book(path)
    with transaction(path) as connection:
        first = connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment)
            VALUES (?, 'person', '玄奘', '奉命西行的僧人。', 1, 1)
            """,
            (book_id,),
        ).lastrowid
        second = connection.execute(
            """
            INSERT INTO entities(book_id, kind, name, summary, importance, first_segment)
            VALUES (?, 'person', '唐僧', '奉命西行的僧人。', 1, 2)
            """,
            (book_id,),
        ).lastrowid
        connection.execute("INSERT INTO aliases(entity_id, alias) VALUES (?, '唐僧')", (first,))
        connection.execute(
            """
            INSERT INTO entity_merge_candidates(book_id, left_entity_id, right_entity_id, reason, confidence)
            VALUES (?, ?, ?, '明确别名', 0.9)
            """,
            (book_id, first, second),
        )
        result = auto_resolve_identity_candidates(connection, book_id)
        decision = connection.execute(
            "SELECT id FROM identity_decisions WHERE book_id = ? AND verdict = 'merge'",
            (book_id,),
        ).fetchone()
        assert result["merged"] == 1
        assert connection.execute("SELECT COUNT(*) FROM entities WHERE book_id = ?", (book_id,)).fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM identity_cluster_members WHERE entity_id IN (?, ?)",
            (first, second),
        ).fetchone()[0] == 1
        undo_identity_decision(connection, int(decision["id"]))
        assert connection.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM identity_cluster_members WHERE entity_id IN (?, ?)",
            (first, second),
        ).fetchone()[0] == 2


def test_distinct_roles_block_automatic_identity_merge(tmp_path: Path) -> None:
    """同一事件中的不同角色属于主体冲突，系统必须自动分开。"""

    path = tmp_path / "identity-conflict.db"
    book_id = create_semantic_book(path)
    with transaction(path) as connection:
        left = connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '甲', '甲方。', 1, 0)",
            (book_id,),
        ).lastrowid
        right = connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '乙', '乙方。', 1, 0)",
            (book_id,),
        ).lastrowid
        event = connection.execute(
            """
            INSERT INTO events(book_id, title, summary, narrative_order, story_order, temporal_kind,
                confidence, first_segment) VALUES (?, '甲追逐乙', '甲追逐乙。', 0, 0, 'unknown', 1, 0)
            """,
            (book_id,),
        ).lastrowid
        connection.executemany(
            "INSERT INTO event_participants(event_id, entity_id, role) VALUES (?, ?, ?)",
            [(event, left, '追逐者'), (event, right, '被追逐者')],
        )
        connection.execute("INSERT INTO aliases(entity_id, alias) VALUES (?, '乙')", (left,))
        connection.execute(
            "INSERT INTO entity_merge_candidates(book_id, left_entity_id, right_entity_id, reason, confidence) VALUES (?, ?, ?, '共享称呼', 0.99)",
            (book_id, left, right),
        )
        result = auto_resolve_identity_candidates(connection, book_id)
        assert result["separate"] == 1
        assert connection.execute(
            "SELECT COUNT(DISTINCT cluster_id) FROM identity_cluster_members WHERE entity_id IN (?, ?)",
            (left, right),
        ).fetchone()[0] == 2


def test_chronology_ignores_model_rank_and_isolates_cycle(tmp_path: Path) -> None:
    """石猴出世保留在开端，成环的最后一条时间约束不会生效。"""

    path = tmp_path / "chronology.db"
    book_id = create_semantic_book(path)
    with transaction(path) as connection:
        event_ids = []
        for narrative_order, title, model_rank in (
            (0, '石猴出世', 50),
            (100, '拜师学艺', 1),
            (200, '大闹天宫', 2),
        ):
            event_ids.append(
                connection.execute(
                    """
                    INSERT INTO events(book_id, title, summary, narrative_order, story_order,
                        temporal_kind, confidence, first_segment)
                    VALUES (?, ?, ?, ?, ?, 'unknown', 1, ?)
                    """,
                    (book_id, title, title, narrative_order, model_rank, narrative_order // 100),
                ).lastrowid
            )
        connection.executemany(
            """
            INSERT INTO event_order_edges(book_id, earlier_event_id, later_event_id, relation, confidence)
            VALUES (?, ?, ?, 'before', ?)
            """,
            [
                (book_id, event_ids[0], event_ids[1], 1.0),
                (book_id, event_ids[1], event_ids[2], 0.9),
                (book_id, event_ids[2], event_ids[0], 0.5),
            ],
        )
        report = recompute_chronology_dag(connection, book_id)
        ordered = connection.execute(
            "SELECT title FROM events WHERE book_id = ? ORDER BY story_order",
            (book_id,),
        ).fetchall()
        assert [row["title"] for row in ordered] == ['石猴出世', '拜师学艺', '大闹天宫']
        assert report["conflict_edges"] == 1


def test_journey_keeps_unknown_path_instead_of_dropping_nodes(tmp_path: Path) -> None:
    """地点之间缺少移动说明时保留节点，并把路线标为未知缺口。"""

    path = tmp_path / "journey.db"
    book_id = create_semantic_book(path)
    with transaction(path) as connection:
        person = connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'person', '行者', '远行者。', 1, 0)",
            (book_id,),
        ).lastrowid
        first_place = connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'place', '长安', '起点。', 1, 0)",
            (book_id,),
        ).lastrowid
        second_place = connection.execute(
            "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'place', '两界山', '后续地点。', 1, 2)",
            (book_id,),
        ).lastrowid
        ensure_identity_clusters(connection, book_id)
        event_rows = [
            ('离开长安', 0, first_place, 0),
            ('途中遇险', 100, None, 1),
            ('到达两界山', 200, second_place, 2),
        ]
        for title, order, place, segment in event_rows:
            event = connection.execute(
                """
                INSERT INTO events(book_id, title, summary, narrative_order, story_order,
                    temporal_kind, confidence, location_entity_id, first_segment)
                VALUES (?, ?, ?, ?, ?, 'unknown', 1, ?, ?)
                """,
                (book_id, title, title, order, order, place, segment),
            ).lastrowid
            connection.execute(
                "INSERT INTO event_participants(event_id, entity_id, role) VALUES (?, ?, '主体')",
                (event, person),
            )
        result = rebuild_derived_journey(connection, book_id)
        # 第一处已知地点只放置人物，不生成“地点到自身”的伪路线。
        assert result["legs"] == 1
        assert result["gaps"] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM journey_legs WHERE book_id = ? AND gap_status = 'unknown_path'",
            (book_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM journey_legs WHERE book_id = ? AND from_entity_id = to_entity_id",
            (book_id,),
        ).fetchone()[0] == 0


def test_job_is_created_paused_when_preflight_exceeds_budget(tmp_path: Path) -> None:
    """人工预算模式超过令牌上限时，任务在任何模型请求前暂停。"""

    path = tmp_path / "budget.db"
    book_id = create_semantic_book(path)
    settings = replace(main.settings, database_path=path, deepseek_api_key=None, moonshot_api_key=None)
    job = create_job(
        settings,
        book_id,
        'mock',
        0,
        2,
        1,
        True,
        max_cost_usd=0,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        review_mode='local',
        budget_mode='manual',
    )
    assert job["status"] == 'paused'
    assert job["budget_status"] == 'blocked'
    with connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_call_ledger").fetchone()[0] == 0


def test_adaptive_job_expands_preflight_budget_without_pausing(tmp_path: Path) -> None:
    """自动预算模式根据整书预估扩大范围，并直接进入队列。"""

    path = tmp_path / "adaptive-budget.db"
    book_id = create_semantic_book(path)
    settings = replace(main.settings, database_path=path, deepseek_api_key=None, moonshot_api_key=None)
    job = create_job(
        settings,
        book_id,
        "mock",
        0,
        2,
        1,
        True,
        max_cost_usd=0,
        max_input_tokens=1_000,
        max_output_tokens=1_000,
        review_mode="local",
        budget_mode="adaptive",
    )
    assert job["status"] == "queued"
    assert job["budget_status"] == "auto_expanded"
    assert job["budget_adjustments"] == 1
    assert job["max_input_tokens"] > 1_000
    assert job["max_output_tokens"] > 1_000
