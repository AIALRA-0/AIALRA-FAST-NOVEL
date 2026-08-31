"""验证整本书后台任务的完成、缓存和状态控制。"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app.db import initialize, transaction
from app.jobs import _refresh_job, control_job, create_job


def test_full_book_job_completes_and_reuses_cache(tmp_path: Path) -> None:
    """离线任务会分析全部片段，第二次运行会跳过已完成片段。"""

    main.settings = replace(main.settings, database_path=tmp_path / "jobs.db", deepseek_api_key=None, moonshot_api_key=None)
    with TestClient(main.app) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        created = client.post(f"/api/books/{book_id}/jobs", json={"provider": "mock"})
        assert created.status_code == 201
        job_id = created.json()["id"]
        deadline = time.monotonic() + 5
        job = created.json()
        while job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "completed"
        assert job["completed_segments"] == 5
        with main.connect(main.settings.database_path) as connection:
            linked_evidence = connection.execute(
                """
                SELECT COUNT(*) FROM evidence
                WHERE book_id = ? AND run_manifest_id IS NOT NULL AND model_call_id IS NOT NULL
                """,
                (book_id,),
            ).fetchone()[0]
            first_target = connection.execute(
                "SELECT target_type, target_id FROM evidence WHERE book_id = ? LIMIT 1", (book_id,)
            ).fetchone()
        assert linked_evidence > 0
        evidence_payload = client.get(
            f"/api/evidence/{first_target['target_type']}/{first_target['target_id']}"
        ).json()
        assert evidence_payload[0]["lineage"]["trace_status"] == "complete"
        assert evidence_payload[0]["lineage"]["manifest"]["prompt_hash"]

        cached = client.post(f"/api/books/{book_id}/jobs", json={"provider": "mock"}).json()
        assert cached["status"] == "completed"
        assert cached["total_segments"] == 0

        # 新书的章节编号与任务队列行号不同，证据必须仍然指向这本新书的原文。
        imported = client.post(
            "/api/books/import",
            files={"file": ("普通小说.txt", "第一章\n【人物：阿青】阿青来到渡口。".encode("utf-8"), "text/plain")},
        ).json()
        new_job = client.post(f"/api/books/{imported['id']}/jobs", json={"provider": "mock"}).json()
        deadline = time.monotonic() + 5
        while new_job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.05)
            new_job = client.get(f"/api/jobs/{new_job['id']}").json()
        assert new_job["status"] == "completed"
        with main.connect(main.settings.database_path) as connection:
            mismatched = connection.execute(
                """
                SELECT COUNT(*) FROM evidence evidence_row
                JOIN segments segment ON segment.id = evidence_row.segment_id
                WHERE evidence_row.book_id = ? AND segment.book_id != evidence_row.book_id
                """,
                (imported["id"],),
            ).fetchone()[0]
        assert mismatched == 0


def test_job_can_pause_resume_and_cancel_without_losing_rows(tmp_path: Path) -> None:
    """任务控制只改变待处理状态，已经建立的片段清单保持完整。"""

    settings = replace(main.settings, database_path=tmp_path / "control.db", deepseek_api_key=None, moonshot_api_key=None)
    initialize(settings.database_path)
    with transaction(settings.database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('任务测试', 'txt', 'job-hash', 'job.txt', 1, 6)
            """
        )
        book_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, 0, '正文', 'job-seg', '事件发生。', 0, 5)
            """,
            (book_id,),
        )
    job = create_job(settings, book_id, "mock", 0, None, 3, False)
    assert control_job(settings, job["id"], "pause")["status"] == "paused"
    assert control_job(settings, job["id"], "resume")["status"] == "queued"
    assert control_job(settings, job["id"], "cancel")["status"] == "cancelled"
    with main.connect(settings.database_path) as connection:
        manifest = connection.execute(
            "SELECT status FROM run_manifests WHERE id = ?", (job["run_manifest_id"],)
        ).fetchone()
    assert manifest["status"] == "cancelled"


def test_restart_returns_interrupted_quality_check_to_retryable_state(tmp_path: Path) -> None:
    """应用重启后，专项复审进入可重试状态，不会误当成章节任务继续。"""

    settings = replace(main.settings, database_path=tmp_path / "quality-restart.db", deepseek_api_key=None, moonshot_api_key=None)
    initialize(settings.database_path)
    with transaction(settings.database_path) as connection:
        book_id = int(connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('质量恢复测试', 'txt', 'quality-restart-hash', 'quality.txt', 1, 6)
            """
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, 0, '正文', 'quality-restart-segment', '事件发生。', 0, 5)
            """,
            (book_id,),
        )
    job = create_job(settings, book_id, "mock", 0, None, 3, False)
    with transaction(settings.database_path) as connection:
        connection.execute(
            "UPDATE analysis_jobs SET status = 'quality_checking', quality_gate_status = 'running' WHERE id = ?",
            (job["id"],),
        )
    initialize(settings.database_path)
    with main.connect(settings.database_path) as connection:
        recovered = connection.execute("SELECT status, quality_gate_status FROM analysis_jobs WHERE id = ?", (job["id"],)).fetchone()
    assert recovered["status"] == "needs_review"
    assert recovered["quality_gate_status"] == "needs_review"


def test_deepseek_job_records_cache_aware_cost_snapshot(tmp_path: Path) -> None:
    """DeepSeek 任务会保存令牌分类，并按照创建时费率计算本次费用。"""

    settings = replace(
        main.settings,
        database_path=tmp_path / "cost.db",
        deepseek_api_key="test-only-key",
        moonshot_api_key=None,
    )
    initialize(settings.database_path)
    with transaction(settings.database_path) as connection:
        book_id = int(connection.execute(
            """
            INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count)
            VALUES ('费用测试', 'txt', 'cost-hash', 'cost.txt', 1, 6)
            """
        ).lastrowid)
        connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, 0, '第一章', 'cost-seg', '事件发生。', 0, 5)
            """,
            (book_id,),
        )
    job = create_job(settings, book_id, "deepseek", 0, None, 3, False)
    with transaction(settings.database_path) as connection:
        connection.execute(
            """
            UPDATE analysis_job_segments SET status = 'completed', input_tokens = 1500,
                output_tokens = 200, cache_hit_input_tokens = 500,
                cache_miss_input_tokens = 1000 WHERE job_id = ?
            """,
            (job["id"],),
        )
        refreshed = dict(_refresh_job(connection, job["id"]))
    assert refreshed["cache_hit_input_tokens"] == 500
    assert refreshed["cache_miss_input_tokens"] == 1000
    assert refreshed["estimated_cost_usd"] == 0.00067361
    assert "DeepSeek 官方高峰价" in refreshed["pricing_source"]


