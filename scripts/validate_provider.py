"""用隐藏输入的开放平台密钥验证单个虚构片段。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
from dataclasses import replace
from pathlib import Path

from app.config import load_settings
from app.importers import parse_book
from app.pipeline import find_quote
from app.providers import create_provider


SAMPLE = """雾港的潮钟敲过六响，陆昭在旧灯塔收到林雪送来的守灯司密令。
密令写道：黑潮将在三日后越过归潮渡。林雪把黑曜罗盘交给陆昭，陆昭答应与林雪同行。"""


async def validate(provider_name: str, source_file: Path | None, segment_ordinal: int, show_missing: bool) -> None:
    """只打印结构数量和令牌用量，不打印密钥或完整模型输出。"""

    secret = getpass.getpass(f"请输入 {provider_name} 开放平台密钥：")
    settings = load_settings()
    if provider_name == "deepseek":
        settings = replace(settings, deepseek_api_key=secret)
    else:
        settings = replace(settings, moonshot_api_key=secret)
    provider = create_provider(settings, provider_name)
    if source_file is None:
        chapter_title = "第一章 雾港来信"
        source_text = SAMPLE
        ordinal = 0
    else:
        parsed = parse_book(source_file.name, source_file.read_bytes())
        try:
            segment = parsed.segments[segment_ordinal]
        except IndexError as exc:
            raise RuntimeError("指定片段不存在。") from exc
        chapter_title = segment.chapter_title
        source_text = segment.text
        ordinal = segment.ordinal
    response = await provider.extract(chapter_title, ordinal, source_text)
    result = response.extraction
    candidates = [
        *result.entities,
        *result.relations,
        *result.events,
        *result.world_notes,
        *result.entries,
    ]
    exact_evidence = sum(1 for candidate in candidates if candidate.evidence_quote.strip() in source_text)
    aligned_evidence = sum(1 for candidate in candidates if find_quote(source_text, candidate.evidence_quote) is not None)
    print(
        json.dumps(
            {
                "provider": provider.name,
                "model": provider.model,
                "entities": len(result.entities),
                "relations": len(result.relations),
                "events": len(result.events),
                "world_notes": len(result.world_notes),
                "entries": len(result.entries),
                "candidates": len(candidates),
                "exact_evidence": exact_evidence,
                "aligned_evidence": aligned_evidence,
                "source_characters": len(source_text),
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            },
            ensure_ascii=False,
        )
    )
    if show_missing:
        missing = [
            {
                "type": candidate.__class__.__name__,
                "label": getattr(candidate, "name", None)
                or getattr(candidate, "title", None)
                or getattr(candidate, "predicate", None),
                "quote": candidate.evidence_quote,
            }
            for candidate in candidates
            if find_quote(source_text, candidate.evidence_quote) is None
        ]
        print(json.dumps(missing, ensure_ascii=True))


def main() -> None:
    """解析供应商名称并运行一次受控验证。"""

    parser = argparse.ArgumentParser(description="验证小说抽取供应商")
    parser.add_argument("provider", choices=["deepseek", "moonshot"])
    parser.add_argument("--file", type=Path, help="可选的 TXT、Markdown 或 EPUB 样例")
    parser.add_argument("--segment", type=int, default=0, help="样例中的片段序号")
    parser.add_argument("--show-missing", action="store_true", help="显示无法逐字定位的公版引文")
    arguments = parser.parse_args()
    asyncio.run(validate(arguments.provider, arguments.file, arguments.segment, arguments.show_missing))


if __name__ == "__main__":
    main()
