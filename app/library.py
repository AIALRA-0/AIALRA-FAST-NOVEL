"""书库文件夹与保留旧证据的增量更新。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any

from app.importers import ParsedBook, ParsedSegment


def _segment_payload(segment: ParsedSegment) -> dict[str, Any]:
    """把解析片段保存为可延后处理的本地更新候选。"""

    return {
        "ordinal": segment.ordinal,
        "chapter_title": segment.chapter_title,
        "anchor": segment.anchor,
        "text": segment.text,
        "char_start": segment.char_start,
        "char_end": segment.char_end,
    }


def _excerpt(text: str, limit: int = 180) -> str:
    """冲突清单只展示足够辨认的开头，完整原文仍保存在更新批次中。"""

    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit] + "…"


def _same_segment(stored: sqlite3.Row, proposed: ParsedSegment) -> bool:
    """章节标题和正文都相同才算旧内容未变化。"""

    return str(stored["chapter_title"]) == proposed.chapter_title and str(stored["text"]) == proposed.text


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_key(title: str, text: str) -> str:
    normalized = " ".join(title.casefold().split())
    return hashlib.sha256(f"{normalized}\0{_content_hash(text)}".encode("utf-8")).hexdigest()[:24]


def _body_hash(title: str, text: str) -> str:
    """Hash chapter prose independently from the heading so a pure rename remains reusable."""

    body = text.strip()
    normalized_title = title.strip()
    if normalized_title and body.startswith(normalized_title):
        remainder = body[len(normalized_title):].lstrip(" \t\r\n：:—-")
        if remainder:
            body = remainder
    return _content_hash(body)


def _build_update_plan(stored: list[sqlite3.Row], proposed: list[ParsedSegment]) -> dict[str, Any]:
    """Align old and new chapters without assuming that unchanged chapters keep their ordinal."""

    old_hashes = [_body_hash(str(item["chapter_title"]), str(item["text"])) for item in stored]
    new_hashes = [_body_hash(item.chapter_title, item.text) for item in proposed]
    matches: list[dict[str, Any]] = []
    unmatched_old = set(range(len(stored)))
    unmatched_new = set(range(len(proposed)))
    old_by_hash: dict[str, list[int]] = defaultdict(list)
    new_by_hash: dict[str, list[int]] = defaultdict(list)
    for index, digest in enumerate(old_hashes):
        old_by_hash[digest].append(index)
    for index, digest in enumerate(new_hashes):
        new_by_hash[digest].append(index)

    # Exact content is reusable even when a chapter moved. Duplicate passages are paired by
    # title first and then by nearest position so repeated boilerplate cannot steal a chapter ID.
    for digest in sorted(set(old_by_hash) & set(new_by_hash)):
        available = set(old_by_hash[digest])
        for new_index in new_by_hash[digest]:
            if not available:
                break
            new_title = proposed[new_index].chapter_title
            old_index = min(
                available,
                key=lambda index: (
                    str(stored[index]["chapter_title"]) != new_title,
                    abs(index - new_index),
                    index,
                ),
            )
            available.remove(old_index)
            unmatched_old.discard(old_index)
            unmatched_new.discard(new_index)
            old_title = str(stored[old_index]["chapter_title"])
            matches.append({
                "kind": "unchanged" if old_index == new_index and old_title == new_title else "renamed" if old_index == new_index else "reordered",
                "old_index": old_index,
                "new_index": new_index,
                "segment_id": int(stored[old_index]["id"]),
            })

    # Pair a remaining old and new chapter only when their titles are close enough; otherwise
    # insertion and deletion stay explicit and cannot silently overwrite unrelated evidence.
    for old_index in sorted(tuple(unmatched_old)):
        best: tuple[float, int] | None = None
        old_title = str(stored[old_index]["chapter_title"])
        for new_index in unmatched_new:
            score = SequenceMatcher(None, old_title.casefold(), proposed[new_index].chapter_title.casefold(), autojunk=False).ratio()
            if best is None or score > best[0]:
                best = (score, new_index)
        if best is not None and best[0] >= 0.68:
            new_index = best[1]
            unmatched_old.remove(old_index)
            unmatched_new.remove(new_index)
            matches.append({"kind": "modified", "old_index": old_index, "new_index": new_index, "segment_id": int(stored[old_index]["id"])})

    for old_index in sorted(unmatched_old):
        matches.append({"kind": "removed", "old_index": old_index, "new_index": None, "segment_id": int(stored[old_index]["id"])})
    for new_index in sorted(unmatched_new):
        matches.append({"kind": "inserted", "old_index": None, "new_index": new_index, "segment_id": None})
    matches.sort(key=lambda item: (len(proposed) if item["new_index"] is None else int(item["new_index"]), str(item["kind"])))
    unchanged = sum(item["kind"] in {"unchanged", "renamed", "reordered"} for item in matches)
    affected_new = {int(item["new_index"]) for item in matches if item["new_index"] is not None and item["kind"] in {"modified", "inserted"}}
    for item in matches:
        if item["kind"] == "removed" and proposed:
            affected_new.add(min(int(item["old_index"]), len(proposed) - 1))
    affected_new = sorted(affected_new)
    context = sorted({value for index in affected_new for value in (index - 1, index, index + 1) if 0 <= value < len(proposed)})
    counts = {kind: sum(item["kind"] == kind for item in matches) for kind in ("unchanged", "renamed", "reordered", "modified", "inserted", "removed")}
    return {
        "matches": matches,
        "counts": counts,
        "reuse_ratio": round(unchanged / max(1, len(proposed)), 4),
        "affected_scope": {"changed_ordinals": affected_new, "context_ordinals": context},
    }


def _append_segments(
    connection: sqlite3.Connection,
    book_id: int,
    proposed: list[dict[str, Any]],
    source_hash: str,
    filename: str,
) -> tuple[int, int]:
    """在一个事务中追加片段并保留全部旧片段标识。"""

    book = connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        raise ValueError("找不到这本书。")
    start_ordinal = int(book["segment_count"])
    last = connection.execute(
        "SELECT char_end FROM segments WHERE book_id = ? ORDER BY ordinal DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    cursor = int(last["char_end"]) + 2 if last is not None else 0
    added_character_count = 0
    rows: list[tuple[Any, ...]] = []
    for offset, segment in enumerate(proposed):
        ordinal = start_ordinal + offset
        text = str(segment["text"])
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
        rows.append(
            (
                book_id,
                ordinal,
                str(segment["chapter_title"])[:160],
                f"seg-{ordinal}-{digest}",
                text,
                cursor,
                cursor + len(text),
            )
        )
        added_character_count += len(text)
        cursor += len(text) + 2
    if rows:
        connection.executemany(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    combined_hash = hashlib.sha256(
        f"{book['source_hash']}:{source_hash}:{len(rows)}".encode("utf-8")
    ).hexdigest()
    connection.execute(
        """
        UPDATE books SET segment_count = segment_count + ?, character_count = character_count + ?,
            source_hash = ?, original_filename = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (len(rows), added_character_count, combined_hash, filename, book_id),
    )
    return start_ordinal, len(rows)


