"""验证演示书、剧透边界、人工审核和接口密钥隔离。"""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import initialize, transaction
from app.quality_harness import _multi_entity_windows, refresh_local_reviews
from app.semantic import recompute_chronology_dag


def client_for(tmp_path: Path) -> TestClient:
    """为每个测试创建独立数据库。"""

    main.settings = replace(main.settings, database_path=tmp_path / "api.db", deepseek_api_key=None, moonshot_api_key=None)
    return TestClient(main.app)


def test_health_and_readiness_endpoints(tmp_path: Path) -> None:
    """部署探针必须区分进程存活和数据库可用。"""

    with client_for(tmp_path) as client:
        health = client.get("/healthz").json()
        assert health == {"status": "ok", "version": "2.9.6-rc.1"}
        readiness = client.get("/readyz")
        assert readiness.status_code == 200
        assert readiness.json() == {"status": "ready", "version": "2.9.6-rc.1"}


def test_relation_direction_can_be_reviewed_without_creating_a_second_fact(tmp_path: Path) -> None:
    """人工切换双向关系时保留同一事实、两个称谓和修订历史。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        claim = next(item for item in overview["claims"] if item["predicate"] == "同行")
        updated = client.patch(
            f"/api/claims/{claim['id']}",
            json={
                "status": claim["status"],
                "directionality": "bidirectional",
                "reverse_predicate": "同行",
                "reason": "原文同一证据明确支持同行关系",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["directionality"] == "bidirectional"
        assert updated.json()["reverse_predicate"] == "同行"
        refreshed = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        matching = [item for item in refreshed["claims"] if item["id"] == claim["id"]]
        assert len(matching) == 1
        assert matching[0]["directionality"] == "bidirectional"
        assert matching[0]["reverse_predicate"] == "同行"


def test_asymmetric_relation_cannot_become_bidirectional_without_reverse_label(tmp_path: Path) -> None:
    """追捕等非对称关系缺少明确反向称谓时必须继续保持单向。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=119").json()
        claim = next(item for item in overview["claims"] if item["predicate"] not in {"父亲", "母亲", "父母", "儿子", "女儿", "子女", "师父", "师傅", "徒弟", "丈夫", "妻子", "配偶", "夫妻", "伴侣", "兄弟", "姐妹", "兄妹", "姐弟", "同胞", "亲属"})
        updated = client.patch(
            f"/api/claims/{claim['id']}",
            json={"status": claim["status"], "directionality": "bidirectional", "reason": "缺少反向称谓的错误请求"},
        )
        assert updated.status_code == 200
        assert updated.json()["directionality"] == "directed"
        assert updated.json()["reverse_predicate"] is None


def test_demo_overview_respects_spoiler_boundary(tmp_path: Path) -> None:
    """阅读进度为第一章时不能看到第五章反派关系。"""

    with client_for(tmp_path) as client:
        books = client.get("/api/books").json()
        demo = next(book for book in books if book["title"] == "雾川行记 · 演示")
        book_id = demo["id"]
        early = client.get(f"/api/books/{book_id}/overview?through_segment=0").json()
        full = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        assert all(item["name"] != "沈烬" for item in early["entities"])
        assert any(item["name"] == "沈烬" for item in full["entities"])
        assert len(full["events"]) == 5


def test_provider_status_never_returns_keys(tmp_path: Path) -> None:
    """供应商接口只暴露可用状态。"""

    with client_for(tmp_path) as client:
        body = client.get("/api/providers").text
        assert "api_key" not in body.lower()
        assert "deepseek" in body


def test_claim_review_hides_rejected_relation(tmp_path: Path) -> None:
    """人工拒绝关系后，派生图会隐藏关系并保留修订记录。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        claim_id = overview["claims"][0]["id"]
        response = client.patch(f"/api/claims/{claim_id}", json={"status": "rejected", "reason": "测试纠错"})
        assert response.status_code == 200
        updated = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        assert all(item["id"] != claim_id for item in updated["claims"])


def test_search_uses_same_spoiler_boundary(tmp_path: Path) -> None:
    """全书搜索只能看到阅读进度以内的人物和原文。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        early = client.get(f"/api/books/{book_id}/search", params={"q": "沈烬", "through_segment": 0}).json()
        full = client.get(f"/api/books/{book_id}/search", params={"q": "沈烬", "through_segment": 4}).json()
        assert early == []
        assert full


