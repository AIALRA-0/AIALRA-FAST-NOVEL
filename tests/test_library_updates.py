"""验证书库管理和保留旧分析的增量更新。"""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app.importers import parse_book
from app.library import _build_update_plan


def _client(tmp_path: Path) -> TestClient:
    """每个测试使用独立本地数据库。"""

    main.settings = replace(
        main.settings,
        database_path=tmp_path / "library.db",
        deepseek_api_key=None,
        moonshot_api_key=None,
    )
    return TestClient(main.app)


def _upload(client: TestClient, filename: str, text: str, folder_id: int | None = None) -> dict:
    """通过正式上传接口创建测试书籍。"""

    data = {} if folder_id is None else {"folder_id": str(folder_id)}
    response = client.post(
        "/api/books/import",
        data=data,
        files={"file": (filename, text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_folder_and_book_crud_preserves_books_when_folder_is_deleted(tmp_path: Path) -> None:
    """文件夹和书籍支持创建、读取、修改与删除，删除目录不会删除书。"""

    with _client(tmp_path) as client:
        parent = client.post("/api/library/folders", json={"name": "长篇", "parent_id": None})
        assert parent.status_code == 201
        parent_id = parent.json()["id"]
        child = client.post("/api/library/folders", json={"name": "玄幻", "parent_id": parent_id})
        assert child.status_code == 201
        child_id = child.json()["id"]

        book = _upload(client, "书库测试.txt", "第一章 起点\n\n主角出发。", child_id)
        changed = client.patch(
            f"/api/books/{book['id']}",
            json={"title": "书库测试修订", "author": "测试作者", "folder_id": parent_id},
        )
        assert changed.status_code == 200
        assert changed.json()["title"] == "书库测试修订"
        assert changed.json()["folder_id"] == parent_id

        renamed = client.patch(
            f"/api/library/folders/{parent_id}",
            json={"name": "已整理长篇", "parent_id": None},
        )
        assert renamed.status_code == 200
        assert any(item["name"] == "已整理长篇" for item in client.get("/api/library/folders").json())

        removed = client.delete(f"/api/library/folders/{parent_id}")
        assert removed.status_code == 204
        stored = next(item for item in client.get("/api/books").json() if item["id"] == book["id"])
        assert stored["folder_id"] is None
        assert stored["title"] == "书库测试修订"

        deleted = client.delete(f"/api/books/{book['id']}")
        assert deleted.status_code == 204
        assert all(item["id"] != book["id"] for item in client.get("/api/books").json())


def test_safe_incremental_update_only_queues_new_segments(tmp_path: Path) -> None:
    """完整新版保留相同前缀时只追加新章节，并复用旧章节分析结果。"""

    version_one = "第一章 起点\n\n甲来到城门。\n\n第二章 相遇\n\n甲遇见乙。"
    version_two = version_one + "\n\n第三章 远行\n\n甲与乙乘船离开。"
    with _client(tmp_path) as client:
        book = _upload(client, "增量小说.txt", version_one)
        first_job = client.post(f"/api/books/{book['id']}/jobs", json={"provider": "mock"}).json()
        deadline = time.monotonic() + 5
        while first_job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.05)
            first_job = client.get(f"/api/jobs/{first_job['id']}").json()
        assert first_job["status"] == "completed"

        with main.connect(main.settings.database_path) as connection:
            old_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM segments WHERE book_id = ? ORDER BY ordinal", (book["id"],)
            ).fetchall()]

        response = client.post(
            f"/api/books/{book['id']}/updates",
            data={"mode": "auto"},
            files={"file": ("增量小说新版.txt", version_two.encode("utf-8"), "text/plain")},
        )
        assert response.status_code == 201, response.text
        update = response.json()
        assert update["status"] == "applied"
        assert update["added_segment_count"] == 1
        assert update["conflict_count"] == 0

        with main.connect(main.settings.database_path) as connection:
            all_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM segments WHERE book_id = ? ORDER BY ordinal", (book["id"],)
            ).fetchall()]
        assert all_ids[: len(old_ids)] == old_ids
        assert len(all_ids) == len(old_ids) + 1

        incremental_job = client.post(
            f"/api/books/{book['id']}/jobs",
            json={"provider": "mock", "start_segment": update["start_segment"]},
        )
        assert incremental_job.status_code == 201
        assert incremental_job.json()["total_segments"] == 1


