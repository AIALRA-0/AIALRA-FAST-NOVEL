"""统一处理书籍的阅读窗口和防剧透上限。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReadingWindow:
    """一个接口请求实际使用的片段窗口。"""

    from_segment: int
    through_segment: int
    spoiler_ceiling: int
    total_segments: int

    def payload(self) -> dict[str, int]:
        return {
            "from_segment": self.from_segment,
            "through_segment": self.through_segment,
            "spoiler_ceiling": self.spoiler_ceiling,
            "total_segments": self.total_segments,
        }


def resolve_reading_window(
    connection: sqlite3.Connection,
    book_id: int,
    *,
    from_segment: int | None = None,
    through_segment: int | None = None,
    spoiler_ceiling: int | None = None,
) -> ReadingWindow:
    """把可选的起止片段裁剪到书籍范围，并拒绝反向窗口。

    旧客户端只传 ``through_segment`` 时，它仍表示防剧透上限；新客户端可以
    通过 ``spoiler_ceiling`` 保留独立上限，再在上限内缩小本次可见终点。
    起点只缩小可见窗口，不会把上限之后的内容带回响应。
    """

    row = connection.execute("SELECT segment_count FROM books WHERE id = ?", (book_id,)).fetchone()
    if row is None:
        raise ValueError("找不到这本书")
    total = max(0, int(row["segment_count"] or 0))
    maximum = max(0, total - 1)
    if spoiler_ceiling is None:
        # 保持旧客户端语义：只有 through_segment 时，它同时是终点和上限
        ceiling = maximum if through_segment is None else min(maximum, max(0, int(through_segment)))
    else:
        ceiling = min(maximum, max(0, int(spoiler_ceiling)))
    requested_through = ceiling if through_segment is None else min(ceiling, max(0, int(through_segment)))
    requested_from = 0 if from_segment is None else int(from_segment)
    if requested_from < 0:
        requested_from = 0
    if requested_from > requested_through:
        raise ValueError("阅读范围的起始片段不能晚于结束片段")
    return ReadingWindow(
        from_segment=min(requested_from, maximum),
        through_segment=requested_through,
        spoiler_ceiling=ceiling,
        total_segments=total,
    )


def mark_context_only(items: list[dict[str, Any]], window: ReadingWindow) -> list[dict[str, Any]]:
    """给窗口起点之前、但为阅读上下文保留的记录加上明确标记。"""

    for item in items:
        value = item.get("first_segment")
        if value is None or value == "":
            continue
        item["context_only"] = int(value) < window.from_segment
    return items
