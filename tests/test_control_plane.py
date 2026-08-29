"""验证透明协作、分层提示词、运行清单和模型赛马控制面。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
import app.providers as providers
from app.benchmarks import evaluation_progress, seed_benchmark_cases
from app.db import initialize, transaction


def client_for(tmp_path: Path) -> TestClient:
    """为控制面测试创建不共享状态的本地数据库。"""

    main.settings = replace(
        main.settings,
        database_path=tmp_path / "control-plane.db",
        deepseek_api_key=None,
        moonshot_api_key=None,
    )
    return TestClient(main.app)


def demo_book(client: TestClient) -> dict:
    """返回最小真实接口演示书。"""

    return next(book for book in client.get("/api/books").json() if book["title"] == "雾川行记 · 演示")


def test_control_plane_exposes_contract_prompts_routes_and_historical_feedback(tmp_path: Path) -> None:
    """控制台首次打开就应解释质量合同、提示词和历史反馈。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        response = client.get(f"/api/books/{book_id}/control-plane")
        assert response.status_code == 200
        body = response.json()
        assert body["contract"]["quality"]["overall_holdout_percent"] == 95
        assert len(body["prompt_bundles"]) == 4
        assert any(item["provider"] == "codex_luna" for item in body["model_routes"])
        assert any("黑盒" in item["original_text"] for item in body["collaboration"])


def test_domain_rule_changes_final_prompt_but_external_fact_stays_separate(tmp_path: Path) -> None:
    """阅读规则进入最终提示词，外部资料只能在隔离层显示。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        original = client.get(f"/api/books/{book_id}/prompt-bundles/extraction").json()
        rejected = client.post(
            f"/api/books/{book_id}/domain-rules",
            json={"statement": "父母关系需要检查吗？", "task_key": "extraction"},
        )
        assert rejected.status_code == 422
        rule = client.post(
            f"/api/books/{book_id}/domain-rules",
            json={
                "statement": "明确出现父母称谓时，应当检查称谓对象能否唯一对应现有人物",
                "task_key": "extraction",
            },
        )
        assert rule.status_code == 201
        fact = client.post(
            f"/api/books/{book_id}/external-facts",
            json={"statement": "影视版改编过人物关系", "source_label": "测试外部资料"},
        )
        assert fact.status_code == 201
        rendered = client.get(f"/api/books/{book_id}/prompt-bundles/extraction").json()
        assert "明确出现父母称谓" in rendered["system_prompt"]
        assert "影视版改编" not in rendered["system_prompt"]
        assert rendered["external_facts"][0]["statement"] == "影视版改编过人物关系"
        assert rendered["external_facts_injected"] is False
        assert rendered["prompt_hash"] != original["prompt_hash"]


def test_key_decision_cannot_skip_confirmation(tmp_path: Path) -> None:
    """涉及核心目标的事项必须先确认，不能从解释状态直接跳到实施。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        created = client.post(
            f"/api/books/{book_id}/collaboration",
            json={
                "original_text": "修改正式准确率口径",
                "interpreted_goal": "把正式准确率门槛改为新的公开口径",
                "acceptance": ["页面和门禁使用同一口径"],
                "impact": ["产品合同", "发布门禁"],
                "requires_confirmation": True,
            },
        ).json()
        blocked = client.patch(f"/api/collaboration/{created['id']}", json={"status": "implementing"})
        assert blocked.status_code == 409
        confirmed = client.patch(f"/api/collaboration/{created['id']}", json={"status": "confirmed"})
        assert confirmed.status_code == 200
        implementing = client.patch(f"/api/collaboration/{created['id']}", json={"status": "implementing"})
        assert implementing.status_code == 200


