"""本机五部公版全文的确定性导入回归，不调用模型，也不使用合成正文。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.importers import parse_book


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "samples"
EXPECTED = {
    "pg23863-shuihuzhuan.txt": {"segments": 155, "minimum_characters": 530_000},
    "pg23950-sanguo.txt": {"segments": 211, "minimum_characters": 600_000},
    "pg23962-xiyouji.txt": {"segments": 203, "minimum_characters": 730_000},
    "pg24264-hongloumeng.txt": {"segments": 328, "minimum_characters": 890_000},
    "pg51828-liaozhai.txt": {"segments": 114, "minimum_characters": 470_000},
}


@pytest.mark.parametrize(("filename", "expected"), EXPECTED.items())
def test_real_public_domain_book_is_read_completely(filename: str, expected: dict[str, int]) -> None:
    """全文去除公版包装后仍应保留稳定章节数、正文规模和唯一原文锚点。"""

    path = CORPUS / filename
    if not path.exists():
        pytest.skip(f"本机公版语料不存在：{filename}")
    parsed = parse_book(filename, path.read_bytes())
    assert len(parsed.segments) == expected["segments"]
    assert parsed.character_count >= expected["minimum_characters"]
    assert len({segment.anchor for segment in parsed.segments}) == len(parsed.segments)
    assert [segment.ordinal for segment in parsed.segments] == list(range(len(parsed.segments)))
    assert all(segment.text and segment.chapter_title for segment in parsed.segments)
    assert all(segment.char_start < segment.char_end for segment in parsed.segments)
    assert all("Project Gutenberg" not in segment.text for segment in parsed.segments)


def test_real_corpus_covers_five_distinct_long_form_structures() -> None:
    """正式真实语料集合必须同时包含五部作品，不能静默退化成单书测试。"""

    missing = [filename for filename in EXPECTED if not (CORPUS / filename).exists()]
    if missing:
        pytest.skip(f"本机缺少公版语料：{', '.join(missing)}")
    parsed = [parse_book(filename, (CORPUS / filename).read_bytes()) for filename in EXPECTED]
    assert len(parsed) == 5
    assert sum(len(book.segments) for book in parsed) == 1_011
    assert sum(book.character_count for book in parsed) >= 3_220_000
    assert len({book.source_hash for book in parsed}) == 5
