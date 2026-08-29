"""2.9.3 geometry, partition, review and incremental-merge gates."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from shapely.geometry import Polygon, box

import app.main as main


def _client(tmp_path: Path) -> TestClient:
    main.settings = replace(main.settings, database_path=tmp_path / "v293.db", deepseek_api_key=None, moonshot_api_key=None)
    return TestClient(main.app)


def test_region_geometry_contains_nodes_labels_and_children(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        atlas = client.get(f"/api/books/{book_id}/map-layout?through_segment=119").json()
        node_by_id = {int(node["id"]): node for node in atlas["nodes"]}
        for region in atlas["regions"]:
            polygon = Polygon([(point["x"], point["y"]) for point in region["hull"]])
            assert region["geometry_validation"]["valid"] is True
            for node_id in region["node_ids"]:
                footprint = node_by_id[int(node_id)]["occupancy_bbox"]
                assert polygon.covers(box(footprint["min_x"], footprint["min_y"], footprint["max_x"], footprint["max_y"]))
            for child in (item for item in atlas["regions"] if item.get("parent_region_id") == region["id"]):
                child = Polygon([(point["x"], point["y"]) for point in child["hull"]])
                assert polygon.covers(child)
            volume = region["volume"]
            assert volume["z_max"] > volume["z_min"]


def test_long_map_labels_wrap_and_their_full_box_remains_inside_region(tmp_path: Path) -> None:
    from app.atlas import _label_lines

    assert len(_label_lines("quiet streets leading towards the Edgware Road sitting-room", 166, 3)) > 1
    assert len(_label_lines("这是一个需要换行显示的非常长地点名称", 150, 2)) == 2
    with _client(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        atlas = client.get(f"/api/books/{book_id}/map-layout?through_segment=119").json()
        for node in atlas["nodes"]:
            placement = node["label_placement"]
            assert 1 <= len(placement["lines"]) <= 2
            assert placement["height"] >= 24
        for region in atlas["regions"]:
            anchor = region["label_anchor"]
            assert 1 <= len(anchor["lines"]) <= 3
            assert anchor["width"] <= 190


def test_narrative_structure_is_reversible_and_keeps_one_book(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        book = next(book for book in client.get("/api/books").json() if "120章" in book["title"])
        response = client.get(f"/api/books/{book['id']}/narrative-structure")
        assert response.status_code == 200, response.text
        structure = response.json()
        assert structure["book_id"] == book["id"]
        assert structure["worlds"]
        assert structure["units"]
        assert structure["scope_options"][0]["kind"] == "book"
        rebuilt = client.post(f"/api/books/{book['id']}/narrative-structure/rebuild", json={"force": True})
        assert rebuilt.status_code == 200
        assert rebuilt.json()["book_id"] == book["id"]


def test_story_partition_can_split_edit_and_restore_without_creating_a_book(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        books_before = client.get("/api/books").json()
        book = next(book for book in books_before if "120章" in book["title"])
        structure = client.get(f"/api/books/{book['id']}/narrative-structure").json()
        unit = structure["units"][0]
        split = client.post(
            f"/api/books/{book['id']}/story-worlds/split",
            json={"unit_ids": [unit["id"]], "name": "人工阅读世界"},
        )
        assert split.status_code == 200, split.text
        assert any(world["name"] == "人工阅读世界" for world in split.json()["worlds"])
        renamed = client.patch(
            f"/api/records/narrative_unit/{unit['id']}",
            json={"field_name": "title", "new_value": "人工故事单元", "reason": "回归测试"},
        )
        assert renamed.status_code == 200, renamed.text
        restored = client.post(
            f"/api/books/{book['id']}/narrative-structure/rebuild",
            json={"force": True},
        )
        assert restored.status_code == 200
        assert len(client.get("/api/books").json()) == len(books_before)


def test_review_tasks_hide_resolved_findings_and_offer_actions(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        tasks = client.get(f"/api/books/{book_id}/review-tasks").json()
        for task in tasks:
            assert task["problem"]
            assert task["impact"]
            assert task["recommendation"]
            assert task["actions"]
            assert task["status"] == "pending"
        if tasks:
            resolved = client.patch(f"/api/review-tasks/{tasks[0]['id']}", json={"action": "defer", "note": "稍后统一处理"})
            assert resolved.status_code == 200
            assert resolved.json()["status"] == "pending"


def test_generated_narrative_uses_semicolon_without_rewriting_evidence() -> None:
    from app.narrative import compose_event_narrative

    quote = "甲抵达城门。随后等待。"
    narrative = compose_event_narrative({
        "summary": quote,
        "narrative_frame": {"cause": "先抵达城门。", "action": "随后等待。"},
    })
    assert narrative == "先抵达城门，随后等待"
    assert quote == "甲抵达城门。随后等待。"


def test_authored_interface_copy_uses_semicolon_style() -> None:
    project_root = Path(__file__).resolve().parents[1]
    assert "。" not in (project_root / "static" / "app.js").read_text(encoding="utf-8")
    assert "。" not in (project_root / "static" / "index.html").read_text(encoding="utf-8")