def test_prompt_draft_requires_trial_and_full_release_gate_before_promotion(tmp_path: Path) -> None:
    """单片段试跑只能证明候选可用，未满三百条金标准时不能冒充正式版本。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        production = client.get(f"/api/books/{book_id}/prompt-bundles/extraction").json()
        core = next(layer["text"] for layer in production["layers"] if layer["key"] == "core")
        draft_response = client.post(
            f"/api/prompt-bundles/extraction/drafts?book_id={book_id}",
            json={
                "core_text": core,
                "task_text": "输出前再次检查主要人物姓名和证据引文",
                "change_note": "增加主要人物自检回归",
            },
        )
        assert draft_response.status_code == 201
        draft = draft_response.json()
        blocked = client.post(f"/api/prompt-bundles/{draft['id']}/promote?book_id={book_id}")
        assert blocked.status_code == 409
        overview = client.get(f"/api/books/{book_id}/overview").json()
        trial = client.post(
            f"/api/prompt-bundles/{draft['id']}/trial",
            json={"book_id": book_id, "segment_id": overview["segments"][0]["id"], "provider": "mock"},
        )
        assert trial.status_code == 200
        assert trial.json()["validation"]["quote_integrity_percent"] == 100
        promoted = client.post(f"/api/prompt-bundles/{draft['id']}/promote?book_id={book_id}")
        assert promoted.status_code == 409
        assert "300" in promoted.json()["detail"]
        runs = client.get(f"/api/books/{book_id}/runs").json()
        assert runs[0]["run_kind"] == "prompt_trial"
        assert runs[0]["prompt_hash"] == draft["prompt_hash"]


def test_release_gate_discloses_missing_scale_without_claiming_ninety_five_percent(tmp_path: Path) -> None:
    """小型演示数据必须明确显示仍缺少多少真实评估案例和作品。"""

    with client_for(tmp_path) as client:
        gate = client.get("/api/eval-suites/release-gate")
        assert gate.status_code == 200
        body = gate.json()
        assert body["release_gate_passed"] is False
        assert body["minimum_cases"] == 300
        assert body["minimum_books"] == 5
        assert body["remaining_cases"] > 0


def test_program_seeded_cases_remain_candidates_until_a_human_reviews_them(tmp_path: Path) -> None:
    """程序预置题不得再次变成人工金标准或伪保留案例。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        with transaction(main.settings.database_path) as connection:
            connection.execute("UPDATE books SET title = '西游记' WHERE id = ?", (book_id,))
            assert seed_benchmark_cases(connection, book_id) == 28
            connection.execute(
                """
                UPDATE quality_benchmark_cases
                SET confirmed_by_user = 1, holdout = 1, origin = 'manual',
                    review_status = 'sealed_holdout'
                WHERE book_id = ?
                """,
                (book_id,),
            )
        initialize(main.settings.database_path)
        with transaction(main.settings.database_path) as connection:
            progress = evaluation_progress(connection)
            seeded = connection.execute(
                """
                SELECT COUNT(*) AS total,
                    SUM(CASE WHEN review_status = 'candidate' THEN 1 ELSE 0 END) AS candidates,
                    SUM(confirmed_by_user) AS confirmed,
                    SUM(holdout) AS holdouts
                FROM quality_benchmark_cases WHERE book_id = ?
                """,
                (book_id,),
            ).fetchone()
        assert int(seeded["total"]) == 28
        assert int(seeded["candidates"]) == 28
        assert int(seeded["confirmed"] or 0) == 0
        assert int(seeded["holdouts"] or 0) == 0
        assert progress["confirmed_cases"] == 0
        assert progress["holdout_cases"] == 0
        assert progress["candidate_cases"] == 28


def test_formal_corpus_declares_twelve_cross_genre_open_works(tmp_path: Path) -> None:
    """质量范围公开区分直接覆盖和开放作品代理，不冒充商业文体验证。"""

    with client_for(tmp_path) as client:
        response = client.get("/api/eval-suites/corpus")
        assert response.status_code == 200
        body = response.json()
        assert body["case_policy"]["total_cases"] == 300
        assert body["case_policy"]["total_sealed_holdout_cases"] == 60
        assert len(body["works"]) == 12
        assert sum(item["coverage_role"] == "direct" for item in body["works"]) == 5
        assert sum(item["coverage_role"] == "proxy" for item in body["works"]) == 7
        assert {item["language"] for item in body["works"]} == {"zh-CN", "en", "ja"}