def test_export_contains_evidence_and_optional_source_text(tmp_path: Path) -> None:
    """可携带原文的导出包包含结构记录、证据和稳定片段。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        response = client.get(f"/api/books/{book_id}/export?include_text=true")
        assert response.status_code == 200
        exported = response.json()
        assert exported["format"] == "novel-atlas-v1"
        assert exported["evidence"]
        assert "text" in exported["segments"][0]


def test_large_demo_exercises_graph_timeline_and_journey(tmp_path: Path) -> None:
    """120 章演示书必须同时提供大型关系网、编年史和连续主线轨迹。"""

    with client_for(tmp_path) as client:
        books = client.get("/api/books").json()
        assert len(books) >= 4
        large = next(book for book in books if "120章" in book["title"])
        overview = client.get(f"/api/books/{large['id']}/overview?through_segment=119").json()
        assert len(overview["segments"]) == 120
        assert len([item for item in overview["entities"] if item["kind"] == "person"]) >= 60
        assert len(overview["claims"]) >= 150
        assert len(overview["events"]) == 120
        assert len(overview["journey_events"]) == 120
        assert len(overview["story_map_steps"]) == 120
        assert overview["chronology_event_ids"] == [item["id"] for item in overview["events"]]
        assert [item["event_id"] for item in overview["story_map_steps"]] == overview["chronology_event_ids"]
        assert [item["canonical_index"] for item in overview["story_map_steps"]] == list(range(120))
        assert len(overview["world_notes"]) == 4
        place_points = [(item["x"], item["y"]) for item in overview["entities"] if item["kind"] == "place"]
        radii = [((x - 50) ** 2 + (y - 50) ** 2) ** 0.5 for x, y in place_points]
        assert max(radii) - min(radii) > 10
        assert overview["quality"]["evidence_coverage_percent"] == 100.0


def test_v27_atlas_narrative_and_knowledge_endpoints_share_existing_facts(tmp_path: Path) -> None:
    """2.7 派生层必须复用原有地点、事件和证据，不生成第二套故事。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        atlas = client.get(f"/api/books/{book_id}/map-layout").json()
        repeated = client.get(f"/api/books/{book_id}/map-layout").json()
        assert atlas["source_hash"] == repeated["source_hash"]
        assert atlas["nodes"] == repeated["nodes"]
        assert atlas["fact_source"] == "overview.story_map_steps"
        assert {item["id"] for item in atlas["nodes"]} == {
            item["id"] for item in overview["entities"] if item["kind"] == "place"
        }
        assert all(item["coordinate_source"] in {"directional_evidence", "stable_topology_projection"} for item in atlas["nodes"])

        memory = client.get(f"/api/books/{book_id}/narrative-memory").json()
        assert memory["generation_policy"] == "local_first_cached_arc_review"
        assert [item["id"] for item in memory["recent_scenes"]] == [item["id"] for item in overview["events"]]
        assert all(item["narrative_text"] for item in memory["recent_scenes"])

        facets = client.get(f"/api/books/{book_id}/knowledge-facets").json()
        concepts = client.get(f"/api/books/{book_id}/concepts", params={"status": "", "limit": 1000}).json()
        assert facets["concept_count"] > 0
        assert any(item["scheme"] == "book" for item in concepts)
        assert all("evidence_count" in item for item in concepts)


def test_v292_map_layout_exposes_named_regions_and_external_label_geometry(tmp_path: Path) -> None:
    """2.9 atlas keeps every real node while deriving bounded levels of detail."""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        atlas = client.get(
            f"/api/books/{book_id}/map-layout",
            params={"through_segment": 119, "detail_level": "low", "focus": "current"},
        ).json()
        assert atlas["layout_version"] == "semantic-atlas-v2.9.4-wrapped-labels1"
        assert atlas["requested_detail_level"] == "low"
        assert atlas["requested_focus"] == "current"
        assert atlas["world_bounds"]["width"] > 0
        assert atlas["world_bounds"]["height"] > 0
        assert set(atlas["detail_levels"]) == {"low", "medium", "high"}
        assert atlas["detail_levels"]["high"]["node_ids"] == [item["id"] for item in atlas["nodes"]]
        assert all(item["display_policy"] == "all_views" for item in atlas["regions"])
        assert all(item["display_name"] for item in atlas["regions"])
        assert all("故事拓扑片区" not in item["display_name"] for item in atlas["regions"])
        assert all(item["representative_node_ids"] for item in atlas["regions"])
        assert all(item["label_anchor"] and item["label_connector"] for item in atlas["regions"])
        assert atlas["region_coverage"]["visible_region_count"] == len(atlas["regions"])
        assert atlas["region_coverage"]["total_place_count"] == len(atlas["nodes"])
        assert atlas["region_coverage"]["overlap"]["same_level_overlap_pairs"] == 0
        assert not any(item["issue_type"] == "region_overlap" for item in atlas["quality_issues"])
        assert "unassigned_node_ids" in atlas
        evidence_nodes = {
            int(node_id)
            for region in atlas["regions"]
            if region["kind"] == "evidence_containment"
            for node_id in region["node_ids"]
        }
        assert all(
            not evidence_nodes.intersection(int(node_id) for node_id in region["node_ids"])
            for region in atlas["regions"]
            if region["kind"] == "topological_cluster"
        )
        node_positions = [(float(node["x"]), float(node["y"])) for node in atlas["nodes"]]
        assert min(
            math.hypot(left_x - right_x, left_y - right_y)
            for index, (left_x, left_y) in enumerate(node_positions)
            for right_x, right_y in node_positions[index + 1:]
        ) >= 112.0
        label_boxes = [region["label_anchor"]["bbox"] for region in atlas["regions"]]
        assert all(
            left["max_x"] <= right["min_x"]
            or right["max_x"] <= left["min_x"]
            or left["max_y"] <= right["min_y"]
            or right["max_y"] <= left["min_y"]
            for index, left in enumerate(label_boxes)
            for right in label_boxes[index + 1:]
        )


