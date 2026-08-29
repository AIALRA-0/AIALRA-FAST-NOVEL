"""Turn quality findings into plain-language, actionable review tasks."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _upsert(
    connection: sqlite3.Connection,
    book_id: int,
    task_key: str,
    category: str,
    severity: str,
    title: str,
    problem: str,
    impact: str,
    recommendation: str,
    actions: list[dict[str, str]],
    evidence: list[Any],
    rebuild_scope: list[str],
    source_type: str,
    source_id: int | None,
) -> None:
    connection.execute(
        """
        INSERT INTO review_tasks(
            book_id, task_key, category, severity, title, problem, impact, recommendation,
            actions_json, evidence_json, rebuild_scope_json, source_type, source_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(book_id, task_key) DO UPDATE SET
            category = excluded.category, severity = excluded.severity, title = excluded.title,
            problem = excluded.problem, impact = excluded.impact, recommendation = excluded.recommendation,
            actions_json = excluded.actions_json, evidence_json = excluded.evidence_json,
            rebuild_scope_json = excluded.rebuild_scope_json, source_type = excluded.source_type,
            source_id = excluded.source_id, updated_at = CURRENT_TIMESTAMP
        """,
        (
            book_id, task_key, category, severity, title, problem, impact, recommendation,
            json.dumps(actions, ensure_ascii=False), json.dumps(evidence, ensure_ascii=False),
            json.dumps(rebuild_scope, ensure_ascii=False), source_type, source_id,
        ),
    )


def sync_review_tasks(connection: sqlite3.Connection, book_id: int) -> list[dict[str, Any]]:
    """Refresh pending tasks and keep resolved history out of the warning stream."""

    active_keys: set[str] = set()
    for row in connection.execute(
        """
        SELECT candidate.*, left_entity.name AS left_name, right_entity.name AS right_name
        FROM entity_merge_candidates candidate
        JOIN entities left_entity ON left_entity.id = candidate.left_entity_id
        JOIN entities right_entity ON right_entity.id = candidate.right_entity_id
        WHERE candidate.book_id = ? AND candidate.status IN ('unreviewed', 'needs_review')
        """,
        (book_id,),
    ).fetchall():
        key = f"identity:{int(row['id'])}"
        active_keys.add(key)
        _upsert(connection, book_id, key, "identity", "blocks_release", f"确认 {row['left_name']} 与 {row['right_name']} 是否为同一人物", str(row["reason"]), "错误合并会把两个人的身份、关系与行程混在一起；错误拆分会产生重复人物", "先查看原文证据；证据不能唯一确认时保持为两个人", [{"id": "accept_suggestion", "label": "接受系统建议"}, {"id": "keep_separate", "label": "保持为两个人"}, {"id": "defer", "label": "稍后处理"}], [], ["人物索引", "关系图", "地图行程"], "merge_candidate", int(row["id"]))

    for row in connection.execute(
        """
        SELECT review.*, entity.name FROM entity_connectivity_reviews review
        JOIN entities entity ON entity.id = review.entity_id
        WHERE review.book_id = ? AND review.status IN ('pending', 'ambiguous')
        """,
        (book_id,),
    ).fetchall():
        key = f"connectivity:{int(row['id'])}"
        active_keys.add(key)
        _upsert(connection, book_id, key, "relationship", "advisory", f"补查 {row['name']} 的人物关系", str(row["reason"] or "原文出现了人物，但尚未找到能够确认的关系"), "若直接进入正式关系图，会形成没有解释的散点；若强行连线，又可能制造原文不存在的关系", "先自动扫描全部提及；仍无证据时确认其确实独立", [{"id": "accept_suggestion", "label": "自动复核这一个"}, {"id": "keep_separate", "label": "确认独立"}, {"id": "defer", "label": "稍后处理"}], json.loads(str(row["evidence_json"] or "[]")), ["人物关系图"], "connectivity", int(row["id"]))

    for row in connection.execute(
        """
        SELECT review.event_id, review.reason, event.title FROM event_location_reviews review
        JOIN events event ON event.id = review.event_id
        WHERE review.book_id = ? AND review.status IN ('unresolved', 'ambiguous')
        """,
        (book_id,),
    ).fetchall():
        key = f"location:{int(row['event_id'])}"
        active_keys.add(key)
        _upsert(connection, book_id, key, "location", "blocks_release", f"确认事件“{row['title']}”发生在哪里", str(row["reason"] or "当前事件没有可靠地点"), "地点不明会让地图步骤停留在上一个地点，并影响行程连续性", "从原文证据中选择明确地点；没有证据时保留未知", [{"id": "accept_suggestion", "label": "查看候选地点"}, {"id": "keep_separate", "label": "保留地点未知"}, {"id": "defer", "label": "稍后处理"}], [], ["编年", "二维地图", "三维地图"], "event_location", int(row["event_id"]))

    for row in connection.execute(
        """
        SELECT edge.id, earlier.title AS earlier_title, later.title AS later_title
        FROM event_order_edges edge
        JOIN events earlier ON earlier.id = edge.earlier_event_id
        JOIN events later ON later.id = edge.later_event_id
        WHERE edge.book_id = ? AND edge.status = 'conflict'
        """,
        (book_id,),
    ).fetchall():
        key = f"time:{int(row['id'])}"
        active_keys.add(key)
        _upsert(connection, book_id, key, "time", "blocks_release", f"确认“{row['earlier_title']}”与“{row['later_title']}”的先后", "两条原文时间约束互相冲突", "错误顺序会直接改变编年和地图播放次序", "先查看两条证据；区分叙述顺序、回忆时间和真实发生顺序", [{"id": "accept_suggestion", "label": "采用证据更强的顺序"}, {"id": "defer", "label": "保留冲突"}], [], ["编年", "地图播放"], "time_edge", int(row["id"]))

    # Findings that disappeared are not warnings; retain them as automatically closed history.
    pending = connection.execute("SELECT id, task_key FROM review_tasks WHERE book_id = ? AND status = 'pending'", (book_id,)).fetchall()
    for row in pending:
        if str(row["task_key"]) not in active_keys:
            connection.execute("UPDATE review_tasks SET status = 'resolved', resolution = 'source_resolved', resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (int(row["id"]),))
    return list_review_tasks(connection, book_id)


def list_review_tasks(connection: sqlite3.Connection, book_id: int, include_resolved: bool = False) -> list[dict[str, Any]]:
    clause = "" if include_resolved else "AND status = 'pending'"
    rows = connection.execute(
        f"""
        SELECT * FROM review_tasks WHERE book_id = ? {clause}
        ORDER BY CASE severity WHEN 'blocks_analysis' THEN 0 WHEN 'blocks_release' THEN 1 ELSE 2 END,
                 category, updated_at DESC, id DESC
        """,  # noqa: S608
        (book_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for source, target in (("actions_json", "actions"), ("evidence_json", "evidence"), ("rebuild_scope_json", "rebuild_scope")):
            item[target] = json.loads(str(item.pop(source) or "[]"))
        result.append(item)
    return result


def resolve_review_task(connection: sqlite3.Connection, task_id: int, action: str, note: str) -> dict[str, Any]:
    task = connection.execute("SELECT * FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("找不到待处理事项")
    source_type = str(task["source_type"])
    source_id = task["source_id"]
    if source_type == "merge_candidate" and source_id is not None and action == "keep_separate":
        connection.execute("UPDATE entity_merge_candidates SET status = 'rejected', resolution_reason = ?, resolved_by = 'human', resolved_at = CURRENT_TIMESTAMP WHERE id = ?", (note or "人工确认为不同对象", int(source_id)))
    elif source_type == "connectivity" and source_id is not None and action == "keep_separate":
        connection.execute("UPDATE entity_connectivity_reviews SET status = 'confirmed_isolated', reason = ?, review_method = 'human', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (note or "人工确认当前证据下确实独立", int(source_id)))
    elif source_type == "event_location" and source_id is not None and action == "keep_separate":
        connection.execute(
            "UPDATE event_location_reviews SET status = 'confirmed_unknown', effective_location_entity_id = NULL, reason = ?, updated_at = CURRENT_TIMESTAMP WHERE event_id = ?",
            (note or "人工确认原文没有足够证据确定地点", int(source_id)),
        )
    elif action == "accept_suggestion":
        connection.execute(
            "UPDATE review_tasks SET resolution = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (note or "等待执行对应的自动复核或证据比较", task_id),
        )
        return {"id": task_id, "status": "pending", "resolution": note or "等待执行对应的自动复核或证据比较"}
    elif action == "defer":
        connection.execute("UPDATE review_tasks SET resolution = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (note or "暂缓处理", task_id))
        return {"id": task_id, "status": "pending", "resolution": note or "暂缓处理"}
    connection.execute("UPDATE review_tasks SET status = 'resolved', resolution = ?, resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (note or action, task_id))
    return {"id": task_id, "status": "resolved", "resolution": note or action}
