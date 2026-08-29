"""Build an ignored private corpus database through the public application APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "real_corpus_manifest.json"


def load_works() -> list[dict[str, object]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["works"]


def build_database(database: Path, corpus: Path, estimate_provider: str | None = None) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    main.settings = replace(main.settings, database_path=database,
        deepseek_api_key="estimate-only" if estimate_provider == "deepseek" else None,
        moonshot_api_key="estimate-only" if estimate_provider == "moonshot" else None)
    works = load_works()
    with TestClient(main.app) as client:
        folders = client.get("/api/library/folders").json()
        real_folder = next((item for item in folders if item["name"] == "真实开放作品"), None)
        if real_folder is None:
            response = client.post("/api/library/folders", json={"name": "真实开放作品", "parent_id": None})
            response.raise_for_status()
            real_folder = response.json()
        demo_folder = next((item for item in folders if item["name"] == "功能演示"), None)
        if demo_folder is None:
            response = client.post("/api/library/folders", json={"name": "功能演示", "parent_id": None})
            response.raise_for_status()
            demo_folder = response.json()
        for book in client.get("/api/books").json():
            if book.get("corpus_kind") == "synthetic":
                client.patch(f"/api/books/{book['id']}", json={"folder_id": demo_folder["id"], "corpus_kind": "synthetic", "rights_status": "synthetic"}).raise_for_status()

        existing = {item["original_filename"]: item for item in client.get("/api/books").json()}
        for work in works:
            filename = str(work["local_filename"])
            path = corpus / filename
            if not path.exists():
                raise FileNotFoundError(path)
            book = existing.get(filename)
            if book is None:
                mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                with path.open("rb") as source:
                    response = client.post("/api/books/import", data={"folder_id": str(real_folder["id"])}, files={"file": (filename, source, mime_type)})
                response.raise_for_status()
                book = response.json()
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            response = client.patch(f"/api/books/{book['id']}", json={
                "title": work["title"], "author": work["author"], "folder_id": real_folder["id"],
                "language": work["language"], "corpus_kind": "open_real", "license_name": work["license_name"],
                "source_url": work["source_url"], "rights_status": work["rights_status"], "source_sha256": digest})
            response.raise_for_status()
        stored = [item for item in client.get("/api/books").json() if item.get("corpus_kind") == "open_real"]
        if len(stored) != len(works):
            raise RuntimeError(f"private corpus database contains {len(stored)} of {len(works)} declared open works")
        for item in stored:
            if not item["source_url"] or not item["license_name"] or not item["language"] or not item["source_sha256"]:
                raise RuntimeError(f"missing source metadata for {item['title']}")
            print(f"{item['title']}\t{item['segment_count']}\t{item['character_count']}\t{item['language']}\t{item['license_name']}")
        if estimate_provider:
            for item in stored:
                end_segment = min(int(item["segment_count"]) - 1, 11)
                response = client.post(f"/api/books/{item['id']}/jobs/estimate", json={
                    "provider": estimate_provider, "start_segment": 0, "end_segment": end_segment, "max_retries": 2,
                    "reanalyze": False, "max_cost_usd": 1_000, "max_input_tokens": 500_000_000,
                    "max_output_tokens": 100_000_000, "review_mode": "local", "budget_mode": "adaptive"})
                response.raise_for_status()
                estimate = response.json()
                print(f"ESTIMATE\t{item['title']}\t{estimate['segment_count']}\t{estimate['estimated_cost_usd']}")


def main_entry() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/e2e-real-v292.db"))
    parser.add_argument("--corpus", type=Path, default=Path("data/samples"))
    parser.add_argument("--estimate-provider", choices=("deepseek", "moonshot", "codex_luna"))
    args = parser.parse_args()
    build_database(args.database.resolve(), args.corpus.resolve(), args.estimate_provider)


if __name__ == "__main__":
    main_entry()