def test_nested_place_regions_form_real_parent_child_frames(tmp_path: Path) -> None:
    """Evidence-backed location chains render as nested frames, not crossing siblings."""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        with transaction(main.settings.database_path) as connection:
            place_ids = {}
            for name in ("测试外城", "测试内院", "测试密室"):
                place_ids[name] = int(connection.execute(
                    "INSERT INTO entities(book_id, kind, name, summary, importance, first_segment) VALUES (?, 'place', ?, ?, 0.8, 0)",
                    (book_id, name, name),
                ).lastrowid)
            connection.execute(
                "INSERT INTO place_relations(book_id, source_entity_id, target_entity_id, relative_position, summary, confidence, first_segment) VALUES (?, ?, ?, 'inside', ?, 0.95, 0)",
                (book_id, place_ids["测试内院"], place_ids["测试外城"], "测试内院位于测试外城内"),
            )
            connection.execute(
                "INSERT INTO place_relations(book_id, source_entity_id, target_entity_id, relative_position, summary, confidence, first_segment) VALUES (?, ?, ?, 'inside', ?, 0.95, 0)",
                (book_id, place_ids["测试密室"], place_ids["测试内院"], "测试密室位于测试内院内"),
            )

        atlas = client.get(f"/api/books/{book_id}/map-layout", params={"through_segment": 4}).json()
        regions = {region["display_name"]: region for region in atlas["regions"]}
        outer = regions["测试外城"]
        inner = regions["测试内院"]
        assert set(outer["node_ids"]) >= set(inner["node_ids"])
        assert inner["parent_region_id"] == outer["id"]

        def bounds(region: dict) -> tuple[float, float, float, float]:
            return (
                min(float(point["x"]) for point in region["hull"]),
                min(float(point["y"]) for point in region["hull"]),
                max(float(point["x"]) for point in region["hull"]),
                max(float(point["y"]) for point in region["hull"]),
            )

        outer_bounds = bounds(outer)
        inner_bounds = bounds(inner)
        assert outer_bounds[0] <= inner_bounds[0]
        assert outer_bounds[1] <= inner_bounds[1]
        assert outer_bounds[2] >= inner_bounds[2]
        assert outer_bounds[3] >= inner_bounds[3]


def test_v29_system_graph_and_story_context_are_evidence_and_spoiler_bounded(tmp_path: Path) -> None:
    """System nodes require literal evidence in the UI flow and story capsules stay inside progress."""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        segment = overview["segments"][0]
        source = client.get(f"/api/segments/{segment['id']}").json()
        quote = source["text"][: min(16, len(source["text"]))]
        created = client.post(
            f"/api/books/{book_id}/systems",
            json={"name": "测试阶层", "category": "social", "structure_type": "partial_order", "description": "只测试证据边界"},
        )
        assert created.status_code == 201
        system_id = created.json()["id"]
        node = client.post(
            f"/api/systems/{system_id}/nodes",
            json={"label": "已知阶层", "segment_id": segment["id"], "evidence_quote": quote, "effective_from_segment": 0},
        )
        assert node.status_code == 201
        node_id = node.json()["id"]
        assert client.post(f"/api/systems/{system_id}/nodes", json={"label": "无证据节点"}).status_code == 422
        second = client.post(
            f"/api/systems/{system_id}/nodes",
            json={"label": "并列阶层", "segment_id": segment["id"], "evidence_quote": quote, "effective_from_segment": 0},
        )
        assert second.status_code == 201
        relation = client.post(
            f"/api/systems/{system_id}/relations",
            json={
                "source_node_id": node_id, "target_node_id": second.json()["id"], "relation_type": "related",
                "segment_id": segment["id"], "evidence_quote": quote,
            },
        )
        assert relation.status_code == 201
        relation_id = relation.json()["id"]
        assert client.patch(f"/api/system-relations/{relation_id}", json={"relation_type": "precedes"}).json()["relation_type"] == "precedes"
        assert client.delete(f"/api/system-relations/{relation_id}").json()["status"] == "deprecated"
        assert client.patch(f"/api/system-nodes/{node_id}", json={"description": "人工修订说明"}).json()["description"] == "人工修订说明"
        systems = client.get(f"/api/books/{book_id}/systems").json()
        assert systems[0]["nodes"][0]["evidence_id"] is not None
        event_id = overview["events"][0]["id"]
        visible = client.get(
            f"/api/books/{book_id}/story-context/{event_id}", params={"through_segment": 0},
        )
        assert visible.status_code == 200
        assert visible.json()["through_segment"] == 0
        hidden_event_id = overview["events"][-1]["id"]
        hidden = client.get(
            f"/api/books/{book_id}/story-context/{hidden_event_id}", params={"through_segment": 0},
        )
        assert hidden.status_code == 404
        assert client.delete(f"/api/system-nodes/{node_id}").json()["status"] == "deprecated"
        assert client.delete(f"/api/systems/{system_id}").json()["status"] == "archived"


def test_v29_ui_issue_ledger_is_actionable_and_verifiable(tmp_path: Path) -> None:
    """Visual defects keep reproduction, acceptance, regression, and closure evidence together."""

    with client_for(tmp_path) as client:
        created = client.post(
            "/api/ui-issues",
            json={
                "page_key": "atlas-3d", "viewport": "1366x768@125%", "severity": "high",
                "summary": "区域标签超出画布", "reproduction": "打开真实长篇并切换 3D",
                "acceptance": "全部标签可恢复到画布范围", "regression_test": "tests/e2e_ui.spec.js",
            },
        )
        assert created.status_code == 201
        issue_id = created.json()["id"]
        assert client.get("/api/ui-issues", params={"status": "open"}).json()[0]["id"] == issue_id
        verified = client.patch(f"/api/ui-issues/{issue_id}", json={"status": "verified"})
        assert verified.status_code == 200
        assert verified.json()["closed_at"] is not None


def test_v29_cost_forecast_separates_median_ceiling_and_cache(tmp_path: Path) -> None:
    """Forecasts expose confidence and never mix the median with the hard ceiling."""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        forecast = client.get(
            f"/api/books/{book_id}/cost-forecast",
            params={"provider": "mock", "start_segment": 0, "review_mode": "local"},
        )
        assert forecast.status_code == 200
        body = forecast.json()
        assert body["forecast_version"] == "cost-forecast-v2"
        assert body["confidence"] in {"low", "medium", "high"}
        assert body["conservative_input_tokens"] >= body["median_input_tokens"]
        assert body["conservative_output_tokens"] >= body["median_output_tokens"]
        assert body["pending_segments"] + body["exact_cache_segments"] == body["total_segments"]


