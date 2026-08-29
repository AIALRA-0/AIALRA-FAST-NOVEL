"""跨章节合并实体、重复事实和故事时间顺序。"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher


GENERIC_SHARED_ALIAS_KEYS = {
    "龙王", "龍王", "天王", "大王", "国王", "國王", "王后", "夫人", "公主", "太子",
    "长老", "長老", "师父", "師父", "师兄", "師兄", "师弟", "師弟", "师姐", "師姐",
    "师妹", "師妹", "将军", "將軍", "元帅", "元帥", "菩萨", "菩薩", "佛祖", "妖王",
    "妖怪", "道士", "和尚", "行者", "童子", "仙子", "星君", "真人", "老君", "陛下",
    "妖精", "那怪", "老妖", "怪物", "老怪", "妖魔", "老者", "小妖", "主公", "魔王",
    "老魔", "土地", "公公", "万岁", "萬歲", "老龙王", "老龍王", "老施主", "老官儿",
    "老官兒", "老和尚", "皇帝", "父王", "昏君", "小王子", "小和尚", "妈妈", "媽媽",
    "娘娘", "天尊", "大仙", "君王", "公主娘娘", "先锋", "先鋒", "鬼王", "院主", "郡侯",
    "老道", "老母", "老妪", "老嫗", "老姆", "老妖王", "老儿", "老兒", "老人", "秀才",
    "祖师", "祖師", "真君", "法师", "法師", "殿下", "樵夫", "樵哥", "第二个", "第二個",
    "第三个", "第三個", "老施主", "法王", "王妃", "国丈", "國丈", "老爷", "老爺",
}

WORLD_TITLE_REPLACEMENTS = {
    "下界": "地上",
    "咒语": "咒",
    "延生长寿": "长生",
    "长生不老": "长生",
    "延寿长生": "长生",
}

WORLD_TITLE_SUFFIXES = (
    "的传说",
    "地理特征",
    "分布",
)


def normalized_name(value: str) -> str:
    """生成只用于查重的稳定名称，不改变用户看到的原名。"""

    value = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"[\s·・—_\-，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]《》<>]+", "", value)


def _canonical_world_title(value: str) -> str:
    """归一少量含义稳定的标题写法，用于识别跨章节重复设定。"""

    result = unicodedata.normalize("NFKC", value).strip()
    for source, target in WORLD_TITLE_REPLACEMENTS.items():
        result = result.replace(source, target)
    for suffix in WORLD_TITLE_SUFFIXES:
        if result.endswith(suffix) and len(result) > len(suffix) + 1:
            result = result[: -len(suffix)]
            break
    normalized = normalized_name(result)
    if "紧箍" in normalized and ("咒" in normalized or "约束" in normalized):
        return "紧箍约束机制"
    if "天上一日" in normalized or ("天庭" in normalized and "时间流速" in normalized):
        return "天庭凡间时间流速"
    if "狮驼岭三魔" in normalized and "势力" in normalized:
        return "狮驼岭三魔势力"
    if "天庭" in normalized and "军事力量" in normalized:
        return "天庭军事力量"
    return normalized


def _ngram_similarity(left: str, right: str, size: int = 3) -> float:
    """比较两段说明共享的连续短语，避免只看相似标题就误合并。"""

    left_value = normalized_name(left)
    right_value = normalized_name(right)
    left_parts = {left_value[index:index + size] for index in range(max(0, len(left_value) - size + 1))}
    right_parts = {right_value[index:index + size] for index in range(max(0, len(right_value) - size + 1))}
    if not left_parts or not right_parts:
        return 0.0
    return len(left_parts & right_parts) / len(left_parts | right_parts)


def _same_world_topic(left: sqlite3.Row, right: sqlite3.Row, require_same_origin: bool = True) -> bool:
    """用标题和正文双重条件判断两张世界卡是否讲同一主题。"""

    if left["category"] != right["category"]:
        return False
    if require_same_origin and left["created_by"] != right["created_by"]:
        return False
    left_title = _canonical_world_title(str(left["title"]))
    right_title = _canonical_world_title(str(right["title"]))
    if not left_title or not right_title:
        return False
    if left_title == right_title:
        return True

    shorter, longer = sorted((left_title, right_title), key=len)
    original_longer = str(left["title"] if len(left_title) >= len(right_title) else right["title"])
    if len(shorter) >= 4 and longer.endswith(shorter) and f"的{shorter}" in normalized_name(original_longer):
        return True

    title_similarity = SequenceMatcher(None, left_title, right_title).ratio()
    summary_similarity = _ngram_similarity(str(left["summary"]), str(right["summary"]))
    if not require_same_origin and len(shorter) >= 4 and longer.startswith(shorter) and summary_similarity >= 0.08:
        return True
    return title_similarity >= 0.84 or (title_similarity >= 0.78 and summary_similarity >= 0.08) or (
        title_similarity >= 0.65 and summary_similarity >= 0.22
    ) or (
        title_similarity >= 0.52 and summary_similarity >= 0.28
    )


def _combine_summaries(left: str, right: str) -> str:
    """保留两处证据带来的补充事实，同时避免原句完全重复。"""

    left_value = _deduplicate_summary_sentences(left)
    right_value = _deduplicate_summary_sentences(right)
    normalized_left = normalized_name(left_value)
    normalized_right = normalized_name(right_value)
    if normalized_left in normalized_right:
        return right_value
    if normalized_right in normalized_left:
        return left_value
    return _deduplicate_summary_sentences(f"{left_value.rstrip('。；')}；{right_value}")


def _deduplicate_summary_sentences(value: str) -> str:
    """删除同一说明中的重复句，保留包含更多细节的版本。"""

    sentences = [item.strip() for item in re.split(r"(?<=[。；！？!?])\s*", value.strip()) if item.strip()]
    if len(sentences) < 2:
        return value.strip()
    kept: list[str] = []
    for sentence in sentences:
        sentence_key = normalized_name(sentence)
        if not sentence_key:
            continue
        replacement_index: int | None = None
        duplicate = False
        for index, existing in enumerate(kept):
            existing_key = normalized_name(existing)
            shorter, longer = sorted((sentence_key, existing_key), key=len)
            similarity = SequenceMatcher(None, sentence_key, existing_key).ratio()
            overlap = _ngram_similarity(sentence, existing)
            if (
                sentence_key == existing_key
                or (len(shorter) >= 12 and shorter in longer)
                or similarity >= 0.90
                or overlap >= 0.62
                or (len(shorter) >= 30 and similarity >= 0.60 and overlap >= 0.32)
            ):
                duplicate = True
                if len(sentence_key) > len(existing_key):
                    replacement_index = index
                break
        if replacement_index is not None:
            kept[replacement_index] = sentence
        elif not duplicate:
            kept.append(sentence)
    return "".join(kept)


def _move_evidence(
    connection: sqlite3.Connection,
    target_type: str,
    old_id: int,
    new_id: int,
) -> None:
    """把旧记录的证据复制给保留记录，再移除旧证据。"""

    connection.execute(
        """
        INSERT OR IGNORE INTO evidence(
            book_id, target_type, target_id, segment_id, quote, quote_start, quote_end
        )
        SELECT book_id, target_type, ?, segment_id, quote, quote_start, quote_end
        FROM evidence WHERE target_type = ? AND target_id = ?
        """,
        (new_id, target_type, old_id),
    )
    connection.execute(
        "DELETE FROM evidence WHERE target_type = ? AND target_id = ?",
        (target_type, old_id),
    )


def merge_entities(
    connection: sqlite3.Connection,
    book_id: int,
    keep_entity_id: int,
    remove_entity_id: int,
    reason: str,
) -> int:
    """合并两个同类型实体，并安全迁移关系、事件、别名和证据。"""

    if keep_entity_id == remove_entity_id:
        return keep_entity_id
    keep = connection.execute(
        "SELECT * FROM entities WHERE id = ? AND book_id = ?",
        (keep_entity_id, book_id),
    ).fetchone()
    remove = connection.execute(
        "SELECT * FROM entities WHERE id = ? AND book_id = ?",
        (remove_entity_id, book_id),
    ).fetchone()
    if keep is None or remove is None:
        raise ValueError("待合并实体不存在。")
    if keep["kind"] != remove["kind"]:
        raise ValueError("不同类别的实体不能直接合并。")

    # 关系先逐条迁移，遇到相同关系时合并证据。
    claims = connection.execute(
        "SELECT * FROM claims WHERE source_entity_id = ? OR target_entity_id = ?",
        (remove_entity_id, remove_entity_id),
    ).fetchall()
    for claim in claims:
        source_id = keep_entity_id if claim["source_entity_id"] == remove_entity_id else claim["source_entity_id"]
        target_id = keep_entity_id if claim["target_entity_id"] == remove_entity_id else claim["target_entity_id"]
        duplicate = connection.execute(
            """
            SELECT id FROM claims
            WHERE book_id = ? AND source_entity_id = ? AND target_entity_id = ?
              AND predicate = ? AND first_segment = ? AND id != ?
            """,
            (book_id, source_id, target_id, claim["predicate"], claim["first_segment"], claim["id"]),
        ).fetchone()
        if duplicate is not None:
            _move_evidence(connection, "claim", int(claim["id"]), int(duplicate["id"]))
            connection.execute("DELETE FROM claims WHERE id = ?", (claim["id"],))
        else:
            connection.execute(
                "UPDATE claims SET source_entity_id = ?, target_entity_id = ? WHERE id = ?",
                (source_id, target_id, claim["id"]),
            )

    # 地点实体合并时，方位关系也要迁移；否则外键会阻止删除旧地点。
    place_relations = connection.execute(
        "SELECT * FROM place_relations WHERE source_entity_id = ? OR target_entity_id = ?",
        (remove_entity_id, remove_entity_id),
    ).fetchall()
    for relation in place_relations:
        source_id = keep_entity_id if relation["source_entity_id"] == remove_entity_id else relation["source_entity_id"]
        target_id = keep_entity_id if relation["target_entity_id"] == remove_entity_id else relation["target_entity_id"]
        if source_id == target_id:
            connection.execute("DELETE FROM evidence WHERE target_type = 'place_relation' AND target_id = ?", (relation["id"],))
            connection.execute("DELETE FROM place_relations WHERE id = ?", (relation["id"],))
            continue
        duplicate = connection.execute(
            """
            SELECT id FROM place_relations
            WHERE book_id = ? AND source_entity_id = ? AND target_entity_id = ?
              AND relative_position = ? AND first_segment = ? AND id != ?
            """,
            (
                book_id, source_id, target_id, relation["relative_position"],
                relation["first_segment"], relation["id"],
            ),
        ).fetchone()
        if duplicate is not None:
            _move_evidence(connection, "place_relation", int(relation["id"]), int(duplicate["id"]))
            connection.execute("DELETE FROM place_relations WHERE id = ?", (relation["id"],))
        else:
            connection.execute(
                "UPDATE place_relations SET source_entity_id = ?, target_entity_id = ? WHERE id = ?",
                (source_id, target_id, relation["id"]),
            )

    # 事件地点和参与者迁移后，主角设置也跟随保留实体。
    connection.execute(
        "UPDATE events SET location_entity_id = ? WHERE location_entity_id = ?",
        (keep_entity_id, remove_entity_id),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO event_participants(event_id, entity_id, role)
        SELECT event_id, ?, role FROM event_participants WHERE entity_id = ?
        """,
        (keep_entity_id, remove_entity_id),
    )
    connection.execute("DELETE FROM event_participants WHERE entity_id = ?", (remove_entity_id,))
    connection.execute(
        "UPDATE book_settings SET protagonist_entity_id = ? WHERE book_id = ? AND protagonist_entity_id = ?",
        (keep_entity_id, book_id, remove_entity_id),
    )

    # 别名和查重键都归到保留实体；冲突键由数据库唯一约束自然去重。
    connection.execute(
        "INSERT OR IGNORE INTO aliases(entity_id, alias) VALUES (?, ?)",
        (keep_entity_id, remove["name"]),
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO aliases(entity_id, alias)
        SELECT ?, alias FROM aliases WHERE entity_id = ?
        """,
        (keep_entity_id, remove_entity_id),
    )
    keys = connection.execute(
        "SELECT kind, normalized_name, source FROM entity_keys WHERE entity_id = ?",
        (remove_entity_id,),
    ).fetchall()
    connection.execute("DELETE FROM entity_keys WHERE entity_id = ?", (remove_entity_id,))
    for key in keys:
        connection.execute(
            """
            INSERT OR IGNORE INTO entity_keys(book_id, entity_id, kind, normalized_name, source)
            VALUES (?, ?, ?, ?, ?)
            """,
            (book_id, keep_entity_id, key["kind"], key["normalized_name"], key["source"]),
        )
    _move_evidence(connection, "entity", remove_entity_id, keep_entity_id)
    connection.execute(
        """
        UPDATE entities SET
            importance = MAX(importance, ?),
            first_segment = MIN(first_segment, ?),
            summary = CASE WHEN LENGTH(summary) >= LENGTH(?) THEN summary ELSE ? END
        WHERE id = ?
        """,
        (remove["importance"], remove["first_segment"], remove["summary"], remove["summary"], keep_entity_id),
    )
    connection.execute(
        "INSERT INTO entity_merges(book_id, kept_entity_id, removed_name, reason) VALUES (?, ?, ?, ?)",
        (book_id, keep_entity_id, remove["name"], reason),
    )
    connection.execute("DELETE FROM entities WHERE id = ?", (remove_entity_id,))
    return keep_entity_id


def register_entity_keys(connection: sqlite3.Connection, book_id: int) -> None:
    """登记规范名和别名，并把可疑跨实体别名交给人工确认。"""

    entities = connection.execute(
        "SELECT * FROM entities WHERE book_id = ? ORDER BY first_segment, id",
        (book_id,),
    ).fetchall()
    canonical: dict[tuple[str, str], int] = {}
    for entity in entities:
        key = normalized_name(entity["name"])
        if not key:
            continue
        identity = (entity["kind"], key)
        previous = canonical.get(identity)
        if previous is not None and previous != entity["id"]:
            left_id, right_id = sorted((previous, int(entity["id"])))
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_merge_candidates(
                    book_id, left_entity_id, right_entity_id, reason, confidence
                ) VALUES (?, ?, ?, '规范名称归一后完全相同', 0.999)
                """,
                (book_id, left_id, right_id),
            )
            continue
        canonical[identity] = int(entity["id"])
        connection.execute(
            """
            INSERT OR IGNORE INTO entity_keys(book_id, entity_id, kind, normalized_name, source)
            VALUES (?, ?, ?, ?, 'canonical')
            """,
            (book_id, entity["id"], entity["kind"], key),
        )

    alias_rows = connection.execute(
        """
        SELECT a.entity_id, a.alias, e.kind FROM aliases a
        JOIN entities e ON e.id = a.entity_id WHERE e.book_id = ?
        """,
        (book_id,),
    ).fetchall()
    for alias in alias_rows:
        key = normalized_name(alias["alias"])
        if not key or alias["alias"] in GENERIC_SHARED_ALIAS_KEYS:
            continue
        owner = connection.execute(
            "SELECT entity_id FROM entity_keys WHERE book_id = ? AND kind = ? AND normalized_name = ?",
            (book_id, alias["kind"], key),
        ).fetchone()
        if owner is None:
            connection.execute(
                """
                INSERT INTO entity_keys(book_id, entity_id, kind, normalized_name, source)
                VALUES (?, ?, ?, ?, 'alias')
                """,
                (book_id, alias["entity_id"], alias["kind"], key),
            )
        elif owner["entity_id"] != alias["entity_id"] and alias["alias"] not in GENERIC_SHARED_ALIAS_KEYS:
            left_id, right_id = sorted((int(owner["entity_id"]), int(alias["entity_id"])))
            connection.execute(
                """
                INSERT OR IGNORE INTO entity_merge_candidates(
                    book_id, left_entity_id, right_entity_id, reason, confidence
                ) VALUES (?, ?, ?, ?, 0.75)
                """,
                (book_id, left_id, right_id, f"共享称呼：{alias['alias']}"),
            )