def preview_book_update(
    connection: sqlite3.Connection,
    book_id: int,
    parsed: ParsedBook,
    mode: str,
) -> dict[str, Any]:
    """比较新版与旧片段；无冲突时立即追加，有冲突时保存完整清单。"""

    if mode not in {"auto", "full", "append"}:
        raise ValueError("更新方式无效。")
    book = connection.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    if book is None:
        raise ValueError("找不到这本书。")
    stored = connection.execute(
        "SELECT id, ordinal, chapter_title, anchor, text, char_start, char_end, revision FROM segments WHERE book_id = ? ORDER BY ordinal",
        (book_id,),
    ).fetchall()
    payload = [_segment_payload(item) for item in parsed.segments]
    conflicts: list[dict[str, Any]] = []
    common = 0
    additions: list[dict[str, Any]] = []

    if mode == "append":
        existing_hashes = {
            hashlib.sha256(str(item["text"]).encode("utf-8")).hexdigest(): item for item in stored
        }
        for proposed in parsed.segments:
            digest = hashlib.sha256(proposed.text.encode("utf-8")).hexdigest()
            duplicate = existing_hashes.get(digest)
            if duplicate is not None:
                conflicts.append(
                    {
                        "kind": "duplicate_append",
                        "ordinal": int(duplicate["ordinal"]),
                        "old_title": str(duplicate["chapter_title"]),
                        "new_title": proposed.chapter_title,
                        "old_excerpt": _excerpt(str(duplicate["text"])),
                        "new_excerpt": _excerpt(proposed.text),
                    }
                )
        if not conflicts:
            additions = payload
            common = len(stored)
    else:
        overlap = min(len(stored), len(parsed.segments))
        while common < overlap and _same_segment(stored[common], parsed.segments[common]):
            common += 1
        if common == len(stored):
            additions = payload[common:]
        else:
            maximum = max(len(stored), len(parsed.segments))
            for index in range(common, maximum):
                old = stored[index] if index < len(stored) else None
                new = parsed.segments[index] if index < len(parsed.segments) else None
                if old is not None and new is not None and _same_segment(old, new):
                    continue
                conflicts.append(
                    {
                        "kind": "changed" if old is not None and new is not None else "removed" if new is None else "inserted",
                        "ordinal": index,
                        "old_title": str(old["chapter_title"]) if old is not None else "—",
                        "new_title": new.chapter_title if new is not None else "—",
                        "old_excerpt": _excerpt(str(old["text"])) if old is not None else "",
                        "new_excerpt": _excerpt(new.text) if new is not None else "",
                    }
                )

    update_plan = _build_update_plan(stored, parsed.segments) if mode != "append" else {
        "matches": [], "counts": {"unchanged": len(stored), "renamed": 0, "reordered": 0, "modified": 0, "inserted": len(additions), "removed": 0},
        "reuse_ratio": round(len(stored) / max(1, len(stored) + len(additions)), 4),
        "affected_scope": {"changed_ordinals": list(range(len(stored), len(stored) + len(additions))), "context_ordinals": list(range(max(0, len(stored) - 1), len(stored) + len(additions)))},
    }
    status = "needs_review" if conflicts else "applied"
    cursor = connection.execute(
        """
        INSERT INTO book_update_batches(
            book_id, mode, filename, source_type, source_hash, proposed_title, proposed_author,
            previous_segment_count, added_segment_count, common_prefix_count, status,
            conflicts_json, payload_json, resolution, applied_at, match_summary_json,
            affected_scope_json, reuse_ratio
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, CASE WHEN ? = 'applied' THEN CURRENT_TIMESTAMP END, ?, ?, ?)
        """,
        (
            book_id,
            mode,
            parsed.original_filename,
            parsed.source_type,
            parsed.source_hash,
            parsed.title,
            parsed.author,
            len(stored),
            common,
            status,
            json.dumps(conflicts, ensure_ascii=False),
            json.dumps(payload, ensure_ascii=False),
            "safe_append" if not conflicts else "",
            status,
            json.dumps({"counts": update_plan["counts"], "matches": update_plan["matches"]}, ensure_ascii=False),
            json.dumps(update_plan["affected_scope"], ensure_ascii=False),
            update_plan["reuse_ratio"],
        ),
    )
    update_id = int(cursor.lastrowid)
    start_segment = len(stored)
    added = 0
    if not conflicts and additions:
        start_segment, added = _append_segments(
            connection, book_id, additions, parsed.source_hash, parsed.original_filename
        )
        connection.execute(
            "UPDATE book_update_batches SET added_segment_count = ? WHERE id = ?",
            (added, update_id),
        )
    return {
        "id": update_id,
        "book_id": book_id,
        "status": status,
        "mode": mode,
        "common_prefix_count": common,
        "previous_segment_count": len(stored),
        "added_segment_count": added,
        "start_segment": start_segment,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "match_summary": update_plan["counts"],
        "affected_scope": update_plan["affected_scope"],
        "reuse_ratio": update_plan["reuse_ratio"],
        "message": "新增章节已经追加，旧分析保持不变" if added else "文本没有新增内容" if not conflicts else "已生成增量合并方案；可以应用、另存或保留当前版本",
    }