def test_v27_derived_views_share_spoiler_boundary(tmp_path: Path) -> None:
    """地图和叙事记忆不能比总览提前暴露后文地点、事件或人物状态。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        boundary = 3
        overview = client.get(f"/api/books/{book_id}/overview", params={"through_segment": boundary}).json()
        atlas = client.get(f"/api/books/{book_id}/map-layout", params={"through_segment": boundary}).json()
        memory = client.get(f"/api/books/{book_id}/narrative-memory", params={"through_segment": boundary}).json()
        visible_places = {item["id"] for item in overview["entities"] if item["kind"] == "place"}
        visible_events = {item["id"] for item in overview["events"]}
        assert atlas["through_segment"] == boundary
        assert {item["id"] for item in atlas["nodes"]} == visible_places
        assert memory["through_segment"] == boundary
        assert {item["id"] for item in memory["recent_scenes"]} == visible_events
        assert all(item["source_event_ids"] and set(item["source_event_ids"]) <= visible_events for item in memory["character_states"])


def test_v27_large_world_is_split_into_non_geographic_semantic_regions(tmp_path: Path) -> None:
    """大型连通地图应形成多个语义片区，同时明确它们不是正式地理边界。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if "120章" in book["title"])
        atlas = client.get(f"/api/books/{book_id}/map-layout", params={"through_segment": 119}).json()
        assert len(atlas["regions"]) >= 3
        assert all(item["formal_geography"] is False for item in atlas["regions"])
        assert all(item["kind"] == "topological_cluster" for item in atlas["regions"])
        assert all(item["boundary_kind"] == "semantic" for item in atlas["regions"])
        assert all(isinstance(item["centroid"]["x"], (int, float)) for item in atlas["regions"])
        assert all(isinstance(item["node_ids"], list) and item["node_ids"] for item in atlas["regions"])


