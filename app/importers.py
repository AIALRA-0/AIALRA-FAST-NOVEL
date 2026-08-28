"""安全读取 TXT、Markdown 与 EPUB，并生成稳定原文片段。"""

from __future__ import annotations

import hashlib
import html
import posixpath
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree

from pypdf import PdfReader


CHAPTER_PATTERN = re.compile(
    r"(?m)^\s*((?:第[零〇○一二两三四五六七八九十百千万0-9]+[章节回卷部篇])[^\n]{0,80}|(?:chapter|part)\s+\d+[^\n]{0,80})\s*$",
    re.IGNORECASE,
)
MAX_EPUB_FILES = 2_000
MAX_EPUB_UNCOMPRESSED = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
SEGMENT_TARGET = 4_500


class ImportErrorDetail(ValueError):
    """向接口提供安全且可读的导入错误。"""


@dataclass(frozen=True)
class ParsedSegment:
    """数据库写入前的原文片段。"""

    ordinal: int
    chapter_title: str
    anchor: str
    text: str
    char_start: int
    char_end: int


@dataclass(frozen=True)
class ParsedBook:
    """规范化后的整本书。"""

    title: str
    author: str
    source_type: str
    source_hash: str
    original_filename: str
    segments: list[ParsedSegment]
    character_count: int


class TextExtractor(HTMLParser):
    """只提取可见文本，忽略脚本、样式和外部资源。"""

    block_tags = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "br", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0
        self.heading = ""
        self._heading_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in {"script", "style", "svg", "iframe", "object"}:
            self.ignored_depth += 1
        if self.ignored_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")
        if self.ignored_depth == 0 and tag in {"h1", "h2"}:
            self._heading_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "iframe", "object"} and self.ignored_depth:
            self.ignored_depth -= 1
        if self.ignored_depth == 0 and tag in self.block_tags:
            self.parts.append("\n")
        if tag in {"h1", "h2"} and self._heading_depth:
            self._heading_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        cleaned = html.unescape(data).strip()
        if cleaned:
            self.parts.append(cleaned)
            if self._heading_depth and not self.heading:
                self.heading = cleaned[:120]

    def get_text(self) -> str:
        """合并块级文本并压缩多余空行。"""

        return normalize_text("".join(self.parts))


def normalize_text(text: str) -> str:
    """统一换行与空白，同时保留段落边界。"""

    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode_text(content: bytes) -> str:
    """按常见中文文本编码依次解码。"""

    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ImportErrorDetail("文本编码无法识别，请转换为 UTF-8 或 GB18030。")


def strip_gutenberg_wrapper(text: str) -> str:
    """移除 Project Gutenberg 公开文本的授权页眉和页脚。"""

    start = re.search(r"(?im)^\*{3}\s*START OF THE PROJECT GUTENBERG EBOOK[^\n]*\*{3}\s*$", text)
    end = re.search(r"(?im)^\*{3}\s*END OF THE PROJECT GUTENBERG EBOOK[^\n]*\*{3}\s*$", text)
    if start is None or end is None or end.start() <= start.end():
        return text
    body = text[start.end() : end.start()].strip()

    # Older Gutenberg exports may add a legacy English end line immediately before
    # the modern starred marker. It is licensing wrapper text, not novel evidence.
    legacy_end = re.search(r"(?im)^\s*End of Project Gutenberg(?:'s)?[^\n]*\s*$", body)
    if legacy_end is not None and legacy_end.start() >= max(0, len(body) - 1_000):
        body = body[: legacy_end.start()].rstrip()

    # 一些 Gutenberg 文本会在正文标记之后再次写入制作者署名；只在首章前的短前缀中清除它。
    first_chapter = CHAPTER_PATTERN.search(body)
    if first_chapter is not None and first_chapter.start() < 1_000:
        prefix = body[: first_chapter.start()]
        if re.search(r"(?im)^\s*(?:produced|prepared|transcribed)\s+by\b", prefix):
            body = body[first_chapter.start() :]
    return body.strip()


