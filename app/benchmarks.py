"""真实小说金标准用例和可复算准确率门禁。"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.semantic import cluster_for_entity, select_main_subject


XIYOUJI_CASES: tuple[dict[str, Any], ...] = (
    {"case_type": "identity_same", "subject": "玄奘=陈玄奘", "expected": {"left": "玄奘", "right": "陈玄奘"}, "source": 22, "critical": 1},
    {"case_type": "identity_same", "subject": "玄奘=唐僧", "expected": {"left": "玄奘", "right": "唐僧"}, "source": 27, "critical": 1},
    {"case_type": "identity_same", "subject": "石猴=孙悟空", "expected": {"left": "石猴", "right": "孙悟空"}, "source": 1, "critical": 1},
    {"case_type": "identity_same", "subject": "猪八戒=猪悟能", "expected": {"left": "猪八戒", "right": "猪悟能"}, "source": 15, "critical": 1},
    {"case_type": "identity_same", "subject": "沙僧=沙悟净", "expected": {"left": "沙僧", "right": "沙悟净"}, "source": 49, "critical": 1},
    {"case_type": "identity_distinct", "subject": "孙悟空≠唐僧", "expected": {"left": "孙悟空", "right": "唐僧"}, "source": 27, "critical": 1},
    {"case_type": "identity_distinct", "subject": "孙悟空≠猪八戒", "expected": {"left": "孙悟空", "right": "猪八戒"}, "source": 39, "critical": 1},
    {"case_type": "identity_distinct", "subject": "唐僧≠猪八戒", "expected": {"left": "唐僧", "right": "猪八戒"}, "source": 39, "critical": 1},
    {"case_type": "identity_distinct", "subject": "观音菩萨≠如来佛祖", "expected": {"left": "观音菩萨", "right": "如来佛祖"}, "source": 14, "critical": 1},
    {"case_type": "identity_distinct", "subject": "牛魔王≠红孩儿", "expected": {"left": "牛魔王", "right": "红孩儿"}, "source": 84, "critical": 1},
    {"case_type": "event_present", "subject": "石猴出世", "expected": {"title": "石猴出世", "max_segment": 0}, "source": 0, "critical": 1},
    {"case_type": "event_present", "subject": "大闹天宫", "expected": {"title": "大闹天宫", "max_segment": 12}, "source": 12, "critical": 1},
    {"case_type": "event_present", "subject": "揭帖救出孙悟空", "expected": {"title": "揭帖救出孙悟空", "max_segment": 27}, "source": 27, "critical": 1},
    {"case_type": "event_present", "subject": "白骨精第一次变化", "expected": {"title": "白骨精第一次变化", "max_segment": 54}, "source": 53, "critical": 1},
    {"case_type": "event_present", "subject": "红孩儿三昧真火", "expected": {"title": "红孩儿放三昧真火", "max_segment": 83}, "source": 82, "critical": 1},
    {"case_type": "event_present", "subject": "扇熄火焰山", "expected": {"title": "扇熄火焰山", "max_segment": 124}, "source": 123, "critical": 1},
    {"case_type": "event_before", "subject": "石猴出世早于大闹天宫", "expected": {"earlier": "石猴出世", "later": "大闹天宫"}, "source": 12, "critical": 1},
    {"case_type": "event_before", "subject": "大闹天宫早于压五行山", "expected": {"earlier": "大闹天宫", "later": "压孙悟空于五行山"}, "source": 12, "critical": 1},
    {"case_type": "event_before", "subject": "压五行山早于揭帖", "expected": {"earlier": "压孙悟空于五行山", "later": "揭帖救出孙悟空"}, "source": 27, "critical": 1},
    {"case_type": "event_before", "subject": "揭帖早于白骨精", "expected": {"earlier": "揭帖救出孙悟空", "later": "白骨精第一次变化"}, "source": 53, "critical": 1},
    {"case_type": "event_before", "subject": "白骨精早于红孩儿", "expected": {"earlier": "白骨精第一次变化", "later": "红孩儿放三昧真火"}, "source": 82, "critical": 1},
    {"case_type": "event_before", "subject": "红孩儿早于火焰山", "expected": {"earlier": "红孩儿放三昧真火", "later": "扇熄火焰山"}, "source": 123, "critical": 1},
    {"case_type": "event_before", "subject": "火焰山早于传播真经", "expected": {"earlier": "扇熄火焰山", "later": "传播真经"}, "source": 201, "critical": 1},
    {"case_type": "main_subject", "subject": "主线人物包含孙悟空", "expected": {"name": "孙悟空"}, "source": 1, "critical": 1},
    {"case_type": "journey_start", "subject": "主线行程从开篇开始", "expected": {"max_segment": 3}, "source": 1, "critical": 1},
    {"case_type": "segment_accounting", "subject": "全书片段无缺漏", "expected": {"percent": 100}, "source": 202, "critical": 0},
    {"case_type": "fact_evidence", "subject": "正式事实全部有证据", "expected": {"percent": 100}, "source": 202, "critical": 0},
    {"case_type": "quote_integrity", "subject": "证据逐字存在于原文", "expected": {"percent": 100}, "source": 202, "critical": 0},
)


def _entity_ids(connection: sqlite3.Connection, book_id: int, name: str) -> list[int]:
    rows = connection.execute(
        """
        SELECT DISTINCT e.id FROM entities e
        LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? AND (e.name = ? OR a.alias = ?)
        """,
        (book_id, name, name),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def _event_rank(connection: sqlite3.Connection, book_id: int, title_part: str) -> tuple[int, float] | None:
    row = connection.execute(
        """
        SELECT id, story_order FROM events WHERE book_id = ? AND title LIKE ?
        ORDER BY story_order, narrative_order, id LIMIT 1
        """,
        (book_id, f"%{title_part}%"),
    ).fetchone()
    return (int(row["id"]), float(row["story_order"])) if row is not None else None


def seed_benchmark_cases(connection: sqlite3.Connection, book_id: int) -> int:
    """Prepare deterministic Journey to the West candidates for later human review."""

    book = connection.execute(
        "SELECT title, original_filename FROM books WHERE id = ?",
        (book_id,),
    ).fetchone()
    if book is None:
        return 0
    identity = f"{book['title']} {book['original_filename']}".lower()
    if "西游记" not in identity and "xiyouji" not in identity:
        return 0
    for case in XIYOUJI_CASES:
        connection.execute(
            """
            INSERT INTO quality_benchmark_cases(
                book_id, case_type, subject, expected_json, source_segment, note, critical,
                suite_name, origin, holdout, confirmed_by_user, failure_category, review_status,
                second_review_status
            ) VALUES (?, ?, ?, ?, ?, '系统根据《西游记》固定题库准备的待人工核对候选', ?,
                'real-novel-gold', 'agent_seeded_candidate', 0, 0, 'xiyouji-core', 'candidate',
                'not_required')
            ON CONFLICT(book_id, case_type, subject, source_segment) DO UPDATE SET
                expected_json = excluded.expected_json, critical = excluded.critical,
                note = excluded.note, suite_name = excluded.suite_name,
                origin = excluded.origin, holdout = excluded.holdout,
                confirmed_by_user = excluded.confirmed_by_user,
                failure_category = excluded.failure_category,
                review_status = excluded.review_status,
                second_review_status = excluded.second_review_status,
                reviewer_id = '', reviewer_role = '', review_session = '',
                review_evidence_hash = '', reviewed_at = NULL,
                second_reviewer_id = '', second_reviewed_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                book_id, case["case_type"], case["subject"],
                json.dumps(case["expected"], ensure_ascii=False), case["source"], case["critical"],
            ),
        )
    return len(XIYOUJI_CASES)