def test_v27_knowledge_crud_requires_exact_quote_for_original_fact(tmp_path: Path) -> None:
    """人工知识可以编辑，标成原文事实时必须逐字命中同书章节。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        created = client.post(
            f"/api/books/{book_id}/concepts",
            json={"category": "term", "preferred_label": "测试概念", "description": "人工测试", "aliases": ["测试别名"], "scheme": "custom"},
        )
        assert created.status_code == 200
        concept = created.json()
        segment_id = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()["segments"][0]["id"]
        source = client.get(f"/api/segments/{segment_id}").json()
        quote = source["text"][: min(12, len(source["text"]))]
        rejected = client.post(
            f"/api/books/{book_id}/knowledge-claims",
            json={"concept_id": concept["id"], "predicate": "错误引文", "value": "不保存", "source_kind": "original_text", "segment_id": segment_id, "evidence_quote": "原文中不存在的句子"},
        )
        assert rejected.status_code == 422
        accepted = client.post(
            f"/api/books/{book_id}/knowledge-claims",
            json={"concept_id": concept["id"], "predicate": "说明", "value": "有证据", "source_kind": "original_text", "segment_id": segment_id, "evidence_quote": quote, "qualifiers": {"阶段": "当前"}},
        )
        assert accepted.status_code == 200
        claim = accepted.json()
        assert claim["evidence_count"] == 1
        assert claim["qualifiers"] == {"阶段": "当前"}
        updated = client.patch(f"/api/knowledge-claims/{claim['id']}", json={"status": "parallel", "value": "并列保留"})
        assert updated.status_code == 200
        assert updated.json()["status"] == "parallel"
        claim_revisions = client.get(
            f"/api/books/{book_id}/knowledge-revisions",
            params={"target_type": "knowledge_claim", "target_id": claim["id"]},
        ).json()
        assert [item["action"] for item in claim_revisions] == ["updated", "created"]
        archived = client.delete(f"/api/concepts/{concept['id']}")
        assert archived.json()["status"] == "archived"
        concept_revisions = client.get(
            f"/api/books/{book_id}/knowledge-revisions",
            params={"target_type": "concept", "target_id": concept["id"]},
        ).json()
        assert [item["action"] for item in concept_revisions] == ["archived", "created"]


@pytest.mark.skipif(sys.platform != "win32", reason="本机凭据存储使用 Windows DPAPI")
def test_provider_key_is_encrypted_and_never_echoed(tmp_path: Path, monkeypatch) -> None:
    """保存接口只返回配置状态，凭据文件中不出现明文。"""

    secret_path = tmp_path / "credentials.json"
    monkeypatch.setenv("NOVEL_SECRET_PATH", str(secret_path))
    placeholder = "test-provider-secret-123456"
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/settings/provider-key",
            json={"provider": "deepseek", "api_key": placeholder},
        )
        assert response.status_code == 200
        assert placeholder not in response.text
        assert placeholder not in secret_path.read_text(encoding="utf-8")
        removed = client.delete("/api/settings/provider-key/deepseek")
        assert removed.status_code == 204


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 由 DPAPI 加密路径覆盖")
def test_provider_key_storage_fails_closed_without_os_encryption(tmp_path: Path, monkeypatch) -> None:
    """非 Windows 服务器不得把模型密钥降级为明文保存。"""

    secret_path = tmp_path / "credentials.json"
    monkeypatch.setenv("NOVEL_SECRET_PATH", str(secret_path))
    placeholder = "test-provider-secret-123456"
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/settings/provider-key",
            json={"provider": "deepseek", "api_key": placeholder},
        )
        assert response.status_code == 422
        assert placeholder not in response.text
        assert not secret_path.exists()


def test_kimi_code_key_is_rejected_for_moonshot_batch_api(tmp_path: Path) -> None:
    """Kimi Code 订阅凭据不能被误存成 Moonshot 开放平台密钥。"""

    with client_for(tmp_path) as client:
        response = client.post(
            "/api/settings/provider-key",
            json={"provider": "moonshot", "api_key": "sk-" + "kimi-test-placeholder-value"},
        )
        assert response.status_code == 422
        assert "Kimi Code" in response.text


def test_benchmark_cases_support_full_local_crud_and_recalculation(tmp_path: Path) -> None:
    """人工金标准应能登记、编辑、复算和删除，整个过程不产生模型费用。"""

    with client_for(tmp_path) as client:
        book_id = next(
            book["id"] for book in client.get("/api/books").json()
            if book["title"] == "雾川行记 · 演示"
        )
        created = client.post(
            f"/api/books/{book_id}/benchmarks",
            json={
                "case_type": "segment_accounting",
                "subject": "演示书片段覆盖可复算",
                "expected": {"percent": 0},
                "source_segment": 0,
                "note": "使用第一章作为人工核对入口",
                "critical": False,
                "confirmed_by_user": True,
                "reviewer_id": "test-reviewer",
            },
        )
        assert created.status_code == 201
        case = created.json()
        assert case["passed"] is True
        assert case["source_chapter_title"]

        patched = client.patch(
            f"/api/benchmarks/{case['id']}",
            json={"subject": "演示书全部片段覆盖可复算", "expected": {"percent": 100}},
        )
        assert patched.status_code == 200
        assert patched.json()["subject"] == "演示书全部片段覆盖可复算"

        evaluated = client.post(f"/api/books/{book_id}/benchmarks/evaluate")
        assert evaluated.status_code == 200
        assert evaluated.json()["estimated_cost_usd"] == 0
        assert evaluated.json()["summary"]["total"] == 1

        deleted = client.delete(f"/api/benchmarks/{case['id']}")
        assert deleted.status_code == 204
        assert client.get(f"/api/books/{book_id}/benchmarks").json() == []


def test_benchmark_case_rejects_invalid_chapter_and_duplicate_names(tmp_path: Path) -> None:
    """无效章节和重复金标准必须明确拒绝，避免准确率被错误样本污染。"""

    with client_for(tmp_path) as client:
        book_id = next(
            book["id"] for book in client.get("/api/books").json()
            if book["title"] == "雾川行记 · 演示"
        )
        payload = {
            "case_type": "segment_accounting",
            "subject": "片段覆盖人工核对",
            "expected": {"percent": 100},
            "source_segment": 0,
            "note": "人工检查章节数量与处理记录",
            "critical": True,
        }
        assert client.post(f"/api/books/{book_id}/benchmarks", json=payload).status_code == 201
        duplicate = client.post(f"/api/books/{book_id}/benchmarks", json=payload)
        assert duplicate.status_code == 409
        invalid = client.post(
            f"/api/books/{book_id}/benchmarks",
            json={**payload, "subject": "超出范围的人工核对", "source_segment": 99999},
        )
        assert invalid.status_code == 422


def test_merge_candidates_group_reasons_for_same_pair(tmp_path: Path) -> None:
    """同一实体对的多条判断依据只形成一项待办，拒绝时整组完成。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        with transaction(main.settings.database_path) as connection:
            entities = connection.execute(
                "SELECT id FROM entities WHERE book_id = ? AND kind = 'person' ORDER BY id LIMIT 2",
                (book_id,),
            ).fetchall()
            left_id, right_id = int(entities[0]["id"]), int(entities[1]["id"])
            connection.executemany(
                """
                INSERT INTO entity_merge_candidates(
                    book_id, left_entity_id, right_entity_id, reason, confidence
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (book_id, left_id, right_id, "共享称呼", 0.72),
                    (book_id, left_id, right_id, "身份描述一致", 0.91),
                ],
            )
        overview = client.get(f"/api/books/{book_id}/overview").json()
        candidates = overview["merge_candidates"]
        assert len(candidates) == 1
        assert overview["quality"]["unresolved_merges"] == 1
        assert "共享称呼" in candidates[0]["reason"]
        assert "身份描述一致" in candidates[0]["reason"]
        response = client.patch(f"/api/merge-candidates/{candidates[0]['id']}", json={"status": "rejected"})
        assert response.status_code == 200
        overview = client.get(f"/api/books/{book_id}/overview").json()
        assert overview["merge_candidates"] == []
        assert overview["quality"]["unresolved_merges"] == 0


def test_free_auto_resolution_closes_all_conflict_types_without_model_calls(tmp_path: Path) -> None:
    """保守自动处理应关闭身份、事实和时间冲突，并且不建立模型调用账单。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        with transaction(main.settings.database_path) as connection:
            entities = connection.execute(
                "SELECT id FROM entities WHERE book_id = ? AND kind = 'person' ORDER BY id LIMIT 2",
                (book_id,),
            ).fetchall()
            connection.execute(
                """
                INSERT INTO entity_merge_candidates(
                    book_id, left_entity_id, right_entity_id, reason, confidence, status
                ) VALUES (?, ?, ?, '证据不足的相似名称', 0.7, 'needs_review')
                """,
                (book_id, entities[0]["id"], entities[1]["id"]),
            )
            connection.execute(
                """
                INSERT INTO contradictions(
                    book_id, left_type, left_id, right_type, right_id, summary, confidence
                ) VALUES (?, 'entity', ?, 'entity', ?, '两条身份说明可能矛盾', 0.8)
                """,
                (book_id, entities[0]["id"], entities[1]["id"]),
            )
            events = connection.execute(
                "SELECT id FROM events WHERE book_id = ? ORDER BY narrative_order LIMIT 3",
                (book_id,),
            ).fetchall()
            connection.execute("DELETE FROM event_order_edges WHERE book_id = ?", (book_id,))
            connection.executemany(
                """
                INSERT INTO event_order_edges(
                    book_id, earlier_event_id, later_event_id, relation, confidence
                ) VALUES (?, ?, ?, 'before', ?)
                """,
                [
                    (book_id, events[0]["id"], events[1]["id"], 1.0),
                    (book_id, events[1]["id"], events[2]["id"], 0.9),
                    (book_id, events[2]["id"], events[0]["id"], 0.5),
                ],
            )
            recompute_chronology_dag(connection, book_id)

        response = client.post(f"/api/books/{book_id}/conflicts/auto-resolve")
        assert response.status_code == 200
        result = response.json()
        assert result["estimated_cost_usd"] == 0
        assert result["resolution"]["identity_separated"] == 1
        assert result["resolution"]["contradictions_quarantined"] == 1
        assert result["resolution"]["time_constraints_rejected"] == 1
        assert result["quality"]["conflict_gate_passed"] is True
        with transaction(main.settings.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM model_call_ledger WHERE book_id = ?", (book_id,)).fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM entity_merge_candidates WHERE book_id = ? AND status = 'auto_separate'", (book_id,)).fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM contradictions WHERE book_id = ? AND status = 'auto_quarantined'", (book_id,)).fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM event_order_edges WHERE book_id = ? AND status = 'auto_rejected'", (book_id,)).fetchone()[0] == 1


