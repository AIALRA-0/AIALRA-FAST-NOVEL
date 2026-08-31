"""Audit deterministic map geometry against private real-work data without mutating it."""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from app.atlas import build_map_layout_snapshot
from app.db import connect


def audit(database_path: Path) -> dict[str, object]:
    """Build fresh snapshots in a disposable database and summarize failures."""

    with tempfile.TemporaryDirectory(prefix="aialra-map-audit-") as directory:
        audit_path = Path(directory) / "audit.db"
        shutil.copy2(database_path, audit_path)
        connection = connect(audit_path)
        try:
            connection.execute("DELETE FROM map_layout_snapshots")
            books = connection.execute(
                """
                SELECT b.id, b.title
                FROM books AS b
                LEFT JOIN library_folders AS f ON f.id = b.folder_id
                WHERE f.name = '真实开放作品'
                ORDER BY b.id
                """
            ).fetchall()
            results: list[dict[str, object]] = []
            for book in books:
                snapshot = build_map_layout_snapshot(connection, int(book["id"]))
                coverage = snapshot["region_coverage"]
                results.append({
                    "book_id": int(book["id"]),
                    "title": str(book["title"]),
                    "places": int(coverage["total_place_count"]),
                    "validation_state": snapshot["validation_state"],
                    "failed_relations": snapshot["failed_relations"],
                    "constraint_summary": snapshot["constraint_summary"],
                    "unassigned": int(coverage["unassigned_place_count"]),
                    "overlap_pairs": int(coverage["overlap"]["same_level_overlap_pairs"]),
                    "maximum_overlap_percent": float(coverage["overlap"]["maximum_overlap_ratio_percent"]),
                    "overlaps": coverage["overlap"]["pairs"],
                })
            return {
                "database": str(database_path),
                "book_count": len(results),
                "overlap_pairs": sum(int(item["overlap_pairs"]) for item in results),
                "unassigned_places": sum(int(item["unassigned"]) for item in results),
                "invalid_books": sum(item["validation_state"] != "valid" for item in results),
                "failed_relations": sum(len(item["failed_relations"]) for item in results),
                "books": results,
            }
        finally:
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/e2e-real-v292.db"))
    arguments = parser.parse_args()
    print(json.dumps(audit(arguments.database), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