def _archive_segment(connection: sqlite3.Connection, update_id: int, segment: sqlite3.Row) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO segment_versions(
            book_id, segment_id, update_id, revision, ordinal, chapter_title, anchor, text, content_hash, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'superseded')
        """,
        (
            int(segment["book_id"]), int(segment["id"]), update_id, int(segment["revision"]),
            int(segment["ordinal"]), str(segment["chapter_title"]), str(segment["anchor"]),
            str(segment["text"]), str(segment["content_hash"] or _content_hash(str(segment["text"]))),
        ),
    )


def _remove_orphan_model_records(connection: sqlite3.Connection, book_id: int) -> None:
    """Delete only generated records that lost every quote; human corrections always survive."""

    for table, target_type in (("claims", "claim"), ("events", "event"), ("world_notes", "world_note"), ("entries", "entry"), ("entities", "entity")):
        connection.execute(
            f"""
            DELETE FROM {table}
            WHERE book_id = ? AND created_by != 'human'
              AND NOT EXISTS (
                  SELECT 1 FROM evidence WHERE evidence.target_type = ? AND evidence.target_id = {table}.id
              )
            """,  # noqa: S608
            (book_id, target_type),
        )


def _reanchor_evidence_backed_records(connection: sqlite3.Connection, book_id: int) -> None:
    """Keep spoiler gates and story scopes aligned after chapter reorders."""

    table_by_type = {
        "entity": "entities",
        "event": "events",
        "claim": "claims",
        "place_relation": "place_relations",
        "world_note": "world_notes",
        "entry": "entries",
    }
    for target_type, table in table_by_type.items():
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is None:
            continue
        rows = connection.execute(
            """
            SELECT evidence.target_id, MIN(segment.ordinal) AS first_segment
            FROM evidence
            JOIN segments segment ON segment.id = evidence.segment_id
            WHERE evidence.book_id = ? AND evidence.target_type = ?
            GROUP BY evidence.target_id
            """,
            (book_id, target_type),
        ).fetchall()
        if not rows:
            continue
        ids = [int(row["target_id"]) for row in rows]
        placeholders = ",".join("?" for _ in ids)
        connection.execute(
            f"UPDATE {table} SET first_segment = first_segment + 1000000 WHERE book_id = ? AND id IN ({placeholders})",  # noqa: S608
            (book_id, *ids),
        )
        connection.executemany(
            f"UPDATE {table} SET first_segment = ? WHERE book_id = ? AND id = ?",  # noqa: S608
            [(int(row["first_segment"]), book_id, int(row["target_id"])) for row in rows],
        )


def _apply_incremental_update(connection: sqlite3.Connection, update: sqlite3.Row) -> dict[str, Any]:
    payload = json.loads(str(update["payload_json"]))
    summary = json.loads(str(update["match_summary_json"] or "{}"))
    matches = list(summary.get("matches") or [])
    current_rows = {
        int(row["id"]): row for row in connection.execute(
            "SELECT * FROM segments WHERE book_id = ? ORDER BY ordinal", (int(update["book_id"]),)
        ).fetchall()
    }
    changed_ids: set[int] = set()
    removed_ids: set[int] = set()
    # Move every current ordinal out of the unique-key range before assigning the new order.
    connection.execute("UPDATE segments SET ordinal = ordinal + 1000000 WHERE book_id = ?", (int(update["book_id"]),))
    for match in matches:
        kind = str(match["kind"])
        segment_id = match.get("segment_id")
        new_index = match.get("new_index")
        if segment_id is None:
            continue
        current = current_rows[int(segment_id)]
        if kind in {"modified", "removed"}:
            _archive_segment(connection, int(update["id"]), current)
            changed_ids.add(int(segment_id))
        if kind == "removed":
            removed_ids.add(int(segment_id))
            continue
        proposed = payload[int(new_index)]
        text = str(proposed["text"])
        connection.execute(
            """
            UPDATE segments SET ordinal = ?, chapter_title = ?, anchor = ?, text = ?, char_start = ?, char_end = ?,
                stable_key = ?, content_hash = ?, revision = revision + ? WHERE id = ?
            """,
            (
                int(new_index), str(proposed["chapter_title"]), f"seg-{new_index}-{_content_hash(text)[:12]}", text,
                int(proposed["char_start"]), int(proposed["char_end"]), _stable_key(str(proposed["chapter_title"]), text),
                _content_hash(text), 1 if kind == "modified" else 0, int(segment_id),
            ),
        )
    for match in matches:
        if str(match["kind"]) != "inserted":
            continue
        new_index = int(match["new_index"])
        proposed = payload[new_index]
        text = str(proposed["text"])
        connection.execute(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end, stable_key, content_hash, revision)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                int(update["book_id"]), new_index, str(proposed["chapter_title"]), f"seg-{new_index}-{_content_hash(text)[:12]}", text,
                int(proposed["char_start"]), int(proposed["char_end"]), _stable_key(str(proposed["chapter_title"]), text), _content_hash(text),
            ),
        )
    if changed_ids:
        placeholders = ",".join("?" for _ in changed_ids)
        connection.execute(f"DELETE FROM segment_results WHERE segment_id IN ({placeholders})", tuple(changed_ids))  # noqa: S608
        connection.execute(f"DELETE FROM evidence WHERE segment_id IN ({placeholders})", tuple(changed_ids))  # noqa: S608
    if removed_ids:
        placeholders = ",".join("?" for _ in removed_ids)
        connection.execute(f"DELETE FROM segments WHERE id IN ({placeholders})", tuple(removed_ids))  # noqa: S608
    _remove_orphan_model_records(connection, int(update["book_id"]))
    _reanchor_evidence_backed_records(connection, int(update["book_id"]))
    connection.execute("DELETE FROM map_layout_snapshots WHERE book_id = ?", (int(update["book_id"]),))
    connection.execute("DELETE FROM narrative_memories WHERE book_id = ?", (int(update["book_id"]),)) if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='narrative_memories'").fetchone() else None
    connection.execute(
        """
        UPDATE books SET segment_count = ?, character_count = ?, source_hash = ?, original_filename = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (len(payload), sum(len(str(item["text"])) for item in payload), str(update["source_hash"]), str(update["filename"]), int(update["book_id"])),
    )
    connection.execute(
        "UPDATE book_update_batches SET status = 'resolved', resolution = 'apply_incremental', added_segment_count = ?, applied_at = CURRENT_TIMESTAMP WHERE id = ?",
        (sum(item["kind"] == "inserted" for item in matches), int(update["id"])),
    )
    affected = json.loads(str(update["affected_scope_json"] or "{}"))
    return {"id": int(update["id"]), "status": "resolved", "resolution": "apply_incremental", "book_id": int(update["book_id"]), "reuse_ratio": float(update["reuse_ratio"] or 0), "affected_scope": affected}