def test_manual_conflict_actions_preserve_facts_and_revalidate_time_order(tmp_path: Path) -> None:
    """人工裁决应保存理由，反转顺序后重新验算有向无环结构。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        with transaction(main.settings.database_path) as connection:
            entities = connection.execute(
                "SELECT id FROM entities WHERE book_id = ? AND kind = 'person' ORDER BY id LIMIT 2",
                (book_id,),
            ).fetchall()
            contradiction_id = connection.execute(
                """
                INSERT INTO contradictions(
                    book_id, left_type, left_id, right_type, right_id, summary, confidence
                ) VALUES (?, 'entity', ?, 'entity', ?, '测试冲突', 0.8)
                """,
                (book_id, entities[0]["id"], entities[1]["id"]),
            ).lastrowid
            events = connection.execute(
                "SELECT id FROM events WHERE book_id = ? ORDER BY narrative_order LIMIT 3",
                (book_id,),
            ).fetchall()
            connection.execute("DELETE FROM event_order_edges WHERE book_id = ?", (book_id,))
            connection.executemany(
                """
                INSERT INTO event_order_edges(
                    book_id, earlier_event_id, later_event_id, relation, confidence
                ) VALUES (?, ?, ?, 'before', ?)
                """,
                [
                    (book_id, events[0]["id"], events[1]["id"], 1.0),
                    (book_id, events[1]["id"], events[2]["id"], 0.9),
                    (book_id, events[2]["id"], events[0]["id"], 0.5),
                ],
            )
            recompute_chronology_dag(connection, book_id)
            conflict_id = connection.execute(
                "SELECT id FROM event_order_edges WHERE book_id = ? AND status = 'conflict'",
                (book_id,),
            ).fetchone()["id"]

        resolved = client.patch(
            f"/api/contradictions/{contradiction_id}",
            json={"action": "contextual", "reason": "两条说明对应不同故事阶段。"},
        )
        assert resolved.status_code == 200
        reversed_edge = client.patch(
            f"/api/time-conflicts/{conflict_id}",
            json={"action": "reverse", "reason": "原文表明方向应当反转。"},
        )
        assert reversed_edge.status_code == 200
        assert reversed_edge.json()["status"] == "accepted"
        with transaction(main.settings.database_path) as connection:
            contradiction = connection.execute(
                "SELECT status, resolution_reason, resolved_by FROM contradictions WHERE id = ?",
                (contradiction_id,),
            ).fetchone()
            assert contradiction["status"] == "resolved_contextual"
            assert contradiction["resolved_by"] == "human"
            assert "不同故事阶段" in contradiction["resolution_reason"]
            edge = connection.execute(
                "SELECT status, earlier_event_id, later_event_id, resolved_by FROM event_order_edges WHERE id = ?",
                (conflict_id,),
            ).fetchone()
            assert edge["status"] == "accepted"
            assert edge["earlier_event_id"] == events[0]["id"]
            assert edge["later_event_id"] == events[2]["id"]
            assert edge["resolved_by"] == "human"


def test_restart_preserves_per_call_cost_for_mixed_models(tmp_path: Path) -> None:
    """应用重启后应汇总逐次调用金额，不能按任务最初模型的单价覆盖。"""

    database_path = tmp_path / "mixed-cost.db"
    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        database_path = main.settings.database_path
        with transaction(database_path) as connection:
            job_id = connection.execute(
                """
                INSERT INTO analysis_jobs(
                    book_id, provider, model, status, start_segment, end_segment,
                    prompt_version, cache_hit_input_usd_per_million,
                    cache_miss_input_usd_per_million, output_usd_per_million,
                    pricing_source, pricing_effective_date, input_tokens, output_tokens,
                    cache_hit_input_tokens, cache_miss_input_tokens, estimated_cost_usd
                ) VALUES (?, 'deepseek', 'deepseek-chat', 'completed', 0, 4,
                    'test', 0.07, 0.27, 1.1, 'test', '2026-08-24',
                    100000, 10000, 0, 100000, 0.038)
                """,
                (book_id,),
            ).lastrowid
            connection.executemany(
                """
                INSERT INTO model_call_ledger(
                    book_id, job_id, purpose, provider, model, prompt_version,
                    request_hash, status, estimated_cost_usd
                ) VALUES (?, ?, 'test', 'deepseek', ?, 'test', ?, 'completed', ?)
                """,
                [
                    (book_id, job_id, "deepseek-chat", "mixed-a", 0.02),
                    (book_id, job_id, "deepseek-v4-flash", "mixed-b", 0.03),
                ],
            )

    initialize(database_path)
    with transaction(database_path) as connection:
        job = connection.execute(
            "SELECT estimated_cost_usd, status, quality_gate_status FROM analysis_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    assert job["estimated_cost_usd"] == 0.05
    assert job["status"] == "needs_review"
    assert job["quality_gate_status"] == "needs_review"


def test_internal_evaluation_does_not_create_reader_facing_quality_error(tmp_path: Path) -> None:
    """内部回归样本不足时不应给普通用户制造悬挂任务。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        with transaction(main.settings.database_path) as connection:
            connection.execute(
                """
                INSERT INTO analysis_jobs(
                    book_id, provider, model, status, start_segment, end_segment,
                    prompt_version, pricing_source, pricing_effective_date
                ) VALUES (?, 'deepseek', 'deepseek-v4-flash', 'completed', 0, 4,
                    'test', 'test', '2026-08-24')
                """,
                (book_id,),
            )
        overview = client.get(f"/api/books/{book_id}/overview").json()
        assert overview["quality"]["structural_gate_passed"] is True
        assert overview["quality"]["accuracy_gate_required"] is True
        assert overview["quality"]["accuracy_gate_passed"] is False
        assert overview["quality"]["internal_evaluation_gate_passed"] is False
        assert overview["quality"]["quality_gate_passed"] is True
        assert not any("金标准" in issue["title"] for issue in overview["quality"]["issues"])