def _evaluate_case(
    connection: sqlite3.Connection,
    book_id: int,
    case_type: str,
    expected: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    if case_type in {"identity_same", "identity_distinct"}:
        left_ids = _entity_ids(connection, book_id, str(expected["left"]))
        right_ids = _entity_ids(connection, book_id, str(expected["right"]))
        left_clusters = {cluster_for_entity(connection, entity_id) for entity_id in left_ids}
        right_clusters = {cluster_for_entity(connection, entity_id) for entity_id in right_ids}
        shared = bool((left_clusters - {None}) & (right_clusters - {None}))
        passed = shared if case_type == "identity_same" else bool(left_ids and right_ids and not shared)
        return passed, {"left_ids": left_ids, "right_ids": right_ids, "shared_identity": shared}
    if case_type == "event_present":
        row = connection.execute(
            """
            SELECT id, title, first_segment FROM events
            WHERE book_id = ? AND title LIKE ? AND first_segment <= ?
            ORDER BY first_segment, id LIMIT 1
            """,
            (book_id, f"%{expected['title']}%", int(expected["max_segment"])),
        ).fetchone()
        return row is not None, dict(row) if row is not None else {"found": False}
    if case_type == "event_before":
        earlier = _event_rank(connection, book_id, str(expected["earlier"]))
        later = _event_rank(connection, book_id, str(expected["later"]))
        passed = earlier is not None and later is not None and earlier[1] < later[1]
        return passed, {"earlier": earlier, "later": later}
    if case_type == "main_subject":
        subject_id = select_main_subject(connection, book_id)
        wanted_ids = _entity_ids(connection, book_id, str(expected["name"]))
        subject_cluster = cluster_for_entity(connection, subject_id) if subject_id else None
        passed = subject_cluster is not None and any(cluster_for_entity(connection, entity_id) == subject_cluster for entity_id in wanted_ids)
        return passed, {"subject_entity_id": subject_id, "expected_entity_ids": wanted_ids}
    if case_type == "journey_start":
        row = connection.execute(
            "SELECT MIN(first_segment) AS first_segment FROM journey_legs WHERE book_id = ? AND created_by = 'derived'",
            (book_id,),
        ).fetchone()
        first_segment = row["first_segment"] if row is not None else None
        return first_segment is not None and int(first_segment) <= int(expected["max_segment"]), {"first_segment": first_segment}
    if case_type == "segment_accounting":
        row = connection.execute(
            """
            SELECT b.segment_count, COUNT(DISTINCT r.segment_id) AS processed
            FROM books b LEFT JOIN segment_results r ON r.book_id = b.id WHERE b.id = ? GROUP BY b.id
            """,
            (book_id,),
        ).fetchone()
        percent = round(int(row["processed"] or 0) / int(row["segment_count"] or 1) * 100, 2)
        return percent >= float(expected["percent"]), {"percent": percent}
    if case_type == "fact_evidence":
        missing = 0
        total = 0
        for target_type, table in (
            ("entity", "entities"), ("claim", "claims"), ("place_relation", "place_relations"),
            ("event", "events"), ("journey_leg", "journey_legs"),
            ("world_note", "world_notes"), ("entry", "entries"),
        ):
            rows = connection.execute(f"SELECT id FROM {table} WHERE book_id = ?", (book_id,)).fetchall()  # noqa: S608
            total += len(rows)
            missing += sum(
                1
                for row in rows
                if connection.execute(
                    "SELECT 1 FROM evidence WHERE target_type = ? AND target_id = ? LIMIT 1",
                    (target_type, row["id"]),
                ).fetchone() is None
            )
        percent = round((total - missing) / max(1, total) * 100, 2)
        return percent >= float(expected["percent"]), {"percent": percent, "missing": missing, "total": total}
    if case_type == "quote_integrity":
        rows = connection.execute(
            """
            SELECT x.quote, s.text FROM evidence x JOIN segments s ON s.id = x.segment_id
            WHERE x.book_id = ?
            """,
            (book_id,),
        ).fetchall()
        invalid = sum(1 for row in rows if str(row["quote"]) not in str(row["text"]))
        percent = round((len(rows) - invalid) / max(1, len(rows)) * 100, 2)
        return percent >= float(expected["percent"]), {"percent": percent, "invalid": invalid, "total": len(rows)}
    return False, {"error": "unknown_case_type"}


def evaluate_benchmarks(connection: sqlite3.Connection, book_id: int) -> dict[str, Any]:
    """执行已登记用例并写回实际值，供页面和发布门禁读取。"""

    cases = connection.execute(
        """
        SELECT * FROM quality_benchmark_cases
        WHERE book_id = ? AND confirmed_by_user = 1
          AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
        ORDER BY critical DESC, id
        """,
        (book_id,),
    ).fetchall()
    passed = 0
    critical_failed = 0
    for case in cases:
        expected = json.loads(str(case["expected_json"]))
        result, actual = _evaluate_case(connection, book_id, str(case["case_type"]), expected)
        connection.execute(
            """
            UPDATE quality_benchmark_cases SET actual_json = ?, passed = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (json.dumps(actual, ensure_ascii=False), int(result), case["id"]),
        )
        passed += int(result)
        critical_failed += int(bool(case["critical"]) and not result)
    total = len(cases)
    accuracy = round(passed / total * 100, 2) if total else None
    return {
        "total": total,
        "passed": passed,
        "accuracy_percent": accuracy,
        "critical_failed": critical_failed,
        "gate_passed": bool(total >= 25 and accuracy is not None and accuracy >= 95 and critical_failed == 0),
    }


def evaluation_progress(connection: sqlite3.Connection, *, refresh: bool = True) -> dict[str, Any]:
    """计算全库金标准规模、保留集比例和正式发布所需的事实门禁。"""

    book_ids = [
        int(row["book_id"])
        for row in connection.execute(
            """
            SELECT DISTINCT book_id FROM quality_benchmark_cases
            WHERE confirmed_by_user = 1
              AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
            ORDER BY book_id
            """
        ).fetchall()
    ]
    per_book_counts = {
        int(row["book_id"]): int(row["case_count"])
        for row in connection.execute(
            """
            SELECT book_id, COUNT(*) AS case_count
            FROM quality_benchmark_cases WHERE confirmed_by_user = 1
              AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
            GROUP BY book_id
            """
        ).fetchall()
    }
    books_below_minimum = sum(1 for count in per_book_counts.values() if count < 25)
    if refresh:
        for book_id in book_ids:
            evaluate_benchmarks(connection, book_id)
    counts = connection.execute(
        """
        SELECT COUNT(*) AS confirmed,
            COALESCE(SUM(CASE WHEN review_status = 'sealed_holdout'
                AND second_review_status = 'confirmed' THEN 1 ELSE 0 END), 0) AS holdout,
            COALESCE(SUM(CASE WHEN holdout = 0 THEN 1 ELSE 0 END), 0) AS development,
            COALESCE(SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END), 0) AS passed,
            COALESCE(SUM(CASE WHEN review_status = 'sealed_holdout'
                AND second_review_status = 'confirmed' AND passed = 1 THEN 1 ELSE 0 END), 0) AS holdout_passed,
            COALESCE(SUM(CASE WHEN critical = 1 AND COALESCE(passed, 0) != 1 THEN 1 ELSE 0 END), 0) AS critical_failed,
            COALESCE(SUM(CASE WHEN (critical = 1 OR review_status = 'sealed_holdout')
                AND second_review_status != 'confirmed' THEN 1 ELSE 0 END), 0) AS second_review_pending
        FROM quality_benchmark_cases WHERE confirmed_by_user = 1
          AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
        """
    ).fetchone()
    confirmed = int(counts["confirmed"] or 0)
    holdout = int(counts["holdout"] or 0)
    passed = int(counts["passed"] or 0)
    holdout_passed = int(counts["holdout_passed"] or 0)
    holdout_share = round(holdout / confirmed * 100, 2) if confirmed else 0.0
    accuracy = round(passed / confirmed * 100, 2) if confirmed else None
    holdout_accuracy = round(holdout_passed / holdout * 100, 2) if holdout else None
    # 发布门禁只检查已经纳入金标准的作品，演示书和未确认作品不能污染正式评估结果。
    book_placeholders = ",".join("?" for _ in book_ids)
    quote_scope = f"WHERE evidence.book_id IN ({book_placeholders})" if book_ids else "WHERE 1 = 0"
    quote_counts = connection.execute(
        f"""
        SELECT COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN INSTR(segment.text, evidence.quote) > 0 THEN 1 ELSE 0 END), 0) AS valid
        FROM evidence
        JOIN segments segment ON segment.id = evidence.segment_id
        {quote_scope}
        """,
        book_ids,
    ).fetchone()
    quote_total = int(quote_counts["total"] or 0)
    quote_valid = int(quote_counts["valid"] or 0)
    quote_integrity = round(quote_valid / quote_total * 100, 2) if quote_total else 100.0
    if book_ids:
        unresolved = int(connection.execute(
            f"""
            SELECT
                (SELECT COUNT(*) FROM contradictions WHERE status = 'unreviewed' AND book_id IN ({book_placeholders})) +
                (SELECT COUNT(*) FROM event_order_edges WHERE status = 'conflict' AND book_id IN ({book_placeholders})) +
                (SELECT COUNT(*) FROM entity_merge_candidates WHERE status = 'unreviewed' AND book_id IN ({book_placeholders})) +
                (SELECT COUNT(*) FROM entity_connectivity_reviews WHERE status IN ('pending', 'ambiguous') AND book_id IN ({book_placeholders}))
            """,
            book_ids * 4,
        ).fetchone()[0])
    else:
        unresolved = 0
    dataset_ready = (
        confirmed >= 300
        and len(book_ids) >= 12
        and books_below_minimum == 0
        and holdout_share >= 20
        and int(counts["second_review_pending"] or 0) == 0
    )
    quality_ready = bool(
        dataset_ready
        and int(counts["critical_failed"] or 0) == 0
        and holdout_accuracy is not None
        and holdout_accuracy >= 95
        and quote_integrity == 100
        and unresolved == 0
    )
    return {
        "confirmed_cases": confirmed,
        "development_cases": int(counts["development"] or 0),
        "holdout_cases": holdout,
        "holdout_share_percent": holdout_share,
        "book_count": len(book_ids),
        "overall_accuracy_percent": accuracy,
        "holdout_accuracy_percent": holdout_accuracy,
        "critical_failures": int(counts["critical_failed"] or 0),
        "second_review_pending": int(counts["second_review_pending"] or 0),
        "candidate_cases": int(connection.execute(
            "SELECT COUNT(*) FROM quality_benchmark_cases WHERE review_status = 'candidate'"
        ).fetchone()[0]),
        "quote_integrity_percent": quote_integrity,
        "unresolved_conflicts": unresolved,
        "minimum_cases": 300,
        "minimum_books": 12,
        "minimum_cases_per_book": 25,
        "books_below_minimum_cases": books_below_minimum,
        "minimum_holdout_share_percent": 20,
        "minimum_holdout_accuracy_percent": 95,
        "dataset_ready": dataset_ready,
        "release_gate_passed": quality_ready,
        "remaining_cases": max(0, 300 - confirmed),
        "remaining_books": max(0, 12 - len(book_ids)),
    }
