"""计算一本书当前可核验的数据质量和待处理问题。"""

from __future__ import annotations

import sqlite3
from typing import Any


def build_quality_report(connection: sqlite3.Connection, book_id: int, visible: int) -> dict[str, Any]:
    """从数据库约束和证据覆盖生成可复算质量报告。"""

    book = connection.execute("SELECT segment_count FROM books WHERE id = ?", (book_id,)).fetchone()
    total_segments = int(book["segment_count"]) if book is not None else 0
    processed_segments = connection.execute(
        "SELECT COUNT(DISTINCT segment_id) FROM segment_results WHERE book_id = ?",
        (book_id,),
    ).fetchone()[0]
    target_specs = (
        ("entity", "entities"),
        ("claim", "claims"),
        ("place_relation", "place_relations"),
        ("event", "events"),
        ("journey_leg", "journey_legs"),
        ("world_note", "world_notes"),
        ("entry", "entries"),
    )
    total_facts = 0
    evidenced_facts = 0
    missing_evidence: list[dict[str, Any]] = []
    for target_type, table in target_specs:
        source_filter = ""
        if table == "world_notes":
            source_filter = " AND archived_at IS NULL"
        if table in {"world_notes", "entries"}:
            source_filter += (
                " AND (created_by != 'human' OR EXISTS ("
                "SELECT 1 FROM evidence source_evidence "
                f"WHERE source_evidence.target_type = '{target_type}' AND source_evidence.target_id = {table}.id))"
            )
        records = connection.execute(
            f"SELECT id FROM {table} WHERE book_id = ? AND first_segment <= ?{source_filter}",  # noqa: S608
            (book_id, visible),
        ).fetchall()
        total_facts += len(records)
        for record in records:
            count = connection.execute(
                "SELECT COUNT(*) FROM evidence WHERE target_type = ? AND target_id = ?",
                (target_type, record["id"]),
            ).fetchone()[0]
            if count:
                evidenced_facts += 1
            else:
                missing_evidence.append({"target_type": target_type, "target_id": int(record["id"])})

    low_confidence = 0
    for table in ("claims", "place_relations", "events", "world_notes", "entries"):
        low_confidence += connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE book_id = ? AND first_segment <= ? AND confidence < 0.65",  # noqa: S608
            (book_id, visible),
        ).fetchone()[0]
    unknown_time = connection.execute(
        "SELECT COUNT(*) FROM events WHERE book_id = ? AND first_segment <= ? AND temporal_kind = 'unknown'",
        (book_id, visible),
    ).fetchone()[0]
    event_location_counts = connection.execute(
        """
        SELECT COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN location_entity_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS located
        FROM events WHERE book_id = ? AND first_segment <= ?
        """,
        (book_id, visible),
    ).fetchone()
    relationship_counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM entities WHERE book_id = ? AND first_segment <= ?
                AND kind IN ('person', 'faction')) AS total,
            COUNT(DISTINCT entity_id) AS connected
        FROM (
            SELECT claim.source_entity_id AS entity_id FROM claims claim
                JOIN entities entity ON entity.id = claim.source_entity_id
                WHERE claim.book_id = ? AND claim.first_segment <= ? AND claim.status != 'rejected'
                  AND entity.kind IN ('person', 'faction')
            UNION
            SELECT claim.target_entity_id AS entity_id FROM claims claim
                JOIN entities entity ON entity.id = claim.target_entity_id
                WHERE claim.book_id = ? AND claim.first_segment <= ? AND claim.status != 'rejected'
                  AND entity.kind IN ('person', 'faction')
        )
        """,
        (book_id, visible, book_id, visible, book_id, visible),
    ).fetchone()
    connectivity_reviews = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status = 'connected' THEN 1 ELSE 0 END), 0) AS connected,
            COALESCE(SUM(CASE WHEN status = 'confirmed_isolated' THEN 1 ELSE 0 END), 0) AS isolated,
            COALESCE(SUM(CASE WHEN status = 'ambiguous' THEN 1 ELSE 0 END), 0) AS ambiguous,
            COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending
        FROM entity_connectivity_reviews WHERE book_id = ?
        """,
        (book_id,),
    ).fetchone()
    location_reviews = connection.execute(
        """
        SELECT
            COALESCE(SUM(CASE WHEN status = 'explicit' THEN 1 ELSE 0 END), 0) AS explicit,
            COALESCE(SUM(CASE WHEN status = 'inherited' THEN 1 ELSE 0 END), 0) AS inherited,
            COALESCE(SUM(CASE WHEN status = 'unresolved' THEN 1 ELSE 0 END), 0) AS unresolved
        FROM event_location_reviews WHERE book_id = ?
        """,
        (book_id,),
    ).fetchone()
    map_topology_edges = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM place_relations
             WHERE book_id = ? AND first_segment <= ? AND source_entity_id != target_entity_id)
            + (SELECT COUNT(*) FROM journey_legs
               WHERE book_id = ? AND first_segment <= ?
                 AND from_entity_id IS NOT NULL AND from_entity_id != to_entity_id)
        """,
        (book_id, visible, book_id, visible),
    ).fetchone()[0]
    unresolved_merges = connection.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT left_entity_id, right_entity_id
            FROM entity_merge_candidates
            WHERE book_id = ? AND status IN ('unreviewed', 'needs_review')
            GROUP BY left_entity_id, right_entity_id
        )
        """,
        (book_id,),
    ).fetchone()[0]
    contradictions = connection.execute(
        "SELECT COUNT(*) FROM contradictions WHERE book_id = ? AND status = 'unreviewed'",
        (book_id,),
    ).fetchone()[0]
    time_conflicts = connection.execute(
        "SELECT COUNT(*) FROM event_order_edges WHERE book_id = ? AND status = 'conflict'",
        (book_id,),
    ).fetchone()[0]
    benchmark = connection.execute(
        """
        SELECT COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END), 0) AS passed,
            COALESCE(SUM(CASE WHEN critical = 1 AND passed = 0 THEN 1 ELSE 0 END), 0) AS critical_failed,
            COALESCE(SUM(CASE WHEN passed IS NULL THEN 1 ELSE 0 END), 0) AS pending
        FROM quality_benchmark_cases
        WHERE book_id = ? AND confirmed_by_user = 1
          AND review_status IN ('confirmed_development', 'sealed_holdout', 'adjudicated')
        """,
        (book_id,),
    ).fetchone()
    accuracy_gate_required = bool(connection.execute(
        "SELECT 1 FROM analysis_jobs WHERE book_id = ? AND provider NOT IN ('mock', 'demo') LIMIT 1",
        (book_id,),
    ).fetchone())
    relation_pairs = connection.execute(
        """
        SELECT source_entity_id, target_entity_id, COUNT(DISTINCT predicate) AS predicates
        FROM claims WHERE book_id = ? AND first_segment <= ? AND status != 'rejected'
        GROUP BY source_entity_id, target_entity_id HAVING COUNT(DISTINCT predicate) >= 3
        """,
        (book_id, visible),
    ).fetchall()

    issues: list[dict[str, Any]] = []
    if processed_segments < total_segments:
        issues.append(
            {
                "level": "info",
                "title": "整本书仍在分析",
                "detail": f"已完成 {processed_segments}/{total_segments} 个片段，未完成片段不会生成结论。",
            }
        )
    if unresolved_merges:
        issues.append(
            {
                "level": "error",
                "title": "存在待确认的同名或别名人物",
                "detail": f"共有 {unresolved_merges} 组。自动处理会在证据不足时保持分离，人工也可逐组归并。",
            }
        )
    if unknown_time:
        issues.append(
            {
                "level": "info",
                "title": "部分事件缺少明确时间",
                "detail": f"共有 {unknown_time} 条。系统保留未知状态，没有编造日期。",
            }
        )
    total_events = int(event_location_counts["total"] or 0)
    located_events = int(event_location_counts["located"] or 0)
    location_coverage = round(located_events / total_events * 100, 1) if total_events else None
    relationship_entities = int(relationship_counts["total"] or 0)
    connected_relationship_entities = int(relationship_counts["connected"] or 0)
    relationship_coverage = round(
        connected_relationship_entities / relationship_entities * 100, 1
    ) if relationship_entities else None
    analysis_complete = processed_segments >= total_segments and total_segments > 0
    # 明确地点是地图锚点，沿用地点负责连续场景。每四步至少一个明确锚点，
    # 再配合零未解决位置，能避免“一处地点沿用全书”这种虚假完整。
    explicit_location_density_passed = total_events < 5 or located_events * 4 >= total_events
    if analysis_complete and not explicit_location_density_passed:
        issues.append(
            {
                "level": "error",
                "title": "明确地点锚点不足",
                "detail": f"{total_events} 个事件中只有 {located_events} 个明确地点，连续沿用跨度过长，地图位置仍需补齐。",
            }
        )
    review_connected = int(connectivity_reviews["connected"] or 0)
    confirmed_isolated = int(connectivity_reviews["isolated"] or 0)
    ambiguous_connectivity = int(connectivity_reviews["ambiguous"] or 0)
    pending_connectivity = int(connectivity_reviews["pending"] or 0)
    if analysis_complete and (ambiguous_connectivity or pending_connectivity):
        issues.append(
            {
                "level": "error",
                "title": "人物关系复审尚未闭环",
                "detail": f"仍有 {pending_connectivity} 个节点等待自动复审、{ambiguous_connectivity} 个节点需要自动重试或人工裁定。",
            }
        )
    if confirmed_isolated:
        issues.append(
            {
                "level": "info",
                "title": "存在已确认孤立人物或势力",
                "detail": f"共有 {confirmed_isolated} 个节点已经扫描全部提及窗口，未发现可逐字验证的关系，已从核心图单列。",
            }
        )
    place_count = connection.execute(
        "SELECT COUNT(*) FROM entities WHERE book_id = ? AND first_segment <= ? AND kind = 'place'",
        (book_id, visible),
    ).fetchone()[0]
    if analysis_complete and int(place_count) >= 2 and int(map_topology_edges or 0) == 0:
        issues.append(
            {
                "level": "error",
                "title": "地图缺少地点连接",
                "detail": f"已经识别 {int(place_count)} 个地点，但方位和移动连接均为 0，当前地图结构不合格。",
            }
        )
    unresolved_locations = int(location_reviews["unresolved"] or 0)
    inherited_locations = int(location_reviews["inherited"] or 0)
    location_gate_required = total_events >= 5 or int(place_count) > 0
    if analysis_complete and location_gate_required and unresolved_locations:
        issues.append(
            {
                "level": "error",
                "title": "部分剧情步骤没有可核验位置",
                "detail": f"共有 {unresolved_locations} 个事件既没有明确地点，也没有此前位置可沿用，地图门禁不会放行。",
            }
        )
    if low_confidence:
        issues.append(
            {
                "level": "warning",
                "title": "存在低把握结论",
                "detail": f"共有 {low_confidence} 条置信度低于 65%，建议从证据抽屉人工核验。",
            }
        )
    if missing_evidence:
        issues.append(
            {
                "level": "error",
                "title": "发现缺少原文证据的记录",
                "detail": f"共有 {len(missing_evidence)} 条，这些记录不会计入证据覆盖率。",
            }
        )
    if relation_pairs:
        issues.append(
            {
                "level": "warning",
                "title": "部分人物对存在多种关系",
                "detail": f"共有 {len(relation_pairs)} 组人物对包含至少 3 种关系，可能是关系变化，也可能需要纠错。",
            }
        )
    if contradictions:
        issues.append(
            {
                "level": "error",
                "title": "全书整理发现可能冲突",
                "detail": f"共有 {contradictions} 组尚未裁决。自动处理会隔离冲突，人工可标记为情境差异或误报。",
            }
        )
    if time_conflicts:
        issues.append(
            {
                "level": "error",
                "title": "时间约束存在冲突",
                "detail": f"共有 {time_conflicts} 条约束会形成循环。自动处理会舍弃约束但保留剧情事件。",
            }
        )
    coverage = round(evidenced_facts / total_facts * 100, 1) if total_facts else None
    benchmark_total = int(benchmark["total"] or 0)
    benchmark_passed = int(benchmark["passed"] or 0)
    benchmark_score = round(benchmark_passed / benchmark_total * 100, 2) if benchmark_total else None
    critical_failed = int(benchmark["critical_failed"] or 0)
    structural_gate_passed = bool(
        not analysis_complete
        or (
            explicit_location_density_passed
            and (int(place_count) < 2 or int(map_topology_edges or 0) > 0)
            and pending_connectivity == 0
            and ambiguous_connectivity == 0
            and review_connected + confirmed_isolated == relationship_entities
            and (not location_gate_required or unresolved_locations == 0)
        )
    )
    evidence_gate_passed = total_facts == 0 or evidenced_facts == total_facts
    accuracy_gate_passed = bool(
        benchmark_total >= 20 and benchmark_score is not None and benchmark_score >= 95 and critical_failed == 0
    )
    if analysis_complete and accuracy_gate_required and benchmark_total < 20:
        issues.append(
            {
                "level": "error",
                "title": "真实分析尚未建立准确率金标准",
                "detail": f"当前只有 {benchmark_total} 条人工金标准，至少需要 20 条才能计算并承诺 95% 准确率。结构检查通过不等于内容准确率已经校准。",
            }
        )
    elif analysis_complete and accuracy_gate_required and not accuracy_gate_passed:
        issues.append(
            {
                "level": "error",
                "title": "准确率门禁未通过",
                "detail": f"当前金标准准确率为 {benchmark_score or 0}%，主体错误 {critical_failed} 条。最终质量门禁要求准确率至少 95% 且主体错误为 0。",
            }
        )
    conflict_gate_passed = bool(
        unresolved_merges == 0 and contradictions == 0 and time_conflicts == 0
    )
    quality_gate_passed = bool(
        analysis_complete
        and structural_gate_passed
        and evidence_gate_passed
        and conflict_gate_passed
        and (not accuracy_gate_required or accuracy_gate_passed)
    )
    return {
        "segments_total": total_segments,
        "segments_processed": int(processed_segments),
        "facts_total": total_facts,
        "facts_with_evidence": evidenced_facts,
        "evidence_coverage_percent": coverage,
        "unknown_time_events": int(unknown_time),
        "events_with_location": located_events,
        "event_location_coverage_percent": location_coverage,
        "relationship_entities": relationship_entities,
        "connected_relationship_entities": connected_relationship_entities,
        "relationship_coverage_percent": relationship_coverage,
        "connectivity_reviewed_connected": review_connected,
        "connectivity_confirmed_isolated": confirmed_isolated,
        "connectivity_ambiguous": ambiguous_connectivity,
        "connectivity_pending": pending_connectivity,
        "map_topology_edges": int(map_topology_edges or 0),
        "location_explicit_events": int(location_reviews["explicit"] or 0),
        "location_inherited_events": inherited_locations,
        "location_unresolved_events": unresolved_locations,
        "effective_location_coverage_percent": round(
            (int(location_reviews["explicit"] or 0) + inherited_locations) / total_events * 100,
            1,
        ) if total_events else None,
        "low_confidence_facts": int(low_confidence),
        "unresolved_merges": int(unresolved_merges),
        "possible_relation_conflicts": len(relation_pairs),
        "global_contradictions": int(contradictions),
        "time_constraint_conflicts": int(time_conflicts),
        "benchmark_cases": benchmark_total,
        "benchmark_passed": benchmark_passed,
        "benchmark_pending": int(benchmark["pending"] or 0),
        "benchmark_accuracy_percent": benchmark_score,
        "critical_subject_failures": critical_failed,
        "accuracy_gate_passed": accuracy_gate_passed,
        "accuracy_gate_required": accuracy_gate_required,
        "conflict_gate_passed": conflict_gate_passed,
        "accuracy_measurement_status": "measured" if benchmark_total >= 20 else "not_calibrated",
        "evidence_gate_passed": evidence_gate_passed,
        "structural_gate_passed": structural_gate_passed,
        "quality_gate_passed": quality_gate_passed,
        "structural_guarantees": {
            "source_segment_accounting_percent": round(processed_segments / total_segments * 100, 1)
            if total_segments else None,
            "saved_fact_evidence_percent": coverage,
            "destructive_identity_merges": 0,
            "cyclic_time_edges_applied": 0,
        },
        "issues": issues,
    }