def test_record_regeneration_requires_declarative_instruction_and_confirmation(tmp_path: Path) -> None:
    """二次生成拒绝问句，并且只有确认草稿后才修改正式记录。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        note = overview["world_notes"][0]
        rejected = client.post(
            f"/api/records/world_note/{note['id']}/drafts",
            json={"provider": "mock", "instruction": "这个设定到底是什么？", "max_cost_usd": 0},
        )
        assert rejected.status_code == 422
        created = client.post(
            f"/api/records/world_note/{note['id']}/drafts",
            json={"provider": "mock", "instruction": "整理原文已经明确的条件和后果。", "max_cost_usd": 0},
        )
        assert created.status_code == 201
        draft = created.json()
        before = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        assert next(item for item in before["world_notes"] if item["id"] == note["id"])["summary"] == note["summary"]
        applied = client.post(f"/api/record-drafts/{draft['id']}/apply")
        assert applied.status_code == 200
        with transaction(main.settings.database_path) as connection:
            versions = connection.execute(
                "SELECT COUNT(*) FROM record_versions WHERE target_type = 'world_note' AND target_id = ?",
                (note["id"],),
            ).fetchone()[0]
        assert versions >= 2


def test_analysis_estimate_does_not_create_job_or_model_call(tmp_path: Path) -> None:
    """用量预估只读原文和价格，不会偷偷建立任务或调用模型。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        response = client.post(
            f"/api/books/{book_id}/jobs/estimate",
            json={"provider": "mock", "start_segment": 0, "end_segment": None, "review_mode": "local"},
        )
        assert response.status_code == 200
        estimate = response.json()
        assert estimate["segment_count"] == 5
        with transaction(main.settings.database_path) as connection:
            assert connection.execute("SELECT COUNT(*) FROM analysis_jobs").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM model_call_ledger").fetchone()[0] == 0