def safe_xml(content: bytes, label: str) -> ElementTree.Element:
    """拒绝含文档类型或实体声明的 XML，避免实体扩展攻击。"""

    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ImportErrorDetail(f"{label} 含有不允许的 XML 实体声明。")
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ImportErrorDetail(f"{label} 结构无法解析。") from exc


def safe_member_name(name: str) -> str:
    """验证 ZIP 成员路径始终留在 EPUB 容器内。"""

    normalized = posixpath.normpath(name.replace("\\", "/"))
    if normalized.startswith("../") or normalized.startswith("/") or ":" in normalized:
        raise ImportErrorDetail("EPUB 含有越界路径。")
    return normalized


def validate_epub_archive(archive: zipfile.ZipFile) -> None:
    """限制文件数、展开体积和异常压缩比。"""

    infos = archive.infolist()
    if len(infos) > MAX_EPUB_FILES:
        raise ImportErrorDetail("EPUB 内部文件过多。")
    total = 0
    for info in infos:
        safe_member_name(info.filename)
        total += info.file_size
        if total > MAX_EPUB_UNCOMPRESSED:
            raise ImportErrorDetail("EPUB 展开后体积超过 200 MB。")
        if info.file_size > 1_000_000 and info.compress_size > 0:
            ratio = info.file_size / info.compress_size
            if ratio > MAX_COMPRESSION_RATIO:
                raise ImportErrorDetail("EPUB 含有异常压缩文件。")
    names = {safe_member_name(info.filename) for info in infos}
    if "META-INF/encryption.xml" in names:
        raise ImportErrorDetail("当前版本不处理加密或受数字版权管理保护的 EPUB。")


