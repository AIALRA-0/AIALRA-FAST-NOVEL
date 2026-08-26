"""验证常见文本编码、章节切分与 EPUB 安全边界。"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from app.importers import ImportErrorDetail, parse_book


def make_epub(extra_files: dict[str, bytes] | None = None) -> bytes:
    """构造只包含一个正文文件的最小 EPUB。"""

    buffer = BytesIO()
    container = b'''<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
      <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>'''
    package = '''<?xml version="1.0" encoding="UTF-8"?>
    <package xmlns="http://www.idpf.org/2007/opf" version="3.0">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title><dc:creator>测试作者</dc:creator></metadata>
      <manifest><item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
      <spine><itemref idref="c1"/></spine>
    </package>'''.encode("utf-8")
    chapter = "<html><body><h1>第一章</h1><p>陆昭抵达雾港。</p><script>恶意脚本</script></body></html>".encode()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", package)
        archive.writestr("OEBPS/chapter.xhtml", chapter)
        for name, content in (extra_files or {}).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def make_docx() -> bytes:
    """构造包含标题属性和两段正文的最小 DOCX。"""

    buffer = BytesIO()
    document = '''<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>第一章 起程</w:t></w:r></w:p>
      <w:p><w:r><w:t>陆昭离开雾港。</w:t></w:r></w:p>
    </w:body></w:document>'''.encode("utf-8")
    properties = '''<?xml version="1.0" encoding="UTF-8"?>
    <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:title>文档小说</dc:title><dc:creator>测试作者</dc:creator>
    </cp:coreProperties>'''.encode("utf-8")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("docProps/core.xml", properties)
    return buffer.getvalue()


def test_txt_decoding_and_chapter_split() -> None:
    """GB18030 文本应保留两个章节和中文内容。"""

    content = "第一章 起程\n\n陆昭离开。\n第二章 抵达\n\n陆昭到了雾港。".encode("gb18030")
    parsed = parse_book("长篇.txt", content)
    assert parsed.source_type == "txt"
    assert len(parsed.segments) == 2
    assert "雾港" in parsed.segments[1].text
    assert parsed.segments[0].anchor.startswith("seg-0-")


def test_gutenberg_wrapper_is_removed_before_chapter_split() -> None:
    """公版站点的授权页眉不能占用小说的第一个分析片段。"""

    content = """Project Gutenberg header
*** START OF THE PROJECT GUTENBERG EBOOK 测试书 ***
第一回 起程

陆昭离开雾港。
*** END OF THE PROJECT GUTENBERG EBOOK 测试书 ***
Project Gutenberg footer""".encode("utf-8")
    parsed = parse_book("公版小说.txt", content)
    assert len(parsed.segments) == 1
    assert parsed.segments[0].chapter_title == "第一回 起程"
    assert "Project Gutenberg" not in parsed.segments[0].text


def test_gutenberg_credit_after_start_marker_is_removed() -> None:
    """正文标记后的制作者署名也不能变成卷首分析片段。"""

    content = """The Project Gutenberg eBook of 示例
*** START OF THE PROJECT GUTENBERG EBOOK 示例 ***

Produced by Example Volunteer

第一回 真正正文开始

人物从城南走到城北。

*** END OF THE PROJECT GUTENBERG EBOOK 示例 ***
license
""".encode("utf-8")
    parsed = parse_book("示例.txt", content)
    assert parsed.segments[0].chapter_title == "第一回 真正正文开始"
    assert "Produced by" not in parsed.segments[0].text


def test_html_and_docx_import_visible_text() -> None:
    """HTML 和 DOCX 会提取可见正文，并保留文档元数据。"""

    html = parse_book("网页小说.html", "<h1>第一章</h1><p>陆昭抵达。</p><script>忽略我</script>".encode("utf-8"))
    assert html.source_type == "html"
    assert "忽略我" not in html.segments[0].text
    docx = parse_book("文档小说.docx", make_docx())
    assert docx.title == "文档小说"
    assert docx.author == "测试作者"
    assert "陆昭离开雾港" in docx.segments[0].text


def test_classical_circle_numeral_chapter() -> None:
    """公版古籍常用第一○回等写法，章节识别需要覆盖空心圆数字。"""

    content = "第一○回 旧事\n\n往事一段。\n第一一回 新章\n\n新事一段。".encode("utf-8")
    parsed = parse_book("古籍.txt", content)
    assert len(parsed.segments) == 2
    assert parsed.segments[0].chapter_title.startswith("第一○回")


def test_epub_uses_spine_and_drops_script() -> None:
    """EPUB 正文按 spine 读取，脚本文字不会进入证据文本。"""

    parsed = parse_book("test.epub", make_epub())
    assert parsed.title == "测试书"
    assert parsed.author == "测试作者"
    assert "陆昭抵达雾港" in parsed.segments[0].text
    assert "恶意脚本" not in parsed.segments[0].text


def test_epub_rejects_path_traversal() -> None:
    """容器成员不能越过 EPUB 根目录。"""

    with pytest.raises(ImportErrorDetail, match="越界路径"):
        parse_book("bad.epub", make_epub({"../outside.txt": b"x"}))


def test_epub_rejects_encryption() -> None:
    """首版明确拒绝加密和受数字版权管理保护的书。"""

    with pytest.raises(ImportErrorDetail, match="不处理加密"):
        parse_book("locked.epub", make_epub({"META-INF/encryption.xml": b"<encryption/>"}))


def test_rejects_unknown_extension() -> None:
    """扩展名白名单阻止任意文件进入解析器。"""

    with pytest.raises(ImportErrorDetail, match="只支持"):
        parse_book("novel.mobi", b"not a mobi")


def test_rejects_broken_pdf() -> None:
    """声明为 PDF 的损坏文件会返回可读错误。"""

    with pytest.raises(ImportErrorDetail, match="PDF 结构"):
        parse_book("novel.pdf", b"not a pdf")
