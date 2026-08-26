"""可回滚身份簇、无环时间排序和专用行程层。"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any


GENERIC_IDENTITY_NAMES = {
    "师父", "师傅", "行者", "长老", "大王", "国王", "公主", "太子", "将军",
    "菩萨", "妖怪", "妖精", "老者", "和尚", "道士", "陛下", "娘娘", "土地",
}


def identity_key(value: str) -> str:
    """生成只用于身份比较的名称键。"""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s·・—_\-，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]《》<>]+", "", normalized)


def ensure_identity_clusters(connection: sqlite3.Connection, book_id: int) -> None:
    """为尚未进入身份层的原始实体建立单体簇。"""

    entities = connection.execute(
        """
        SELECT e.* FROM entities e
        WHERE e.book_id = ? AND NOT EXISTS (
            SELECT 1 FROM identity_cluster_members m WHERE m.entity_id = e.id
        )
        ORDER BY e.importance DESC, e.first_segment, e.id
        """,
        (book_id,),
    ).fetchall()
    for entity in entities:
        cursor = connection.execute(
            """
            INSERT INTO identity_clusters(
                book_id, kind, canonical_entity_id, canonical_name, confidence, locked_subject
            ) VALUES (?, ?, ?, ?, 1, ?)
            """,
            (
                book_id,
                entity["kind"],
                entity["id"],
                entity["name"],
                int(entity["kind"] == "person" and float(entity["importance"]) >= 0.9),
            ),
        )
        cursor = connection.execute(
            """
            INSERT INTO identity_cluster_members(cluster_id, entity_id, source, confidence)
            VALUES (?, ?, 'singleton', 1)
            """,
            (int(cursor.lastrowid), entity["id"]),
        )


def cluster_for_entity(connection: sqlite3.Connection, entity_id: int) -> int | None:
    """返回实体当前所属的有效身份簇。"""

    row = connection.execute(
        """
        SELECT m.cluster_id FROM identity_cluster_members m
        JOIN identity_clusters c ON c.id = m.cluster_id
        WHERE m.entity_id = ? AND c.status = 'active'
        """,
        (entity_id,),
    ).fetchone()
    return int(row["cluster_id"]) if row is not None else None


def canonical_entity_map(connection: sqlite3.Connection, book_id: int) -> dict[int, int]:
    """生成原始实体到规范展示实体的映射。"""

    ensure_identity_clusters(connection, book_id)
    rows = connection.execute(
        """
        SELECT m.entity_id, c.canonical_entity_id
        FROM identity_cluster_members m
        JOIN identity_clusters c ON c.id = m.cluster_id
        WHERE c.book_id = ? AND c.status = 'active'
        """,
        (book_id,),
    ).fetchall()
    return {int(row["entity_id"]): int(row["canonical_entity_id"]) for row in rows if row["canonical_entity_id"]}


def _aliases(connection: sqlite3.Connection, entity_id: int) -> set[str]:
    values = connection.execute("SELECT alias FROM aliases WHERE entity_id = ?", (entity_id,)).fetchall()
    return {identity_key(str(row["alias"])) for row in values if identity_key(str(row["alias"]))}


def _identity_conflicts(
    connection: sqlite3.Connection,
    left: sqlite3.Row,
    right: sqlite3.Row,
) -> list[str]:
    """查找足以阻止自动合并的同时在场和类别冲突。"""

    conflicts: list[str] = []
    if left["kind"] != right["kind"]:
        conflicts.append("实体类别不同")
    cooccurrence = connection.execute(
        """
        SELECT e.title, lp.role AS left_role, rp.role AS right_role
        FROM event_participants lp
        JOIN event_participants rp ON rp.event_id = lp.event_id AND rp.entity_id != lp.entity_id
        JOIN events e ON e.id = lp.event_id
        WHERE lp.entity_id = ? AND rp.entity_id = ? AND lp.role != rp.role
        LIMIT 3
        """,
        (left["id"], right["id"]),
    ).fetchall()
    conflicts.extend(
        f"事件《{row['title']}》把二者记为不同角色：{row['left_role']}、{row['right_role']}"
        for row in cooccurrence
    )
    direct_relation = connection.execute(
        """
        SELECT predicate FROM claims
        WHERE (source_entity_id = ? AND target_entity_id = ?)
           OR (source_entity_id = ? AND target_entity_id = ?)
        LIMIT 3
        """,
        (left["id"], right["id"], right["id"], left["id"]),
    ).fetchall()
    for row in direct_relation:
        predicate = str(row["predicate"])
        if not any(marker in predicate for marker in ("别名", "又名", "化名", "身份")):
            conflicts.append(f"原文关系把二者作为两个端点：{predicate}")
    return conflicts


def merge_identity_clusters(
    connection: sqlite3.Connection,
    book_id: int,
    keep_entity_id: int,
    other_entity_id: int,
    reason: str,
    confidence: float,
    created_by: str = "system",
    evidence: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> int:
    """合并展示身份但保留全部原始实体，返回可撤销决策编号。"""

    ensure_identity_clusters(connection, book_id)
    left_cluster = cluster_for_entity(connection, keep_entity_id)
    right_cluster = cluster_for_entity(connection, other_entity_id)
    if left_cluster is None or right_cluster is None:
        raise ValueError("人物身份簇不完整。")
    if left_cluster == right_cluster:
        existing = connection.execute(
            """
            SELECT id FROM identity_decisions
            WHERE book_id = ? AND verdict = 'merge' AND undone_at IS NULL
              AND ((left_entity_id = ? AND right_entity_id = ?)
                OR (left_entity_id = ? AND right_entity_id = ?))
            ORDER BY id DESC LIMIT 1
            """,
            (book_id, keep_entity_id, other_entity_id, other_entity_id, keep_entity_id),
        ).fetchone()
        return int(existing["id"]) if existing is not None else 0
    left = connection.execute("SELECT * FROM identity_clusters WHERE id = ?", (left_cluster,)).fetchone()
    right = connection.execute("SELECT * FROM identity_clusters WHERE id = ?", (right_cluster,)).fetchone()
    if left is None or right is None or left["kind"] != right["kind"]:
        raise ValueError("不同类别的实体不能归入同一身份。")
    moved = [
        int(row["entity_id"])
        for row in connection.execute(
            "SELECT entity_id FROM identity_cluster_members WHERE cluster_id = ? ORDER BY entity_id",
            (right_cluster,),
        ).fetchall()
    ]
    decision_cursor = connection.execute(
        """
        INSERT INTO identity_decisions(
            book_id, left_entity_id, right_entity_id, verdict, confidence, reason,
            evidence_json, contradictions_json, left_cluster_id, right_cluster_id,
            moved_entity_ids_json, created_by
        ) VALUES (?, ?, ?, 'merge', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            book_id, keep_entity_id, other_entity_id, confidence, reason,
            json.dumps(evidence or [], ensure_ascii=False),
            json.dumps(contradictions or [], ensure_ascii=False),
            left_cluster, right_cluster, json.dumps(moved), created_by,
        ),
    )
    decision_id = int(decision_cursor.lastrowid)
    connection.execute(
        """
        UPDATE identity_cluster_members SET cluster_id = ?, source = ?, confidence = ?, decision_id = ?
        WHERE cluster_id = ?
        """,
        (left_cluster, created_by, confidence, decision_id, right_cluster),
    )
    connection.execute(
        """
        UPDATE identity_clusters SET confidence = MIN(confidence, ?), updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (confidence, left_cluster),
    )
    connection.execute(
        """
        UPDATE identity_clusters SET status = 'merged', merged_into_cluster_id = ?,
            updated_at = CURRENT_TIMESTAMP WHERE id = ?
        """,
        (left_cluster, right_cluster),
    )
    return decision_id


def undo_identity_decision(connection: sqlite3.Connection, decision_id: int) -> None:
    """撤销最近一次身份合并，原始实体和引用始终保持不变。"""

    decision = connection.execute(
        "SELECT * FROM identity_decisions WHERE id = ? AND verdict = 'merge' AND undone_at IS NULL",
        (decision_id,),
    ).fetchone()
    if decision is None:
        raise ValueError("找不到可撤销的身份决策。")
    moved = [int(value) for value in json.loads(str(decision["moved_entity_ids_json"]))]
    if not moved:
        raise ValueError("这条身份决策没有可恢复成员。")
    placeholders = ",".join("?" for _ in moved)
    later = connection.execute(
        f"""
        SELECT 1 FROM identity_cluster_members
        WHERE entity_id IN ({placeholders}) AND decision_id != ? LIMIT 1
        """,  # noqa: S608
        (*moved, decision_id),
    ).fetchone()
    if later is not None:
        raise ValueError("该身份之后又参与了合并，请先撤销更晚的决策。")
    connection.execute(
        f"""
        UPDATE identity_cluster_members SET cluster_id = ?, source = 'singleton',
            confidence = 1, decision_id = NULL WHERE entity_id IN ({placeholders})
        """,  # noqa: S608
        (decision["right_cluster_id"], *moved),
    )
    connection.execute(
        """
        UPDATE identity_clusters SET status = 'active', merged_into_cluster_id = NULL,
            updated_at = CURRENT_TIMESTAMP WHERE id = ?
        """,
        (decision["right_cluster_id"],),
    )
    connection.execute(
        "UPDATE identity_decisions SET undone_at = CURRENT_TIMESTAMP WHERE id = ?",
        (decision_id,),
    )


def auto_resolve_identity_candidates(connection: sqlite3.Connection, book_id: int) -> dict[str, int]:
    """自动裁决明确候选，把模糊候选留作可选复核。"""

    ensure_identity_clusters(connection, book_id)
    counts = {"merged": 0, "separate": 0, "needs_review": 0}
    candidates = connection.execute(
        """
        SELECT MIN(m.id) AS id, m.book_id, m.left_entity_id, m.right_entity_id,
            GROUP_CONCAT(DISTINCT m.reason) AS reason, MAX(m.confidence) AS confidence,
            l.name AS left_name, l.kind AS left_kind, l.summary AS left_summary,
            r.name AS right_name, r.kind AS right_kind, r.summary AS right_summary
        FROM entity_merge_candidates m
        JOIN entities l ON l.id = m.left_entity_id
        JOIN entities r ON r.id = m.right_entity_id
        WHERE m.book_id = ? AND m.status = 'unreviewed'
        GROUP BY m.book_id, m.left_entity_id, m.right_entity_id,
            l.name, l.kind, l.summary, r.name, r.kind, r.summary
        ORDER BY MAX(m.confidence) DESC, MIN(m.id)
        """,
        (book_id,),
    ).fetchall()
    for candidate in candidates:
        left = connection.execute("SELECT * FROM entities WHERE id = ?", (candidate["left_entity_id"],)).fetchone()
        right = connection.execute("SELECT * FROM entities WHERE id = ?", (candidate["right_entity_id"],)).fetchone()
        if left is None or right is None:
            continue
        conflicts = _identity_conflicts(connection, left, right)
        left_key = identity_key(str(left["name"]))
        right_key = identity_key(str(right["name"]))
        left_aliases = _aliases(connection, int(left["id"]))
        right_aliases = _aliases(connection, int(right["id"]))
        shared_aliases = (left_aliases & right_aliases) - {identity_key(value) for value in GENERIC_IDENTITY_NAMES}
        explicit_alias = left_key in right_aliases or right_key in left_aliases
        exact_name = bool(left_key and left_key == right_key)
        summary_similarity = SequenceMatcher(
            None,
            identity_key(str(left["summary"])),
            identity_key(str(right["summary"])),
        ).ratio()
        evidence = [str(candidate["reason"])]
        if exact_name:
            evidence.append("规范名称归一后完全相同")
        if explicit_alias:
            evidence.append("一方规范名被另一方明确列为别名")
        if shared_aliases:
            evidence.append("共享非通用别名：" + "、".join(sorted(shared_aliases)))

        if conflicts:
            verdict = "separate"
            confidence = 0.995
        elif exact_name or explicit_alias or shared_aliases:
            verdict = "merge"
            confidence = 0.995 if exact_name or explicit_alias else 0.97
        elif float(candidate["confidence"]) >= 0.985 and summary_similarity >= 0.86:
            verdict = "merge"
            confidence = min(0.99, float(candidate["confidence"]))
            evidence.append("名称建议和人物说明同时高度一致")
        else:
            verdict = "needs_review"
            confidence = max(0.5, float(candidate["confidence"]))

        if verdict == "merge":
            left_rank = (float(left["importance"]), -int(left["first_segment"]), -int(left["id"]))
            right_rank = (float(right["importance"]), -int(right["first_segment"]), -int(right["id"]))
            keep, other = (left, right) if left_rank >= right_rank else (right, left)
            merge_identity_clusters(
                connection,
                book_id,
                int(keep["id"]),
                int(other["id"]),
                "；".join(evidence),
                confidence,
                evidence=evidence,
            )
            connection.execute(
                """
                UPDATE entity_merge_candidates SET status = 'auto_merged',
                    resolution_reason = ?, resolved_by = 'system', resolved_at = CURRENT_TIMESTAMP
                WHERE book_id = ? AND left_entity_id = ? AND right_entity_id = ?
                  AND status = 'unreviewed'
                """,
                (
                    "；".join(evidence), book_id,
                    candidate["left_entity_id"], candidate["right_entity_id"],
                ),
            )
            counts["merged"] += 1
        else:
            connection.execute(
                """
                INSERT INTO identity_decisions(
                    book_id, left_entity_id, right_entity_id, verdict, confidence, reason,
                    evidence_json, contradictions_json, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'system')
                """,
                (
                    book_id, left["id"], right["id"], verdict, confidence,
                    "存在主体冲突，自动分开" if conflicts else "证据不足，保留两个原始身份",
                    json.dumps(evidence, ensure_ascii=False),
                    json.dumps(conflicts, ensure_ascii=False),
                ),
            )
            connection.execute(
                """
                UPDATE entity_merge_candidates SET status = ?, resolution_reason = ?,
                    resolved_by = CASE WHEN ? = 'needs_review' THEN '' ELSE 'system' END,
                    resolved_at = CASE WHEN ? = 'needs_review' THEN NULL ELSE CURRENT_TIMESTAMP END
                WHERE book_id = ? AND left_entity_id = ? AND right_entity_id = ?
                  AND status = 'unreviewed'
                """,
                (
                    "auto_separate" if verdict == "separate" else "needs_review",
                    "存在主体冲突，自动保持分离" if conflicts else "证据不足，等待自动保守处理或人工确认",
                    "auto_separate" if verdict == "separate" else "needs_review",
                    "auto_separate" if verdict == "separate" else "needs_review",
                    book_id, candidate["left_entity_id"], candidate["right_entity_id"],
                ),
            )
            counts[verdict] += 1
    return counts


def conservatively_close_conflicts(connection: sqlite3.Connection, book_id: int) -> dict[str, int]:
    """用无损规则关闭剩余冲突，保留原记录和后续人工改判入口。"""

    identity_counts = auto_resolve_identity_candidates(connection, book_id)
    pending_identity_pairs = connection.execute(
        """
        SELECT MIN(id) AS id, left_entity_id, right_entity_id, MAX(confidence) AS confidence,
            GROUP_CONCAT(DISTINCT reason) AS reasons
        FROM entity_merge_candidates
        WHERE book_id = ? AND status = 'needs_review'
        GROUP BY left_entity_id, right_entity_id
        """,
        (book_id,),
    ).fetchall()
    for candidate in pending_identity_pairs:
        reason = "证据不足，自动保持为两个身份；原始候选和证据已保留，可随时人工改为合并。"
        connection.execute(
            """
            INSERT INTO identity_decisions(
                book_id, left_entity_id, right_entity_id, verdict, confidence,
                reason, evidence_json, contradictions_json, created_by
            ) VALUES (?, ?, ?, 'separate', ?, ?, ?, '[]', 'system')
            """,
            (
                book_id,
                candidate["left_entity_id"],
                candidate["right_entity_id"],
                max(0.5, float(candidate["confidence"])),
                reason,
                json.dumps([str(candidate["reasons"] or "")], ensure_ascii=False),
            ),
        )
        connection.execute(
            """
            UPDATE entity_merge_candidates
            SET status = 'auto_separate', resolution_reason = ?, resolved_by = 'system',
                resolved_at = CURRENT_TIMESTAMP
            WHERE book_id = ? AND left_entity_id = ? AND right_entity_id = ?
              AND status = 'needs_review'
            """,
            (reason, book_id, candidate["left_entity_id"], candidate["right_entity_id"]),
        )

    contradiction_reason = (
        "自动隔离可能冲突的两条记录，避免未经证实地删除任何一方；用户可结合原文改判为情境差异或误报。"
    )
    contradiction_cursor = connection.execute(
        """
        UPDATE contradictions
        SET status = 'auto_quarantined', resolution_reason = ?, resolved_by = 'system',
            resolved_at = CURRENT_TIMESTAMP
        WHERE book_id = ? AND status = 'unreviewed'
        """,
        (contradiction_reason, book_id),
    )
    time_reason = "该约束会形成循环或端点无效，自动舍弃约束但保留两件剧情事件和原文证据。"
    time_cursor = connection.execute(
        """
        UPDATE event_order_edges
        SET status = 'auto_rejected', resolution_reason = ?, resolved_by = 'system',
            resolved_at = CURRENT_TIMESTAMP
        WHERE book_id = ? AND status = 'conflict'
        """,
        (time_reason, book_id),
    )
    chronology = recompute_chronology_dag(connection, book_id)
    return {
        "identity_merged": int(identity_counts["merged"]),
        "identity_separated": int(identity_counts["separate"]) + len(pending_identity_pairs),
        "contradictions_quarantined": max(0, int(contradiction_cursor.rowcount)),
        "time_constraints_rejected": max(0, int(time_cursor.rowcount)),
        "remaining_time_conflicts": int(chronology["conflict_edges"]),
    }


def recompute_chronology_dag(connection: sqlite3.Connection, book_id: int) -> dict[str, int]:
    """只接受不会成环的时间约束，再以原文出现顺序稳定排序。"""

    events = connection.execute(
        "SELECT id, narrative_order FROM events WHERE book_id = ? ORDER BY narrative_order, id",
        (book_id,),
    ).fetchall()
    event_ids = {int(row["id"]) for row in events}
    adjacency: dict[int, set[int]] = defaultdict(set)

    def path_exists(start: int, target: int) -> bool:
        stack = [start]
        visited: set[int] = set()
        while stack:
            node = stack.pop()
            if node == target:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(adjacency.get(node, ()))
        return False

    inactive_statuses = ("rejected", "quarantined", "auto_rejected")
    connection.execute(
        """
        UPDATE event_order_edges SET status = 'pending', reason = ''
        WHERE book_id = ? AND status NOT IN (?, ?, ?)
        """,
        (book_id, *inactive_statuses),
    )
    edges = connection.execute(
        """
        SELECT * FROM event_order_edges
        WHERE book_id = ? AND status NOT IN (?, ?, ?)
        ORDER BY confidence DESC, CASE created_by WHEN 'human' THEN 0 WHEN 'source' THEN 1 ELSE 2 END, id
        """,
        (book_id, *inactive_statuses),
    ).fetchall()
    accepted = 0
    conflicts = 0
    for edge in edges:
        earlier = int(edge["earlier_event_id"])
        later = int(edge["later_event_id"])
        if earlier not in event_ids or later not in event_ids or earlier == later:
            connection.execute(
                "UPDATE event_order_edges SET status = 'conflict', reason = '约束端点无效' WHERE id = ?",
                (edge["id"],),
            )
            conflicts += 1
            continue
        if path_exists(later, earlier):
            connection.execute(
                "UPDATE event_order_edges SET status = 'conflict', reason = '加入后会形成时间循环' WHERE id = ?",
                (edge["id"],),
            )
            conflicts += 1
            continue
        adjacency[earlier].add(later)
        connection.execute(
            "UPDATE event_order_edges SET status = 'accepted', reason = '无环时间约束' WHERE id = ?",
            (edge["id"],),
        )
        accepted += 1

    narrative_rank = {int(row["id"]): (int(row["narrative_order"]), int(row["id"])) for row in events}
    indegree = {event_id: 0 for event_id in event_ids}
    for targets in adjacency.values():
        for target in targets:
            indegree[target] += 1
    ready = sorted((event_id for event_id, degree in indegree.items() if degree == 0), key=narrative_rank.get)
    ordered: list[int] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(adjacency.get(current, ()), key=narrative_rank.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=narrative_rank.get)
    if len(ordered) != len(event_ids):
        missing = sorted(event_ids - set(ordered), key=narrative_rank.get)
        ordered.extend(missing)
    for rank, event_id in enumerate(ordered):
        connection.execute("UPDATE events SET story_order = ? WHERE id = ?", (float(rank), event_id))
    return {"events": len(events), "accepted_edges": accepted, "conflict_edges": conflicts}


def select_main_subject(connection: sqlite3.Connection, book_id: int) -> int | None:
    """从人工设置或全书参与度中选择稳定的主线人物。"""

    ensure_identity_clusters(connection, book_id)
    configured = connection.execute(
        "SELECT protagonist_entity_id, auto_protagonist FROM book_settings WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    if configured is not None and not bool(configured["auto_protagonist"]) and configured["protagonist_entity_id"]:
        return int(configured["protagonist_entity_id"])
    row = connection.execute(
        """
        SELECT c.canonical_entity_id,
            COUNT(DISTINCT ep.event_id) * 5 + SUM(e.importance) * 2
              + MAX(0, 30 - MIN(e.first_segment)) AS score
        FROM identity_clusters c
        JOIN identity_cluster_members m ON m.cluster_id = c.id
        JOIN entities e ON e.id = m.entity_id
        LEFT JOIN event_participants ep ON ep.entity_id = e.id
        WHERE c.book_id = ? AND c.status = 'active' AND c.kind = 'person'
        GROUP BY c.id ORDER BY score DESC, MIN(e.first_segment), c.id LIMIT 1
        """,
        (book_id,),
    ).fetchone()
    return int(row["canonical_entity_id"]) if row is not None and row["canonical_entity_id"] else None


def rebuild_derived_journey(connection: sqlite3.Connection, book_id: int) -> dict[str, int]:
    """按规范人物身份和故事顺序重建连续行程，缺口会明确保留。"""

    ensure_identity_clusters(connection, book_id)
    subject = select_main_subject(connection, book_id)
    connection.execute("DELETE FROM journey_legs WHERE book_id = ? AND created_by = 'derived'", (book_id,))
    if subject is None:
        return {"subject_entity_id": 0, "legs": 0, "gaps": 0}
    cluster_id = cluster_for_entity(connection, subject)
    if cluster_id is None:
        return {"subject_entity_id": subject, "legs": 0, "gaps": 0}
    events = connection.execute(
        """
        SELECT DISTINCT e.id, e.title, e.summary, e.story_order, e.narrative_order,
            e.location_entity_id, e.transport, e.first_segment
        FROM events e
        JOIN event_participants ep ON ep.event_id = e.id
        JOIN identity_cluster_members m ON m.entity_id = ep.entity_id
        WHERE e.book_id = ? AND m.cluster_id = ?
        ORDER BY e.story_order, e.narrative_order, e.id
        """,
        (book_id, cluster_id),
    ).fetchall()
    located = [row for row in events if row["location_entity_id"] is not None]
    if not located:
        return {"subject_entity_id": subject, "legs": 0, "gaps": len(events)}
    inserted = 0
    gaps = 0
    previous: sqlite3.Row | None = None
    for ordinal, event in enumerate(located):
        # 第一处已知地点只负责放置人物，不凭空制造一条“地点到自身”的移动路线。
        if previous is None:
            previous = event
            continue
        from_id = int(previous["location_entity_id"])
        to_id = int(event["location_entity_id"])
        # 连续剧情留在同一地点时属于驻留，不应伪装成交通路线。
        if from_id == to_id:
            previous = event
            continue
        intervening = 0
        intervening = sum(
            1
            for raw in events
            if float(previous["story_order"]) < float(raw["story_order"]) < float(event["story_order"])
            and raw["location_entity_id"] is None
        )
        gap_status = "unknown_path" if intervening else "complete"
        if gap_status != "complete":
            gaps += 1
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO journey_legs(
                book_id, subject_entity_id, from_entity_id, to_entity_id, event_id,
                ordinal, transport, summary, gap_status, confidence, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'derived')
            """,
            (
                book_id, subject, from_id, to_id, event["id"], ordinal,
                event["transport"] or "未说明",
                event["summary"], gap_status, 0.95 if not intervening else 0.55,
                event["first_segment"],
            ),
        )
        if cursor.rowcount:
            leg_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence(
                    book_id, target_type, target_id, segment_id, quote, quote_start, quote_end
                )
                SELECT book_id, 'journey_leg', ?, segment_id, quote, quote_start, quote_end
                FROM evidence WHERE target_type = 'event' AND target_id = ?
                """,
                (leg_id, event["id"]),
            )
        inserted += 1
        previous = event
    return {"subject_entity_id": subject, "legs": inserted, "gaps": gaps}


def repair_explicit_kinship(connection: sqlite3.Connection, book_id: int) -> int:
    """把“某人的父亲”等明确命名恢复成有证据的亲属连线。"""

    people = connection.execute(
        "SELECT id, name, summary, first_segment FROM entities WHERE book_id = ? AND kind = 'person'",
        (book_id,),
    ).fetchall()
    inserted = 0
    pattern = re.compile(r"^(.{1,40})的(父亲|母亲|哥哥|姐姐|弟弟|妹妹|儿子|女儿)$")
    for relative in people:
        matched = pattern.match(str(relative["name"]))
        if matched is None:
            continue
        person_name, predicate = matched.groups()
        targets = connection.execute(
            """
            SELECT DISTINCT e.id FROM entities e
            LEFT JOIN aliases a ON a.entity_id = e.id
            WHERE e.book_id = ? AND e.kind = 'person' AND (e.name = ? OR a.alias = ?)
            """,
            (book_id, person_name, person_name),
        ).fetchall()
        if len(targets) != 1 or int(targets[0]["id"]) == int(relative["id"]):
            continue
        target_id = int(targets[0]["id"])
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO claims(
                book_id, source_entity_id, target_entity_id, predicate, summary,
                confidence, status, first_segment, created_by
            ) VALUES (?, ?, ?, ?, ?, 0.98, 'accepted', ?, 'derived')
            """,
            (
                book_id, int(relative["id"]), target_id, predicate,
                str(relative["summary"]), int(relative["first_segment"]),
            ),
        )
        claim = connection.execute(
            """
            SELECT id FROM claims WHERE book_id = ? AND source_entity_id = ?
              AND target_entity_id = ? AND predicate = ? AND first_segment = ?
            """,
            (book_id, int(relative["id"]), target_id, predicate, int(relative["first_segment"])),
        ).fetchone()
        if claim is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO evidence(
                    book_id, target_type, target_id, segment_id, quote, quote_start, quote_end
                )
                SELECT book_id, 'claim', ?, segment_id, quote, quote_start, quote_end
                FROM evidence WHERE target_type = 'entity' AND target_id = ?
                """,
                (int(claim["id"]), int(relative["id"])),
            )
        if cursor.rowcount:
            inserted += 1
    return inserted


def backfill_event_locations(connection: sqlite3.Connection, book_id: int) -> int:
    """事件标题、摘要或逐字证据只指向一个已知地点时，补回该地点。"""

    place_rows = connection.execute(
        """
        SELECT e.id, e.name, a.alias FROM entities e
        LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? AND e.kind = 'place'
        """,
        (book_id,),
    ).fetchall()
    names_by_id: dict[int, set[str]] = defaultdict(set)
    for row in place_rows:
        names_by_id[int(row["id"])].add(str(row["name"]))
        if row["alias"]:
            names_by_id[int(row["id"])].add(str(row["alias"]))
    events = connection.execute(
        """
        SELECT e.id, e.title, e.summary,
            GROUP_CONCAT(x.quote, ' ') AS quotes
        FROM events e LEFT JOIN evidence x
          ON x.target_type = 'event' AND x.target_id = e.id
        WHERE e.book_id = ? AND e.location_entity_id IS NULL
        GROUP BY e.id
        """,
        (book_id,),
    ).fetchall()
    repaired = 0
    for event in events:
        searchable = " ".join((str(event["title"]), str(event["summary"]), str(event["quotes"] or "")))
        normalized_searchable = searchable.replace("的", "")
        matches = {
            place_id
            for place_id, names in names_by_id.items()
            if any(
                len(name) >= 2
                and (
                    name in searchable
                    or (len(name.replace("的", "")) >= 3 and name.replace("的", "") in normalized_searchable)
                )
                for name in names
            )
        }
        if len(matches) != 1:
            continue
        connection.execute(
            "UPDATE events SET location_entity_id = ? WHERE id = ? AND location_entity_id IS NULL",
            (next(iter(matches)), int(event["id"])),
        )
        repaired += 1
    return repaired


def recover_explicit_scene_locations(connection: sqlite3.Connection, book_id: int) -> int:
    """从事件逐字证据里的明确场景短语恢复地点实体和事件位置。"""

    from app.pipeline import upsert_entity

    suffixes = (
        "河边洗衣场|旧高铁转运站|师团长办公室|废弃足球场|军团前进阵地|"
        "办公室|管制室|淋浴间|驾驶舱|队长房间|房间|卧室|食堂|露台|"
        "国军本部|本部|基地|队舍|战场|阵地|废弃都市|都市废墟|都市|"
        "教堂|集市|转运站|自宅|户外|走廊|街道|玻璃屋|宫|区|领域"
    )
    scene_pattern = re.compile(
        rf"(?:在|来到|抵达|前往|返回|进入|逃到|走到|离开|位于)"
        rf"([\u4e00-\u9fff·]{{1,14}}?(?:{suffixes}))"
    )
    events = connection.execute(
        """
        SELECT e.id, e.title, e.first_segment, x.segment_id, x.quote
        FROM events e JOIN evidence x ON x.target_type = 'event' AND x.target_id = e.id
        WHERE e.book_id = ? AND e.location_entity_id IS NULL
        ORDER BY e.story_order, e.id, x.id
        """,
        (book_id,),
    ).fetchall()
    repaired_events: set[int] = set()

    def normalize_scene_name(raw_name: str) -> str | None:
        """清理句法残片，只保留能够作为地点名称展示的短语。"""

        name = re.sub(r"^(?:自己|他的|她的|他们的|共和国的|了)", "", raw_name).strip()
        # 描述性定语容易被正则一并吞入。对明确的场景通名收束到通名，避免生成整句地点。
        generic_suffixes = (
            "师团长办公室", "旧高铁转运站", "废弃足球场", "军团前进阵地",
            "办公室", "管制室", "淋浴间", "驾驶舱", "队长房间", "房间", "卧室",
            "食堂", "队舍", "教堂", "基地", "战场", "阵地", "露台", "走廊",
        )
        if "的" in name:
            matched_suffix = next((suffix for suffix in generic_suffixes if name.endswith(suffix)), None)
            if matched_suffix:
                name = matched_suffix
            elif name.endswith("宫"):
                return None
        if not name or len(name) > 14 or name in {"宫", "区", "领域", "都市"}:
            return None
        return name

    for event in events:
        event_id = int(event["id"])
        if event_id in repaired_events:
            continue
        candidates = {match.group(1) for match in scene_pattern.finditer(str(event["quote"]))}
        candidates = {normalize_scene_name(item) for item in candidates}
        candidates.discard(None)
        if len(candidates) != 1:
            continue
        scene_name = next(iter(candidates))
        scene_id = upsert_entity(
            connection, book_id, "place", scene_name,
            f"事件“{event['title']}”的原文明示场景。", 0.62,
            int(event["first_segment"]), [],
        )
        connection.execute(
            "UPDATE events SET location_entity_id = ? WHERE id = ? AND location_entity_id IS NULL",
            (scene_id, event_id),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO evidence(
                book_id, target_type, target_id, segment_id, quote, quote_start, quote_end
            )
            SELECT book_id, 'entity', ?, segment_id, quote, quote_start, quote_end
            FROM evidence WHERE target_type = 'event' AND target_id = ?
            """,
            (scene_id, event_id),
        )
        repaired_events.add(event_id)
    return len(repaired_events)


def consolidate_semantics(connection: sqlite3.Connection, book_id: int) -> dict[str, Any]:
    """运行全部本地、无模型费用且可重复执行的语义整理。"""

    ensure_identity_clusters(connection, book_id)
    identities = auto_resolve_identity_candidates(connection, book_id)
    kinship = repair_explicit_kinship(connection, book_id)
    locations = backfill_event_locations(connection, book_id)
    explicit_scenes = recover_explicit_scene_locations(connection, book_id)
    locations += backfill_event_locations(connection, book_id)
    chronology = recompute_chronology_dag(connection, book_id)
    journey = rebuild_derived_journey(connection, book_id)
    return {
        "identities": identities,
        "kinship_claims": kinship,
        "event_locations": locations,
        "explicit_scene_locations": explicit_scenes,
        "chronology": chronology,
        "journey": journey,
    }