def remove_generic_merge_candidates(connection: sqlite3.Connection, book_id: int) -> None:
    """清除仅因“龙王、天王”等通用头衔产生的同一实体建议。"""

    candidates = connection.execute(
        """
        SELECT id, reason FROM entity_merge_candidates
        WHERE book_id = ? AND status = 'unreviewed' AND reason LIKE '共享称呼：%'
        """,
        (book_id,),
    ).fetchall()
    for candidate in candidates:
        alias = str(candidate["reason"]).split("：", 1)[-1].strip()
        if alias in GENERIC_SHARED_ALIAS_KEYS:
            connection.execute("DELETE FROM entity_merge_candidates WHERE id = ?", (candidate["id"],))
    generic_keys = sorted({normalized_name(alias) for alias in GENERIC_SHARED_ALIAS_KEYS if normalized_name(alias)})
    placeholders = ",".join("?" for _ in generic_keys)
    connection.execute(
        f"DELETE FROM entity_keys WHERE book_id = ? AND source = 'alias' AND normalized_name IN ({placeholders})",  # noqa: S608
        (book_id, *generic_keys),
    )


def _deduplicate_facts(connection: sqlite3.Connection, book_id: int) -> None:
    """合并跨片段重复出现的关系、世界设定和数据库条目。"""

    claim_groups = connection.execute(
        """
        SELECT source_entity_id, target_entity_id, predicate, GROUP_CONCAT(id) AS ids
        FROM claims WHERE book_id = ?
        GROUP BY source_entity_id, target_entity_id, predicate HAVING COUNT(*) > 1
        """,
        (book_id,),
    ).fetchall()
    for group in claim_groups:
        ids = [int(value) for value in group["ids"].split(",")]
        keep_id = ids[0]
        for old_id in ids[1:]:
            _move_evidence(connection, "claim", old_id, keep_id)
            connection.execute("DELETE FROM claims WHERE id = ?", (old_id,))

    place_groups = connection.execute(
        """
        SELECT source_entity_id, target_entity_id, relative_position, GROUP_CONCAT(id) AS ids
        FROM place_relations WHERE book_id = ?
        GROUP BY source_entity_id, target_entity_id, relative_position HAVING COUNT(*) > 1
        """,
        (book_id,),
    ).fetchall()
    for group in place_groups:
        ids = [int(value) for value in group["ids"].split(",")]
        keep_id = ids[0]
        for old_id in ids[1:]:
            _move_evidence(connection, "place_relation", old_id, keep_id)
            connection.execute("DELETE FROM place_relations WHERE id = ?", (old_id,))

    for table, target_type, name_column in (
        ("world_notes", "world_note", "title"),
        ("entries", "entry", "name"),
    ):
        records = connection.execute(
            f"SELECT * FROM {table} WHERE book_id = ? ORDER BY first_segment, id",  # noqa: S608
            (book_id,),
        ).fetchall()
        kept_records: list[sqlite3.Row] = []
        for record in records:
            identity = (record["category"], normalized_name(record[name_column]))
            if not identity[1]:
                continue
            duplicate = next(
                (
                    kept
                    for kept in kept_records
                    if kept["category"] == record["category"]
                    and (
                        (
                            normalized_name(kept[name_column]) == identity[1]
                            and (table != "world_notes" or kept["created_by"] == record["created_by"])
                        )
                        or (table == "world_notes" and _same_world_topic(kept, record))
                    )
                ),
                None,
            )
            if duplicate is None:
                kept_records.append(record)
                continue
            keep_id = int(duplicate["id"])
            _move_evidence(connection, target_type, int(record["id"]), keep_id)
            if table == "world_notes":
                current_keep = connection.execute(
                    "SELECT summary FROM world_notes WHERE id = ?",
                    (keep_id,),
                ).fetchone()
                # 综合说明和原始世界事实都可能被其他综合说明引用，引用必须一起迁移。
                connection.execute(
                    """
                    INSERT OR IGNORE INTO synthesis_basis(world_note_id, basis_type, basis_id)
                    SELECT ?, basis_type, basis_id FROM synthesis_basis WHERE world_note_id = ?
                    """,
                    (keep_id, record["id"]),
                )
                connection.execute("DELETE FROM synthesis_basis WHERE world_note_id = ?", (record["id"],))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO synthesis_basis(world_note_id, basis_type, basis_id)
                    SELECT world_note_id, basis_type, ? FROM synthesis_basis
                    WHERE basis_type = 'world_note' AND basis_id = ?
                    """,
                    (keep_id, record["id"]),
                )
                connection.execute(
                    "DELETE FROM synthesis_basis WHERE basis_type = 'world_note' AND basis_id = ?",
                    (record["id"],),
                )
                connection.execute(
                    """
                    UPDATE world_notes SET summary = ?, confidence = MAX(confidence, ?),
                        first_segment = MIN(first_segment, ?)
                    WHERE id = ?
                    """,
                    (
                        _combine_summaries(str(current_keep["summary"]), str(record["summary"])),
                        record["confidence"], record["first_segment"], keep_id,
                    ),
                )
            if table == "entries":
                keep = connection.execute("SELECT attributes_json FROM entries WHERE id = ?", (keep_id,)).fetchone()
                try:
                    attributes = {**json.loads(keep["attributes_json"]), **json.loads(record["attributes_json"])}
                except (json.JSONDecodeError, TypeError):
                    attributes = {}
                connection.execute(
                    "UPDATE entries SET attributes_json = ?, confidence = MAX(confidence, ?) WHERE id = ?",
                    (json.dumps(attributes, ensure_ascii=False), record["confidence"], keep_id),
                )
            connection.execute(f"DELETE FROM {table} WHERE id = ?", (record["id"],))  # noqa: S608

    # 旧版综合卡可能已经把近似内容拼进同一说明；迁移时也要清理，而不是等待再次合并。
    world_summaries = connection.execute(
        "SELECT id, summary FROM world_notes WHERE book_id = ?",
        (book_id,),
    ).fetchall()
    for note in world_summaries:
        cleaned = _deduplicate_summary_sentences(str(note["summary"]))
        if cleaned != note["summary"]:
            connection.execute("UPDATE world_notes SET summary = ? WHERE id = ?", (cleaned, note["id"]))


def _link_world_facts_to_matching_syntheses(connection: sqlite3.Connection, book_id: int) -> None:
    """把标题不同但主题相同的原始卡归到综合卡下，页面只展示信息更完整的一张。"""

    syntheses = connection.execute(
        "SELECT * FROM world_notes WHERE book_id = ? AND created_by = 'synthesis' ORDER BY confidence DESC, id",
        (book_id,),
    ).fetchall()
    raw_notes = connection.execute(
        "SELECT * FROM world_notes WHERE book_id = ? AND created_by != 'synthesis' ORDER BY first_segment, id",
        (book_id,),
    ).fetchall()
    for raw_note in raw_notes:
        synthesis = next(
            (item for item in syntheses if _same_world_topic(item, raw_note, require_same_origin=False)),
            None,
        )
        if synthesis is None:
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO synthesis_basis(world_note_id, basis_type, basis_id)
            VALUES (?, 'world_note', ?)
            """,
            (synthesis["id"], raw_note["id"]),
        )


