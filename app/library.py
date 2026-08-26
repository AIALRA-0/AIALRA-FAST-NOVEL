"""书库文件夹与保留旧证据的增量更新。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
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
        "SELECT ordinal, chapter_title, text FROM segments WHERE book_id = ? ORDER BY ordinal",
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

    status = "needs_review" if conflicts else "applied"
    cursor = connection.execute(
        """
        INSERT INTO book_update_batches(
            book_id, mode, filename, source_type, source_hash, proposed_title, proposed_author,
            previous_segment_count, added_segment_count, common_prefix_count, status,
            conflicts_json, payload_json, resolution, applied_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, CASE WHEN ? = 'applied' THEN CURRENT_TIMESTAMP END)
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
        "message": "新增章节已经追加，旧分析保持不变。" if added else "文本没有新增内容。" if not conflicts else "旧章节发生变化，请选择处理方式。",
    }


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
    resolution = "import_as_new" if action == "auto" else action
    if resolution == "keep_current":
        connection.execute(
            "UPDATE book_update_batches SET status = 'resolved', resolution = ?, applied_at = CURRENT_TIMESTAMP WHERE id = ?",
            (resolution, update_id),
        )
        return {"id": update_id, "status": "resolved", "resolution": resolution, "book_id": int(update["book_id"])}
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
        item.pop("payload_json", None)
        item["conflict_count"] = len(item["conflicts"])
        result.append(item)
    return result
