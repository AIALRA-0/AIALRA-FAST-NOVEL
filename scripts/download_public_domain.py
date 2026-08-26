"""下载固定来源的公版中文长篇，用于本地导入与基准测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx


SOURCES = (
    ("https://www.gutenberg.org/cache/epub/23962/pg23962.txt", Path("data/samples/pg23962-xiyouji.txt")),
    ("https://www.gutenberg.org/cache/epub/24264/pg24264.txt", Path("data/samples/pg24264-hongloumeng.txt")),
    ("https://www.gutenberg.org/cache/epub/23863/pg23863.txt", Path("data/samples/pg23863-shuihuzhuan.txt")),
    # 两部叙事结构不同的公版作品补足真实评估语料覆盖，下载后仍须人工确认金标准。
    ("https://www.gutenberg.org/cache/epub/23950/pg23950.txt", Path("data/samples/pg23950-sanguo.txt")),
    ("https://www.gutenberg.org/cache/epub/51828/pg51828.txt", Path("data/samples/pg51828-liaozhai.txt")),
)
MAX_BYTES = 10 * 1024 * 1024


def main() -> None:
    """限制下载体积并打印可复核的内容哈希。"""

    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        for source_url, output_path in SOURCES:
            response = client.get(source_url, headers={"User-Agent": "NovelAtlas/2.2 public-domain-test"})
            response.raise_for_status()
            content = response.content
            if len(content) > MAX_BYTES:
                raise RuntimeError(f"{output_path.name} 下载内容超过 10 MB 限制。")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            print(f"saved={output_path} bytes={len(content)} sha256={digest}")


if __name__ == "__main__":
    main()
