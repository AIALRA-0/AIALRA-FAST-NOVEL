"""本机十二部开放全文的确定性导入回归，不调用模型，也不使用合成正文。"""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from app.importers import parse_book
from scripts.prepare_real_corpus_qa import build_database
import sqlite3


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data" / "samples"
EXPECTED = {
    "pg23863-shuihuzhuan.txt": {"segments": 155, "minimum_characters": 530_000},
    "pg23950-sanguo.txt": {"segments": 211, "minimum_characters": 600_000},
    "pg23962-xiyouji.txt": {"segments": 203, "minimum_characters": 730_000},
    "pg24264-hongloumeng.txt": {"segments": 328, "minimum_characters": 890_000},
    "pg51828-liaozhai.txt": {"segments": 114, "minimum_characters": 470_000},
    "pg23818-jinghuayuan.txt": {"segments": 138, "minimum_characters": 420_000},
    "pg26872-haishanghua.txt": {"segments": 190, "minimum_characters": 320_000},
    "pg1342-pride-and-prejudice.txt": {"segments": 176, "minimum_characters": 710_000},
    "pg1661-sherlock-holmes.txt": {"segments": 135, "minimum_characters": 550_000},
    "pg62-princess-of-mars.txt": {"segments": 87, "minimum_characters": 360_000},
    "aozora-galactic-railroad.html": {"segments": 10, "minimum_characters": 40_000},
    "the-spiraling-web.epub": {"segments": 121, "minimum_characters": 480_000},
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
    if filename == "aozora-galactic-railroad.html":
        full_text = "\n".join(segment.text for segment in parsed.segments)
        assert "銀河鉄道の夜" in full_text
        assert "ジョバンニ" in full_text
        assert "揤婥" not in full_text


def test_real_corpus_covers_twelve_distinct_cross_genre_structures() -> None:
    """正式真实语料必须包含十二部跨语言作品，不能静默退化成合成演示。"""

    missing = [filename for filename in EXPECTED if not (CORPUS / filename).exists()]
    if missing:
        pytest.skip(f"本机缺少公版语料：{', '.join(missing)}")
    parsed = [parse_book(filename, (CORPUS / filename).read_bytes()) for filename in EXPECTED]
    assert len(parsed) == 12
    assert sum(len(book.segments) for book in parsed) == 1_868
    assert sum(book.character_count for book in parsed) >= 6_190_000
    assert len({book.source_hash for book in parsed}) == 12


def test_real_corpus_manifest_has_source_license_and_quality_allocation() -> None:
    """每部作品都有来源和许可，质量集严格分配 300 条案例与 60 条密封案例。"""

    manifest = json.loads((ROOT / "evals" / "real_corpus_manifest.json").read_text(encoding="utf-8"))
    quality = json.loads((ROOT / "evals" / "quality_corpus_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["works"]) == 12
    assert all(work["source_url"] and work["license_name"] and work["language"] for work in manifest["works"])
    assert len(quality["works"]) == 12
    assert quality["case_policy"]["cases_per_work"] == 25
    assert quality["case_policy"]["development_cases_per_work"] == 20
    assert quality["case_policy"]["sealed_holdout_cases_per_work"] == 5
    assert quality["case_policy"]["total_cases"] == 300
    assert quality["case_policy"]["total_sealed_holdout_cases"] == 60
    assert any(work["coverage_role"] == "proxy" for work in quality["works"])


def test_private_corpus_database_separates_real_and_synthetic_books(tmp_path: Path) -> None:
    """真实开放作品进入独立目录，系统虚构演示不能混入真实作品统计。"""

    missing = [filename for filename in EXPECTED if not (CORPUS / filename).exists()]
    if missing:
        pytest.skip(f"本机缺少开放语料：{', '.join(missing)}")
    database = tmp_path / "open-corpus.db"
    build_database(database, CORPUS)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        real = connection.execute("SELECT * FROM books WHERE corpus_kind = 'open_real'").fetchall()
        synthetic = connection.execute("SELECT * FROM books WHERE corpus_kind = 'synthetic'").fetchall()
        folders = {row["id"]: row["name"] for row in connection.execute("SELECT * FROM library_folders")}
    assert len(real) == 12
    assert synthetic
    assert all(folders[row["folder_id"]] == "真实开放作品" for row in real)
    assert all(folders[row["folder_id"]] == "功能演示" for row in synthetic)
    assert all(row["source_url"] and row["license_name"] and row["source_sha256"] for row in real)