def test_startup_repairs_old_cross_book_evidence_link(tmp_path: Path) -> None:
    """旧队列行号造成的跨书证据错链应在启动时自动改回真实章节。"""

    settings = replace(main.settings, database_path=tmp_path / "repair.db", deepseek_api_key=None, moonshot_api_key=None)
    initialize(settings.database_path)
    with transaction(settings.database_path) as connection:
        first_book = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('前一本', 'txt', 'repair-a', 'a.txt', 1, 3)"
        ).lastrowid)
        wrong_segment_id = int(connection.execute(
            "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, 0, '前章', 'repair-a-0', '旧文。', 0, 3)",
            (first_book,),
        ).lastrowid)
        second_book = int(connection.execute(
            "INSERT INTO books(title, source_type, source_hash, original_filename, segment_count, character_count) VALUES ('后一本', 'txt', 'repair-b', 'b.txt', 1, 3)"
        ).lastrowid)
        correct_segment_id = int(connection.execute(
            "INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end) VALUES (?, 0, '后章', 'repair-b-0', '新文。', 0, 3)",
            (second_book,),
        ).lastrowid)
    job = create_job(settings, second_book, "mock", 0, None, 3, False)
    with transaction(settings.database_path) as connection:
        event_id = int(connection.execute(
            "INSERT INTO events(book_id, title, summary, narrative_order, story_order, temporal_kind, temporal_value, confidence, first_segment) VALUES (?, '新事', '新文。', 0, 0, 'unknown', '', 0.8, 0)",
            (second_book,),
        ).lastrowid)
        connection.execute(
            "INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end) VALUES (?, 'event', ?, ?, '新文。', 0, 3)",
            (second_book, event_id, wrong_segment_id),
        )
        queue_row_id = connection.execute(
            "SELECT id FROM analysis_job_segments WHERE job_id = ?",
            (job["id"],),
        ).fetchone()[0]
        assert queue_row_id == wrong_segment_id
    initialize(settings.database_path)
    with transaction(settings.database_path) as connection:
        repaired_segment_id = connection.execute(
            "SELECT segment_id FROM evidence WHERE book_id = ? AND target_type = 'event'",
            (second_book,),
        ).fetchone()[0]
    assert repaired_segment_id == correct_segment_id