def test_holdout_answer_is_hidden_from_normal_benchmark_listing(tmp_path: Path) -> None:
    """保留测试参与门禁，但提示词调试页面看不到答案和当前结果。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        created = client.post(
            f"/api/books/{book_id}/benchmarks",
            json={
                "case_type": "segment_accounting",
                "subject": "保留集覆盖率案例",
                "expected": {"percent": 100},
                "source_segment": 0,
                "note": "只用于正式评估门禁",
                "holdout": True,
                "confirmed_by_user": True,
                "reviewer_id": "test-reviewer",
            },
        )
        assert created.status_code == 201
        listed = client.get(f"/api/books/{book_id}/benchmarks").json()
        hidden = next(item for item in listed if item["id"] == created.json()["id"])
        assert hidden["expected"] == {"withheld": True}
        assert hidden["actual"] == {"withheld": True}
        assert hidden["passed"] is None
        explicitly_requested = client.get(
            f"/api/books/{book_id}/benchmarks", params={"reveal_holdout": True}
        ).json()
        assert next(item for item in explicitly_requested if item["id"] == created.json()["id"])["expected"] == {"withheld": True}
        before_second_review = client.get("/api/eval-suites/release-gate").json()
        assert before_second_review["holdout_cases"] == 0
        assert before_second_review["second_review_pending"] == 1
        reviewed = client.post(
            f"/api/benchmarks/{created.json()['id']}/second-review",
            json={"reviewer_id": "test-reviewer-pass-2", "note": "再次核对原文和答案一致"},
        )
        assert reviewed.status_code == 200
        after_second_review = client.get("/api/eval-suites/release-gate").json()
        assert after_second_review["holdout_cases"] == 1
        assert after_second_review["second_review_pending"] == 0


def test_evidence_candidates_require_human_confirmation_before_counting(tmp_path: Path) -> None:
    """候选只帮助人工建立评估集，确认前后必须处于不同统计状态。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        refreshed = client.post(f"/api/books/{book_id}/benchmark-candidates/refresh")
        assert refreshed.status_code == 200
        candidates = client.get(f"/api/books/{book_id}/benchmark-candidates").json()
        assert candidates
        assert candidates[0]["candidate_origin"] in {"evidence_index", "local_check"}
        assert isinstance(candidates[0]["evidence"], list)
        before = client.get("/api/eval-suites/release-gate").json()["confirmed_cases"]
        resolved = client.post(
            f"/api/benchmark-candidates/{candidates[0]['id']}/resolve",
            json={"action": "accept", "note": "人工核对候选对应的原文章节"},
        )
        assert resolved.status_code == 200
        assert resolved.json()["candidate"]["status"] == "accepted"
        assert resolved.json()["benchmark"]["confirmed_by_user"] is True
        after = client.get("/api/eval-suites/release-gate").json()["confirmed_cases"]
        assert after == before + 1


def test_user_correction_becomes_linked_regression_case(tmp_path: Path) -> None:
    """具体纠正应同时保留原始反馈和永久金标准关联。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        item = client.post(
            f"/api/books/{book_id}/collaboration",
            json={
                "original_text": "全部片段都应当纳入处理记录",
                "interpreted_goal": "使用片段覆盖金标准长期防止漏章",
                "acceptance": ["覆盖率本地复算为百分之百"],
            },
        ).json()
        regression = client.post(
            f"/api/collaboration/{item['id']}/regression",
            json={
                "case_type": "segment_accounting",
                "subject": "用户纠正后的全片段覆盖",
                "expected": {"percent": 100},
                "source_segment": 0,
                "note": "用户明确要求所有原文片段均有处理记录",
                "critical": True,
            },
        )
        assert regression.status_code == 201
        control = client.get(f"/api/books/{book_id}/control-plane").json()
        linked = next(value for value in control["collaboration"] if value["id"] == item["id"])
        assert linked["regression_case_id"] == regression.json()["id"]


def test_dry_model_race_records_readiness_without_spending(tmp_path: Path) -> None:
    """非实时赛马只记录准备状态，不调用任何模型或伪造合格结果。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        response = client.post(
            f"/api/books/{book_id}/model-races",
            json={"providers": ["mock"], "run_live_canary": False},
        )
        assert response.status_code == 200
        report = response.json()["reports"][0]
        assert report["eligible"] is False
        assert report["status"] == "需要真实单片段赛马"
        manifest = client.get(f"/api/runs/{report['run_manifest_id']}").json()
        assert manifest["status"] == "prepared"
        assert manifest["estimated_cost_usd"] is None