def test_world_information_supports_create_archive_and_restore(tmp_path: Path) -> None:
    """人工世界信息能够创建、归档和恢复，并保留版本记录。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        created = client.post(
            f"/api/books/{book_id}/world-notes",
            json={"category": "rule", "title": "人工测试规则", "summary": "这条说明用于验证完整管理流程。"},
        )
        assert created.status_code == 201
        note_id = created.json()["id"]
        overview = client.get(f"/api/books/{book_id}/overview").json()
        assert any(item["id"] == note_id and item["created_by"] == "human" for item in overview["world_notes"])

        assert client.delete(f"/api/world-notes/{note_id}").status_code == 204
        overview = client.get(f"/api/books/{book_id}/overview").json()
        assert all(item["id"] != note_id for item in overview["world_notes"])
        archived = client.get(f"/api/books/{book_id}/world-notes/archived").json()
        assert any(item["id"] == note_id for item in archived)

        restored = client.post(f"/api/world-notes/{note_id}/restore")
        assert restored.status_code == 200
        overview = client.get(f"/api/books/{book_id}/overview").json()
        assert any(item["id"] == note_id for item in overview["world_notes"])


def test_connectivity_conflict_can_be_resolved_with_verified_manual_relation(tmp_path: Path) -> None:
    """孤立节点可以用同章逐字引文人工补建关系，不能长期悬挂成报错。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        target = next(item for item in overview["entities"] if item["kind"] == "person")
        segment = client.get(f"/api/segments/{overview['segments'][0]['id']}").json()
        quote = segment["text"][: min(20, len(segment["text"]))]
        with transaction(main.settings.database_path) as connection:
            entity_id = int(connection.execute(
                """
                INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
                VALUES (?, 'person', '人工待连人物', '用于验证人工关系闭环。', 0.5, 0, 'human')
                """,
                (book_id,),
            ).lastrowid)
            connection.execute(
                """
                INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end)
                VALUES (?, 'entity', ?, ?, ?, 0, ?)
                """,
                (book_id, entity_id, segment["id"], quote, len(quote)),
            )
            refresh_local_reviews(connection, book_id)
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        review = next(item for item in overview["connectivity_reviews"] if item["entity_id"] == entity_id)
        response = client.post(
            f"/api/connectivity-reviews/{review['id']}/relation",
            json={
                "target_entity_id": target["id"],
                "predicate": "相识",
                "summary": "用户依据逐字原文确认二者相识。",
                "segment_id": segment["id"],
                "evidence_quote": quote,
            },
        )
        assert response.status_code == 201
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        updated = next(item for item in overview["connectivity_reviews"] if item["entity_id"] == entity_id)
        assert updated["status"] == "connected"
        assert any(item["source_entity_id"] == entity_id and item["target_entity_id"] == target["id"] for item in overview["claims"])


def test_connectivity_audit_only_scans_completed_analysis_segments(tmp_path: Path) -> None:
    """局部分析的孤立节点复审不得读取尚未分析的后续章节。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        with transaction(main.settings.database_path) as connection:
            unrestricted, _ = _multi_entity_windows(connection, book_id, {999_001: ["沈烬"]})
            scoped, stats = _multi_entity_windows(
                connection,
                book_id,
                {999_001: ["沈烬"]},
                {0},
            )
        assert unrestricted
        assert scoped == []
        assert stats[999_001]["mention_count"] == 0


def test_large_demo_has_terminal_relationship_and_location_review_states(tmp_path: Path) -> None:
    """大型演示的关系和剧情位置必须全部进入可解释状态。"""

    with client_for(tmp_path) as client:
        large = next(book for book in client.get("/api/books").json() if "120章" in book["title"])
        overview = client.get(f"/api/books/{large['id']}/overview?through_segment=119").json()
        assert overview["connectivity_reviews"]
        assert all(item["status"] in {"connected", "confirmed_isolated", "ambiguous", "pending"} for item in overview["connectivity_reviews"])
        assert len(overview["event_location_reviews"]) == len(overview["events"])
        assert overview["quality"]["location_unresolved_events"] == 0


def test_event_location_is_auto_repaired_or_manually_resolved_with_source_quote(tmp_path: Path) -> None:
    """地点冲突先自动补齐唯一证据，剩余未知项允许用户用逐字原文解决。"""

    with client_for(tmp_path) as client:
        book_id = next(book["id"] for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        source = client.get(f"/api/segments/{overview['segments'][0]['id']}").json()
        place_name = "北方"
        assert place_name in source["text"]
        with transaction(main.settings.database_path) as connection:
            place_id = int(connection.execute(
                """
                INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
                VALUES (?, 'place', ?, '自动地点复审测试。', 1, 0, 'human')
                """,
                (book_id, place_name),
            ).lastrowid)
            automatic_event = int(connection.execute(
                """
                INSERT INTO events(book_id, title, summary, narrative_order, story_order,
                    temporal_kind, confidence, first_segment, created_by)
                VALUES (?, '自动定位测试', ?, -1000, -1000, 'unknown', 1, 0, 'human')
                """,
                (book_id, f"人物在{place_name}停留。"),
            ).lastrowid)
            manual_event = int(connection.execute(
                """
                INSERT INTO events(book_id, title, summary, narrative_order, story_order,
                    temporal_kind, confidence, first_segment, created_by)
                VALUES (?, '人工定位测试', '剧情位置仍待用户确认。', -2000, -2000, 'unknown', 1, 0, 'human')
                """,
                (book_id,),
            ).lastrowid)
            quote = source["text"][: min(40, len(source["text"]))]
            distractor = quote[:2]
            if distractor != place_name:
                connection.execute(
                    """
                    INSERT INTO entities(book_id, kind, name, summary, importance, first_segment, created_by)
                    VALUES (?, 'place', ?, '用于制造多地点证据歧义。', 0.1, 0, 'human')
                    """,
                    (book_id, distractor),
                )
            for event_id in (automatic_event, manual_event):
                connection.execute(
                    """
                    INSERT INTO evidence(book_id, target_type, target_id, segment_id, quote, quote_start, quote_end)
                    VALUES (?, 'event', ?, ?, ?, 0, ?)
                    """,
                    (book_id, event_id, source["id"], quote, len(quote)),
                )
            refresh_local_reviews(connection, book_id)
        overview = client.get(f"/api/books/{book_id}/overview?through_segment=4").json()
        automatic_review = next(item for item in overview["event_location_reviews"] if item["event_id"] == automatic_event)
        manual_review = next(item for item in overview["event_location_reviews"] if item["event_id"] == manual_event)
        assert automatic_review["status"] == "explicit"
        assert automatic_review["effective_location_entity_id"] == place_id
        assert manual_review["status"] == "unresolved"
        response = client.patch(
            f"/api/event-location-reviews/{manual_event}",
            json={"location_entity_id": place_id, "segment_id": source["id"], "evidence_quote": quote},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "explicit"