def recompute_chronology(connection: sqlite3.Connection, book_id: int) -> None:
    """使用无环约束图生成故事顺序，模型原始全书序号不参与排序。"""

    from app.semantic import recompute_chronology_dag

    recompute_chronology_dag(connection, book_id)


def update_book_memory(connection: sqlite3.Connection, book_id: int, through_segment: int) -> None:
    """用已核验结构生成因果、人物状态和未闭合线索组成的紧凑记忆。"""

    recent_events = connection.execute(
        """
        SELECT e.id, e.title, e.summary, e.temporal_value, e.location_entity_id,
            f.cause, f.goal, f.action, f.outcome, f.state_changes_json,
            f.open_threads_json, f.resolved_threads_json
        FROM events e LEFT JOIN event_narrative_frames f ON f.event_id = e.id
        WHERE e.book_id = ? AND e.first_segment <= ?
        ORDER BY e.narrative_order DESC, e.id DESC LIMIT 16
        """,
        (book_id, through_segment),
    ).fetchall()
    event_lines = [
        "｜".join(filter(None, (
            str(event["title"]),
            f"前因：{event['cause']}" if event["cause"] else "",
            f"行动：{event['action'] or event['summary']}",
            f"结果：{event['outcome']}" if event["outcome"] else "",
            f"时间：{event['temporal_value'] or '未知'}",
        )))
        for event in reversed(recent_events)
    ]
    state_lines = [
        f"{row['name']}｜当前位置：{row['location_name'] or '未知'}｜目标：{row['goal'] or '未知'}｜状态：{row['state_changes'] or '无新增'}"
        for row in connection.execute(
            """
            SELECT entity.name, place.name AS location_name,
                COALESCE(frame.goal, '') AS goal,
                COALESCE(frame.state_changes_json, '[]') AS state_changes
            FROM event_participants participant
            JOIN events event ON event.id = participant.event_id
            JOIN entities entity ON entity.id = participant.entity_id
            LEFT JOIN entities place ON place.id = event.location_entity_id
            LEFT JOIN event_narrative_frames frame ON frame.event_id = event.id
            WHERE event.book_id = ? AND event.first_segment <= ?
              AND event.id = (
                SELECT latest.id FROM events latest
                JOIN event_participants latest_participant ON latest_participant.event_id = latest.id
                WHERE latest.book_id = event.book_id
                  AND latest.first_segment <= ?
                  AND latest_participant.entity_id = participant.entity_id
                ORDER BY latest.story_order DESC, latest.id DESC LIMIT 1
              )
            ORDER BY entity.importance DESC, entity.id LIMIT 24
            """,
            (book_id, through_segment, through_segment),
        )
    ]
    open_threads: list[str] = []
    resolved_threads: set[str] = set()
    for event in recent_events:
        try:
            open_threads.extend(json.loads(event["open_threads_json"] or "[]"))
            resolved_threads.update(json.loads(event["resolved_threads_json"] or "[]"))
        except (TypeError, ValueError):
            continue
    open_lines = [str(item) for item in dict.fromkeys(open_threads) if item not in resolved_threads][-20:]
    summary = "\n".join([
        "最近场景：", *(event_lines or ["暂无"]),
        "人物状态：", *(state_lines or ["暂无"]),
        "未闭合线索：", *(open_lines or ["暂无"]),
    ])
    connection.execute(
        """
        INSERT INTO book_memory(book_id, through_segment, summary, updated_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(book_id) DO UPDATE SET
            through_segment = excluded.through_segment,
            summary = excluded.summary,
            updated_at = CURRENT_TIMESTAMP
        """,
        (book_id, through_segment, summary),
    )