def test_changed_old_chapters_are_all_listed_and_auto_resolution_applies_incrementally(tmp_path: Path) -> None:
    """旧章节变化时保留旧版本；自动处理只替换受影响章节。"""

    current = "第一章 起点\n\n甲来到城门。\n\n第二章 相遇\n\n甲遇见乙。"
    changed = "第一章 起点\n\n甲来到城门。\n\n第二章 相遇\n\n甲从未遇见乙。\n\n第三章 新线\n\n丙来到城门。"
    with _client(tmp_path) as client:
        book = _upload(client, "冲突小说.txt", current)
        response = client.post(
            f"/api/books/{book['id']}/updates",
            data={"mode": "full"},
            files={"file": ("冲突小说新版.txt", changed.encode("utf-8"), "text/plain")},
        )
        assert response.status_code == 201, response.text
        update = response.json()
        assert update["status"] == "needs_review"
        assert update["conflict_count"] == 2
        assert [item["kind"] for item in update["conflicts"]] == ["changed", "inserted"]

        resolved = client.post(
            f"/api/book-updates/{update['id']}/resolve", json={"action": "auto"}
        )
        assert resolved.status_code == 200
        new_book_id = resolved.json()["book_id"]
        assert new_book_id == book["id"]
        with main.connect(main.settings.database_path) as connection:
            updated_count = connection.execute(
                "SELECT segment_count FROM books WHERE id = ?", (book["id"],)
            ).fetchone()[0]
            version_count = connection.execute(
                "SELECT COUNT(*) FROM segment_versions WHERE book_id = ?", (book["id"],)
            ).fetchone()[0]
        assert updated_count == 3
        assert version_count == 1
        history = client.get(f"/api/books/{book['id']}/updates").json()
        assert history[0]["status"] == "resolved"
        assert history[0]["conflict_count"] == 2


def test_update_plan_detects_rename_reorder_insert_and_remove_without_reanalysis(tmp_path: Path) -> None:
    """相同正文即使改名或移动也复用原片段，只把真正新增和删除列入影响范围。"""

    original = "第一章 甲\n\n甲的正文。\n\n第二章 乙\n\n乙的正文。\n\n第三章 丙\n\n丙的正文。\n\n第四章 丁\n\n丁的正文。"
    revised = "第一章 甲改名\n\n甲的正文。\n\n第三章 丙\n\n丙的正文。\n\n第二章 乙\n\n乙的正文。\n\n第五章 戊\n\n戊的正文。"
    with _client(tmp_path) as client:
        book = _upload(client, "章节对齐.txt", original)
        with main.connect(main.settings.database_path) as connection:
            stored = connection.execute(
                "SELECT * FROM segments WHERE book_id = ? ORDER BY ordinal", (book["id"],)
            ).fetchall()
        parsed = parse_book("章节对齐新版.txt", revised.encode("utf-8"))
        plan = _build_update_plan(stored, parsed.segments)
        assert plan["counts"] == {
            "unchanged": 0,
            "renamed": 1,
            "reordered": 2,
            "modified": 0,
            "inserted": 1,
            "removed": 1,
        }
        assert plan["reuse_ratio"] == 0.75
        assert plan["affected_scope"]["changed_ordinals"] == [3]


def test_modified_chapter_rebuilds_only_affected_results_and_preserves_human_records(tmp_path: Path) -> None:
    """修改旧章只失效该章模型结果；未变化章节和人工修正继续保留。"""

    original = "第一章 甲\n\n甲来到城门。\n\n第二章 乙\n\n乙留在客栈。\n\n第三章 丙\n\n丙乘船离开。"
    revised = "第一章 甲\n\n甲来到城门。\n\n第二章 乙\n\n乙改为前往码头。\n\n第三章 丙\n\n丙乘船离开。"
    with _client(tmp_path) as client:
        book = _upload(client, "选择重建.txt", original)
        job = client.post(f"/api/books/{book['id']}/jobs", json={"provider": "mock"}).json()
        deadline = time.monotonic() + 5
        while job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
            time.sleep(0.05)
            job = client.get(f"/api/jobs/{job['id']}").json()
        assert job["status"] == "completed"
        with main.transaction(main.settings.database_path) as connection:
            segments = connection.execute(
                "SELECT id FROM segments WHERE book_id = ? ORDER BY ordinal", (book["id"],)
            ).fetchall()
            human_id = int(connection.execute(
                "INSERT INTO entities(book_id, name, kind, summary, importance, created_by) VALUES (?, '人工人物', 'person', '人工修正', 1, 'human')",
                (book["id"],),
            ).lastrowid)
        response = client.post(
            f"/api/books/{book['id']}/updates",
            data={"mode": "full"},
            files={"file": ("选择重建新版.txt", revised.encode("utf-8"), "text/plain")},
        )
        assert response.status_code == 201, response.text
        update = response.json()
        assert update["match_summary"]["modified"] == 1
        assert update["reuse_ratio"] == 0.6667
        resolved = client.post(
            f"/api/book-updates/{update['id']}/resolve", json={"action": "apply_incremental"}
        )
        assert resolved.status_code == 200, resolved.text
        with main.connect(main.settings.database_path) as connection:
            result_ids = {int(row["segment_id"]) for row in connection.execute(
                "SELECT segment_id FROM segment_results WHERE book_id = ?", (book["id"],)
            )}
            assert int(segments[0]["id"]) in result_ids
            assert int(segments[1]["id"]) not in result_ids
            assert int(segments[2]["id"]) in result_ids
            assert connection.execute("SELECT 1 FROM entities WHERE id = ?", (human_id,)).fetchone()
            assert connection.execute("SELECT COUNT(*) FROM segment_versions WHERE book_id = ?", (book["id"],)).fetchone()[0] == 1
