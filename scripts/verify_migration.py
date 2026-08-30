"""在数据库副本上验证版本升级，不修改正在使用的正式数据库。"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

from app.db import initialize


CONTROL_TABLES = {
    "product_contracts",
    "collaboration_items",
    "prompt_bundle_versions",
    "domain_rules",
    "external_facts",
    "run_manifests",
    "model_routes",
    "model_race_runs",
}


def verify_migration(source: Path, destination: Path) -> dict[str, object]:
    """复制旧数据库、执行迁移并核对数据量、结构和 SQLite 完整性。"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    with sqlite3.connect(destination) as connection:
        books_before = int(connection.execute("SELECT COUNT(*) FROM books").fetchone()[0])
    initialize(destination)
    with sqlite3.connect(destination) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        evidence_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(evidence)")
        }
        books_after = int(connection.execute("SELECT COUNT(*) FROM books").fetchone()[0])
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    return {
        "source": str(source.resolve()),
        "copy": str(destination.resolve()),
        "books_before": books_before,
        "books_after": books_after,
        "integrity": integrity,
        "control_tables_present": sorted(CONTROL_TABLES & tables),
        "all_control_tables_present": CONTROL_TABLES <= tables,
        "evidence_lineage_present": {"run_manifest_id", "model_call_id"} <= evidence_columns,
        "passed": (
            books_before == books_after
            and integrity == "ok"
            and CONTROL_TABLES <= tables
            and {"run_manifest_id", "model_call_id"} <= evidence_columns
        ),
    }


def main() -> None:
    """读取命令行路径并以 JSON 输出可存档的迁移报告。"""

    parser = argparse.ArgumentParser(description="在数据库副本上验证 Novel Atlas 迁移")
    parser.add_argument("source", type=Path, help="只读的旧数据库或备份文件")
    parser.add_argument("destination", type=Path, help="用于迁移验证的新副本路径")
    arguments = parser.parse_args()
    report = verify_migration(arguments.source, arguments.destination)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
