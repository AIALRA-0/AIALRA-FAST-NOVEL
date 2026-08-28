"""Build an ignored local QA database through the public import and library APIs."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main


WORKS = {
    "pg23962-xiyouji.txt": "西游记",
    "pg24264-hongloumeng.txt": "红楼梦",
    "pg23863-shuihuzhuan.txt": "水浒传",
    "pg23950-sanguo.txt": "三国演义",
    "pg51828-liaozhai.txt": "聊斋志异",
}


def build_database(database: Path, corpus: Path, estimate_provider: str | None = None) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    main.settings = replace(
        main.settings,
        database_path=database,
        # The estimate route constructs the provider but never sends a request.
        # Local placeholders keep this QA command independent of stored secrets.
        deepseek_api_key="estimate-only" if estimate_provider == "deepseek" else None,
        moonshot_api_key="estimate-only" if estimate_provider == "moonshot" else None,
    )
    with TestClient(main.app) as client:
        folders = client.get("/api/library/folders").json()
        folder = next((item for item in folders if item["name"] == "真实公版验收"), None)
        if folder is None:
            response = client.post("/api/library/folders", json={"name": "真实公版验收", "parent_id": None})
            response.raise_for_status()
            folder = response.json()
        existing = {item["original_filename"]: item for item in client.get("/api/books").json()}
        for filename, title in WORKS.items():
            path = corpus / filename
            if not path.exists():
                raise FileNotFoundError(path)
            book = existing.get(filename)
            if book is None:
                with path.open("rb") as source:
                    response = client.post(
                        "/api/books/import",
                        data={"folder_id": str(folder["id"])},
                        files={"file": (filename, source, "text/plain")},
                    )
                response.raise_for_status()
                book = response.json()
            response = client.patch(
                f"/api/books/{book['id']}",
                json={"title": title, "folder_id": folder["id"]},
            )
            response.raise_for_status()
        stored = [item for item in client.get("/api/books").json() if item["original_filename"] in WORKS]
        if len(stored) != len(WORKS):
            raise RuntimeError("The local QA database does not contain all five public-domain books")
        for item in stored:
            print(f"{item['title']}\t{item['segment_count']}\t{item['character_count']}")
        if estimate_provider:
            total_input = 0
            total_output = 0
            total_cost = 0.0
            priced = True
            for item in stored:
                response = client.post(
                    f"/api/books/{item['id']}/jobs/estimate",
                    json={
                        "provider": estimate_provider,
                        "start_segment": 0,
                        "end_segment": int(item["segment_count"]) - 1,
                        "max_retries": 2,
                        "reanalyze": False,
                        "max_cost_usd": 1_000,
                        "max_input_tokens": 500_000_000,
                        "max_output_tokens": 100_000_000,
                        "review_mode": "local",
                        "budget_mode": "adaptive",
                    },
                )
                response.raise_for_status()
                estimate = response.json()
                total_input += int(estimate["estimated_input_tokens"])
                total_output += int(estimate["estimated_output_tokens"])
                if estimate["estimated_cost_usd"] is None:
                    priced = False
                else:
                    total_cost += float(estimate["estimated_cost_usd"])
                print(
                    f"ESTIMATE\t{item['title']}\t{estimate['segment_count']}\t"
                    f"{estimate['estimated_input_tokens']}\t{estimate['estimated_output_tokens']}\t"
                    f"{estimate['estimated_cost_usd']}"
                )
            print(f"ESTIMATE_TOTAL\t{estimate_provider}\t{total_input}\t{total_output}\t{total_cost if priced else 'unpriced'}")


def main_entry() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/e2e-real-v28.db"))
    parser.add_argument("--corpus", type=Path, default=Path("data/samples"))
    parser.add_argument("--estimate-provider", choices=("deepseek", "moonshot", "codex_luna"))
    args = parser.parse_args()
    build_database(args.database.resolve(), args.corpus.resolve(), args.estimate_provider)


if __name__ == "__main__":
    main_entry()
