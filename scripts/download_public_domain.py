"""Download the declared open corpus into the ignored local sample directory."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "real_corpus_manifest.json"
MAX_BYTES = 20 * 1024 * 1024


def load_works() -> list[dict[str, object]]:
    works = json.loads(MANIFEST.read_text(encoding="utf-8")).get("works", [])
    if len(works) != 12:
        raise RuntimeError(f"expected 12 declared works, found {len(works)}")
    return works


def _looks_valid(filename: str, content: bytes) -> bool:
    lower = filename.lower()
    if lower.endswith(".epub"):
        return content.startswith(b"PK") and len(content) > 20_000
    if lower.endswith((".html", ".htm")):
        return b"<html" in content[:5_000].lower() and len(content) > 20_000
    return len(content) > 20_000 and b"<html" not in content[:2_000].lower()


def download_corpus(output_dir: Path, force: bool = False) -> list[dict[str, object]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, object]] = []
    with httpx.Client(follow_redirects=True, timeout=90.0) as client:
        for work in load_works():
            output_path = output_dir / str(work["local_filename"])
            if output_path.exists() and not force:
                content = output_path.read_bytes()
            else:
                response = client.get(str(work["download_url"]), headers={"User-Agent": "ExampleOrg-NovelAtlas/2.9.2 open-corpus-qa"})
                response.raise_for_status()
                content = response.content
                if len(content) > MAX_BYTES:
                    raise RuntimeError(f"{output_path.name} exceeds the 20 MB corpus limit")
                output_path.write_bytes(content)
            if not _looks_valid(output_path.name, content):
                raise RuntimeError(f"{output_path.name} did not match its declared document format")
            digest = hashlib.sha256(content).hexdigest()
            results.append({"id": work["id"], "path": str(output_path), "bytes": len(content), "sha256": digest})
            print(f"saved={output_path} bytes={len(content)} sha256={digest}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "samples")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    download_corpus(args.output.resolve(), force=args.force)


if __name__ == "__main__":
    main()