def consolidate_book(connection: sqlite3.Connection, book_id: int, through_segment: int) -> None:
    """在每个片段完成后执行便宜、可重复的跨章节整理。"""

    register_entity_keys(connection, book_id)
    remove_generic_merge_candidates(connection, book_id)
    _deduplicate_facts(connection, book_id)
    _link_world_facts_to_matching_syntheses(connection, book_id)
    from app.semantic import consolidate_semantics

    consolidate_semantics(connection, book_id)
    book = connection.execute("SELECT segment_count FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is not None and through_segment >= max(0, int(book["segment_count"]) - 1):
        from app.benchmarks import evaluate_benchmarks, seed_benchmark_cases

        if seed_benchmark_cases(connection, book_id):
            evaluate_benchmarks(connection, book_id)
    update_book_memory(connection, book_id, through_segment)


def build_analysis_context(connection: sqlite3.Connection, book_id: int, ordinal: int) -> str:
    """限制上下文体积，并严格排除当前片段之后才出现的实体和事件。"""

    entities = connection.execute(
        """
        SELECT e.id, e.name, e.kind, e.summary, e.importance,
               GROUP_CONCAT(a.alias, '、') AS aliases
        FROM entities e LEFT JOIN aliases a ON a.entity_id = e.id
        WHERE e.book_id = ? AND e.first_segment < ?
        GROUP BY e.id ORDER BY e.importance DESC, e.first_segment LIMIT 80
        """,
        (book_id, ordinal),
    ).fetchall()
    entity_lines = [
        f"- {item['kind']}｜{item['name']}｜别名：{item['aliases'] or '无'}｜{item['summary']}"
        for item in entities
    ]
    recent_events = connection.execute(
        """
        SELECT e.title, e.summary, e.temporal_value,
            f.cause, f.goal, f.action, f.outcome, f.open_threads_json, f.resolved_threads_json
        FROM events e LEFT JOIN event_narrative_frames f ON f.event_id = e.id
        WHERE e.book_id = ? AND e.first_segment < ?
        ORDER BY e.first_segment DESC, e.narrative_order DESC, e.id DESC LIMIT 16
        """,
        (book_id, ordinal),
    ).fetchall()
    event_lines = [
        "｜".join(filter(None, (
            str(event["title"]),
            f"前因：{event['cause']}" if event["cause"] else "",
            f"行动：{event['action'] or event['summary']}",
            f"结果：{event['outcome']}" if event["outcome"] else "",
            f"目标：{event['goal']}" if event["goal"] else "",
            f"时间：{event['temporal_value'] or '未知'}",
        )))
        for event in reversed(recent_events)
    ]
    open_threads: list[str] = []
    resolved_threads: set[str] = set()
    for event in reversed(recent_events):
        try:
            open_threads.extend(json.loads(event["open_threads_json"] or "[]"))
            resolved_threads.update(json.loads(event["resolved_threads_json"] or "[]"))
        except (TypeError, ValueError):
            continue
    unresolved = [str(item) for item in dict.fromkeys(open_threads) if item not in resolved_threads][-20:]
    parts = [
        "已确认实体：", *(entity_lines or ["- 暂无"]),
        "最近因果场景：", *(event_lines or ["暂无"]),
        "仍未解决的线索：", *(unresolved or ["暂无"]),
    ]
    return "\n".join(parts)[:14_000]