def parse_epub(content: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    """按 EPUB spine 顺序返回书名、作者和章节文本。"""

    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ImportErrorDetail("EPUB 容器已损坏。") from exc

    with archive:
        validate_epub_archive(archive)
        names = {safe_member_name(info.filename) for info in archive.infolist()}
        if "META-INF/container.xml" not in names:
            raise ImportErrorDetail("EPUB 缺少 META-INF/container.xml。")
        container = safe_xml(archive.read("META-INF/container.xml"), "EPUB 容器")
        rootfile = next((node for node in container.iter() if node.tag.endswith("rootfile")), None)
        if rootfile is None or not rootfile.attrib.get("full-path"):
            raise ImportErrorDetail("EPUB 没有声明内容清单。")
        opf_path = safe_member_name(rootfile.attrib["full-path"])
        if opf_path not in names:
            raise ImportErrorDetail("EPUB 内容清单不存在。")
        package = safe_xml(archive.read(opf_path), "EPUB 内容清单")
        metadata = next((node for node in package.iter() if node.tag.endswith("metadata")), None)
        title = "未命名作品"
        author = ""
        if metadata is not None:
            title_node = next((node for node in metadata.iter() if node.tag.endswith("title")), None)
            creator_node = next((node for node in metadata.iter() if node.tag.endswith("creator")), None)
            title = (title_node.text or "").strip() if title_node is not None else title
            author = (creator_node.text or "").strip() if creator_node is not None else author
        manifest: dict[str, str] = {}
        spine: list[str] = []
        for node in package.iter():
            if node.tag.endswith("item") and node.attrib.get("id") and node.attrib.get("href"):
                media_type = node.attrib.get("media-type", "")
                if media_type in {"application/xhtml+xml", "text/html"}:
                    manifest[node.attrib["id"]] = node.attrib["href"].split("#", 1)[0]
            elif node.tag.endswith("itemref") and node.attrib.get("idref"):
                spine.append(node.attrib["idref"])
        base = posixpath.dirname(opf_path)
        chapters: list[tuple[str, str]] = []
        for index, item_id in enumerate(spine, start=1):
            href = manifest.get(item_id)
            if not href or re.match(r"^[a-z]+://", href, re.IGNORECASE):
                continue
            member = safe_member_name(posixpath.join(base, href))
            if member not in names:
                continue
            parser = TextExtractor()
            parser.feed(decode_text(archive.read(member)))
            text = parser.get_text()
            if text:
                chapters.append((parser.heading or f"第 {index} 节", text))
        if not chapters:
            raise ImportErrorDetail("EPUB 没有可读取的正文。")
        return title or "未命名作品", author, chapters


def parse_html_document(content: bytes) -> tuple[str, list[tuple[str, str]]]:
    """静态提取 HTML 可见文字，不加载脚本和外部资源。"""

    parser = TextExtractor()
    parser.feed(decode_text(content))
    text = parser.get_text()
    if not text:
        raise ImportErrorDetail("HTML 没有可读取的正文。")
    return parser.heading or "未命名作品", split_plain_text(text)


def parse_docx(content: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    """从 DOCX 压缩包中只读取正文 XML 和文档属性。"""

    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ImportErrorDetail("DOCX 容器已损坏。") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_EPUB_FILES:
            raise ImportErrorDetail("DOCX 内部文件过多。")
        total = 0
        names: set[str] = set()
        for info in infos:
            name = safe_member_name(info.filename)
            names.add(name)
            total += info.file_size
            if total > MAX_EPUB_UNCOMPRESSED:
                raise ImportErrorDetail("DOCX 展开后体积超过 200 MB。")
            if info.file_size > 1_000_000 and info.compress_size > 0:
                if info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
                    raise ImportErrorDetail("DOCX 含有异常压缩文件。")
        if "word/document.xml" not in names:
            raise ImportErrorDetail("DOCX 缺少正文。")
        document = safe_xml(archive.read("word/document.xml"), "DOCX 正文")
        paragraphs: list[str] = []
        for node in document.iter():
            if not node.tag.endswith("}p"):
                continue
            text = "".join(part.text or "" for part in node.iter() if part.tag.endswith("}t")).strip()
            if text:
                paragraphs.append(text)
        body = normalize_text("\n\n".join(paragraphs))
        if not body:
            raise ImportErrorDetail("DOCX 没有可读取的正文文字。")
        title = "未命名作品"
        author = ""
        if "docProps/core.xml" in names:
            properties = safe_xml(archive.read("docProps/core.xml"), "DOCX 文档属性")
            for node in properties.iter():
                if node.tag.endswith("}title") and (node.text or "").strip():
                    title = (node.text or "").strip()
                elif node.tag.endswith("}creator") and (node.text or "").strip():
                    author = (node.text or "").strip()
        return title, author, split_plain_text(body)


def parse_pdf(content: bytes) -> tuple[str, str, list[tuple[str, str]]]:
    """按页提取 PDF 文字，扫描版 PDF 会明确提示缺少文字层。"""

    try:
        reader = PdfReader(BytesIO(content), strict=False)
    except Exception as exc:
        raise ImportErrorDetail("PDF 结构无法解析。") from exc
    if reader.is_encrypted:
        raise ImportErrorDetail("当前版本不处理加密 PDF。")
    if len(reader.pages) > 5_000:
        raise ImportErrorDetail("PDF 页数超过 5000 页的安全限制。")
    chapters: list[tuple[str, str]] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = normalize_text(page.extract_text() or "")
        except Exception:
            text = ""
        if text:
            chapters.append((f"第 {index} 页", text))
    if not chapters:
        raise ImportErrorDetail("PDF 没有可提取的文字层，请先进行文字识别。")
    metadata = reader.metadata or {}
    title = str(metadata.get("/Title") or "未命名作品").strip()
    author = str(metadata.get("/Author") or "").strip()
    return title, author, chapters


def split_plain_text(text: str) -> list[tuple[str, str]]:
    """用章节标题切分普通文本；没有标题时保留为一个章节。"""

    text = normalize_text(text)
    matches = list(CHAPTER_PATTERN.finditer(text))
    if not matches:
        return [("正文", text)]
    chapters: list[tuple[str, str]] = []
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        chapters.append(("卷首", text[: matches[0].start()].strip()))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chapter_text = text[match.start() : end].strip()
        if chapter_text:
            chapters.append((match.group(1).strip(), chapter_text))
    return chapters


def chunk_chapters(chapters: list[tuple[str, str]]) -> list[ParsedSegment]:
    """按段落形成稳定、无重叠的证据片段。"""

    result: list[ParsedSegment] = []
    cursor = 0
    ordinal = 0
    for chapter_title, chapter_text in chapters:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n|(?<=。)\n", chapter_text) if part.strip()]
        if not paragraphs:
            paragraphs = [chapter_text]
        buffer = ""
        for paragraph in paragraphs:
            while len(paragraph) > SEGMENT_TARGET:
                if buffer:
                    result.append(_make_segment(ordinal, chapter_title, buffer, cursor))
                    cursor += len(buffer) + 1
                    ordinal += 1
                    buffer = ""
                piece, paragraph = paragraph[:SEGMENT_TARGET], paragraph[SEGMENT_TARGET:]
                result.append(_make_segment(ordinal, chapter_title, piece, cursor))
                cursor += len(piece) + 1
                ordinal += 1
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) > SEGMENT_TARGET and buffer:
                result.append(_make_segment(ordinal, chapter_title, buffer, cursor))
                cursor += len(buffer) + 1
                ordinal += 1
                buffer = paragraph
            else:
                buffer = candidate
        if buffer:
            result.append(_make_segment(ordinal, chapter_title, buffer, cursor))
            cursor += len(buffer) + 1
            ordinal += 1
    return result


def _make_segment(ordinal: int, chapter_title: str, text: str, char_start: int) -> ParsedSegment:
    """使用内容哈希生成可复算锚点。"""

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return ParsedSegment(
        ordinal=ordinal,
        chapter_title=chapter_title[:160],
        anchor=f"seg-{ordinal}-{digest}",
        text=text,
        char_start=char_start,
        char_end=char_start + len(text),
    )


def parse_book(filename: str, content: bytes) -> ParsedBook:
    """根据扩展名解析并返回规范化书籍。"""

    safe_filename = PurePosixPath(filename.replace("\\", "/")).name
    extension = safe_filename.lower().rsplit(".", 1)[-1] if "." in safe_filename else ""
    source_hash = hashlib.sha256(content).hexdigest()
    if extension == "epub":
        title, author, chapters = parse_epub(content)
        source_type = "epub"
    elif extension in {"txt", "md", "markdown"}:
        text = strip_gutenberg_wrapper(decode_text(content))
        title = safe_filename.rsplit(".", 1)[0] or "未命名作品"
        author = ""
        chapters = split_plain_text(text)
        source_type = "markdown" if extension in {"md", "markdown"} else "txt"
    elif extension in {"html", "htm"}:
        title, chapters = parse_html_document(content)
        author = ""
        source_type = "html"
    elif extension == "docx":
        title, author, chapters = parse_docx(content)
        if title == "未命名作品":
            title = safe_filename.rsplit(".", 1)[0] or title
        source_type = "docx"
    elif extension == "pdf":
        title, author, chapters = parse_pdf(content)
        if title == "未命名作品":
            title = safe_filename.rsplit(".", 1)[0] or title
        source_type = "pdf"
    else:
        raise ImportErrorDetail("只支持 TXT、Markdown、EPUB、HTML、DOCX 和带文字层的 PDF 文件。")
    segments = chunk_chapters(chapters)
    if not segments:
        raise ImportErrorDetail("文件没有可读取的正文。")
    return ParsedBook(
        title=title,
        author=author,
        source_type=source_type,
        source_hash=source_hash,
        original_filename=safe_filename,
        segments=segments,
        character_count=sum(len(item.text) for item in segments),
    )