def resolve_book_update(connection: sqlite3.Connection, update_id: int, action: str) -> dict[str, Any]:
    """保留当前书或把冲突新版另存；两种选择都不覆盖旧证据。"""

    update = connection.execute(
        "SELECT u.*, b.folder_id FROM book_update_batches u JOIN books b ON b.id = u.book_id WHERE u.id = ?",
        (update_id,),
    ).fetchone()
    if update is None:
        raise ValueError("找不到这次更新。")
    if str(update["status"]) != "needs_review":
        raise ValueError("这次更新已经处理。")
    resolution = "apply_incremental" if action == "auto" else action
    if resolution == "keep_current":
        connection.execute(
            "UPDATE book_update_batches SET status = 'resolved', resolution = ?, applied_at = CURRENT_TIMESTAMP WHERE id = ?",
            (resolution, update_id),
        )
        return {"id": update_id, "status": "resolved", "resolution": resolution, "book_id": int(update["book_id"])}
    if resolution == "apply_incremental":
        return _apply_incremental_update(connection, update)
    if resolution != "import_as_new":
        raise ValueError("未知处理方式。")

    existing = connection.execute("SELECT id FROM books WHERE source_hash = ?", (update["source_hash"],)).fetchone()
    if existing is not None:
        new_book_id = int(existing["id"])
    else:
        payload = json.loads(str(update["payload_json"]))
        title = str(update["proposed_title"])
        same_title = connection.execute("SELECT 1 FROM books WHERE title = ?", (title,)).fetchone()
        if same_title is not None:
            title += "（新版）"
        cursor = connection.execute(
            """
            INSERT INTO books(
                title, author, source_type, source_hash, original_filename, folder_id,
                segment_count, character_count, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                title,
                str(update["proposed_author"]),
                str(update["source_type"]),
                str(update["source_hash"]),
                str(update["filename"]),
                update["folder_id"],
                len(payload),
                sum(len(str(item["text"])) for item in payload),
            ),
        )
        new_book_id = int(cursor.lastrowid)
        connection.executemany(
            """
            INSERT INTO segments(book_id, ordinal, chapter_title, anchor, text, char_start, char_end)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    new_book_id,
                    index,
                    str(item["chapter_title"]),
                    f"seg-{index}-{hashlib.sha256(str(item['text']).encode('utf-8')).hexdigest()[:12]}",
                    str(item["text"]),
                    int(item["char_start"]),
                    int(item["char_end"]),
                )
                for index, item in enumerate(payload)
            ],
        )
    connection.execute(
        "UPDATE book_update_batches SET status = 'resolved', resolution = ?, applied_at = CURRENT_TIMESTAMP WHERE id = ?",
        (resolution, update_id),
    )
    return {"id": update_id, "status": "resolved", "resolution": resolution, "book_id": new_book_id}


def list_book_updates(connection: sqlite3.Connection, book_id: int) -> list[dict[str, Any]]:
    """返回更新历史和仍待处理的完整冲突清单。"""

    result: list[dict[str, Any]] = []
    for row in connection.execute(
        "SELECT * FROM book_update_batches WHERE book_id = ? ORDER BY id DESC",
        (book_id,),
    ).fetchall():
        item = dict(row)
        item["conflicts"] = json.loads(str(item.pop("conflicts_json")))
        item["match_summary"] = json.loads(str(item.pop("match_summary_json", "{}") or "{}"))
        item["affected_scope"] = json.loads(str(item.pop("affected_scope_json", "{}") or "{}"))
        item.pop("payload_json", None)
        item["conflict_count"] = len(item["conflicts"])
        result.append(item)
    return result