def test_successful_canary_does_not_trip_circuit_while_global_gate_is_pending(tmp_path: Path) -> None:
    """模型调用成功和正式资格是两件事，缺少三百条金标准不能累计技术故障。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        segment_id = client.get(f"/api/books/{book_id}/overview").json()["segments"][0]["id"]
        response = client.post(
            f"/api/books/{book_id}/model-races",
            json={"providers": ["mock"], "run_live_canary": True, "segment_id": segment_id},
        )
        assert response.status_code == 200
        report = response.json()["reports"][0]
        assert report["status"] == "试跑通过，正式门禁未通过"
        assert report["eligible"] is False
        route = next(item for item in client.get("/api/model-routes").json() if item["provider"] == "mock")
        assert route["consecutive_failures"] == 0
        assert route["circuit_open_until"] is None


def test_release_gate_ignores_conflicts_from_books_outside_confirmed_eval_scope(tmp_path: Path) -> None:
    """演示书的待审项不能污染另一部正式评估作品的发布统计。"""

    with client_for(tmp_path) as client:
        books = client.get("/api/books").json()
        evaluated_book = books[0]
        unrelated_book = books[1]
        created = client.post(
            f"/api/books/{evaluated_book['id']}/benchmarks",
            json={
                "case_type": "segment_accounting",
                "subject": "正式评估范围案例",
                "expected": {"percent": 100},
                "source_segment": 0,
                "note": "用于验证发布门禁的作品范围",
                "confirmed_by_user": True,
                "reviewer_id": "test-reviewer",
            },
        )
        assert created.status_code == 201
        with transaction(main.settings.database_path) as connection:
            entities = connection.execute(
                "SELECT id FROM entities WHERE book_id = ? ORDER BY id LIMIT 2",
                (unrelated_book["id"],),
            ).fetchall()
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_merge_candidates(
                    book_id, left_entity_id, right_entity_id, reason, status
                ) VALUES (?, ?, ?, '范围外演示冲突', 'unreviewed')
                """,
                (unrelated_book["id"], int(entities[0]["id"]), int(entities[1]["id"])),
            )
        gate = client.get("/api/eval-suites/release-gate").json()
        assert gate["book_count"] == 1
        assert gate["books_below_minimum_cases"] == 1
        assert gate["unresolved_conflicts"] == 0


def test_prompt_rollback_restores_prior_formal_version_and_preserves_history(tmp_path: Path) -> None:
    """回滚必须恢复旧正式版，同时保留被替换版本供审计。"""

    with client_for(tmp_path) as client:
        book_id = demo_book(client)["id"]
        original = client.get(f"/api/books/{book_id}/prompt-bundles/extraction").json()
        core = next(layer["text"] for layer in original["layers"] if layer["key"] == "core")
        draft = client.post(
            f"/api/prompt-bundles/extraction/drafts?book_id={book_id}",
            json={"core_text": core, "task_text": "候选版本", "change_note": "回滚测试"},
        ).json()
        with transaction(main.settings.database_path) as connection:
            connection.execute(
                "UPDATE prompt_bundle_versions SET status = 'archived', promoted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (original["id"],),
            )
            connection.execute(
                "UPDATE prompt_bundle_versions SET status = 'production', promoted_at = CURRENT_TIMESTAMP WHERE id = ?",
                (draft["id"],),
            )
        blocked = client.post(f"/api/prompt-bundles/{original['id']}/rollback?book_id={book_id}")
        assert blocked.status_code == 409
        restored = client.post(
            f"/api/prompt-bundles/{original['id']}/rollback?book_id={book_id}&confirmed=true"
        )
        assert restored.status_code == 200
        assert restored.json()["id"] == original["id"]
        versions = client.get(f"/api/prompt-bundles?book_id={book_id}").json()["versions"]
        assert len(versions) >= 2
        statuses = {item["id"]: item["status"] for item in versions}
        assert statuses[original["id"]] == "production"
        assert statuses[draft["id"]] == "archived"


def test_auto_route_does_not_spend_on_unqualified_provider(tmp_path: Path) -> None:
    """即使本机有密钥，尚未通过赛马的供应商也不能获得自动正式流量。"""

    with client_for(tmp_path):
        configured = replace(main.settings, deepseek_api_key="test-key-never-sent")
        assert providers._auto_provider_name(configured) == "mock"
