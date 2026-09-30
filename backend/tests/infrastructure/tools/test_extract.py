"""材料解析器：文本 / PDF / HTML 三条真路径，外加"抽不出来就说抽不出来"。

PDF 那份 fixture 是**手写的真 PDF 字节**（不是替身）——pdfminer 真读它。
"""

import pytest

from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.infrastructure.tools.extract import (
    MAX_MATERIAL_CHARS,
    LocalMaterialParser,
    chunk_pages,
    chunk_text,
)

parser = LocalMaterialParser()


def minimal_pdf(text: str) -> bytes:
    """最小可读 PDF：一页，`/Contents` 流里一行字。"""

    body = (
        "%PDF-1.4\n"
        "1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        "3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        "/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
        f"4 0 obj<</Length 44>>stream\nBT /F1 12 Tf 20 100 Td ({text}) Tj ET\n"
        "endstream\nendobj\n"
        "5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
        "trailer<</Root 1 0 R>>\n"
    )
    return body.encode("ascii")


def test_text_file_becomes_a_parsed_material_with_chunks_and_anchors():
    parsed = parser.parse(
        filename="需求.md",
        media_type="text/markdown",
        data="# 目标\n导出要有权限\n\n# 边界\n只给管理员".encode(),
    )

    assert parsed.origin == "upload"
    assert parsed.source_ref == "attachment:需求.md"
    assert parsed.media_type == "text/markdown"
    assert parsed.parser_id == "local-text"
    assert parsed.bytes_len > 0
    assert [chunk.ordinal for chunk in parsed.chunks] == [0, 1]
    assert parsed.chunks[0].text.startswith("# 目标")
    assert parsed.chunks[0].anchor.startswith("第 1")          # 人话锚点，不是裸偏移
    assert parsed.chunks[0].heading_path == ("# 目标",)
    assert parsed.chunks[1].chunk_id.endswith("#c01")
    assert parsed.truncated is False
    assert parsed.warnings == ()


def test_bad_bytes_do_not_explode():
    parsed = parser.parse(filename="a.txt", media_type="text/plain", data=b"\xff\xfe ok")

    assert "ok" in parsed.chunks[0].text


def test_html_drops_script_and_style():
    html = (
        b"<html><script>var x=1</script><style>p{}</style>"
        b"<p># \xe5\xaf\xbc\xe5\x87\xba</p></html>"
    )

    parsed = parser.parse(filename="p.html", media_type="text/html", data=html)

    assert "导出" in parsed.chunks[0].text
    assert "var x" not in parsed.chunks[0].text
    assert "p{}" not in parsed.chunks[0].text
    assert parsed.parser_id == "bs4-html"


def test_pdf_is_extracted_with_page_anchors():
    parsed = parser.parse(
        filename="spec.pdf", media_type="application/pdf", data=minimal_pdf("Hello export")
    )

    assert "Hello export" in parsed.chunks[0].text
    assert parsed.parser_id == "pdfminer"
    assert parsed.chunks[0].anchor.startswith("第 1 页")
    assert parsed.chunks[0].heading_path == ()


def test_unknown_type_is_refused_not_silently_empty():
    with pytest.raises(UnsupportedMaterial) as error:
        parser.parse(filename="scan.jpg", media_type="image/jpeg", data=b"\x89PNG")

    assert error.value.code == "unsupported_material"


def test_a_pdf_without_extractable_text_says_so():
    """扫描件：抽不出文字 ≠ 空材料。"""

    with pytest.raises(UnsupportedMaterial) as error:
        parser.parse(
            filename="scan.pdf", media_type="application/pdf", data=minimal_pdf("   ")
        )

    assert "没有可抽取的文字" in str(error.value)


def test_a_broken_pdf_is_a_clear_refusal_not_a_500():
    """坏文件（半截 PDF / 加密 PDF）：要报"读不了"，不许把 pdfminer 的异常漏成 500。"""

    with pytest.raises(UnsupportedMaterial) as error:
        parser.parse(filename="broken.pdf", media_type="application/pdf", data=b"%PDF-1.4\n1 0 obj")

    assert error.value.code == "unsupported_material"
    assert "读不了" in str(error.value)


def test_a_very_long_material_is_marked_truncated():
    payload = ("字" * (MAX_MATERIAL_CHARS + 500)).encode()
    parsed = parser.parse(
        filename="big.txt", media_type="text/plain", data=payload
    )

    assert parsed.truncated is True
    assert any("截断" in warning for warning in parsed.warnings)
    assert sum(len(chunk.text) for chunk in parsed.chunks) <= MAX_MATERIAL_CHARS + 50


def test_chunk_text_breaks_at_headings_and_keeps_the_stack():
    chunks = chunk_text("# 一\n甲\n## 1.1\n乙\n## 1.2\n丙", parser_id="local-text")

    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]
    assert chunks[1].heading_path == ("# 一", "## 1.1")
    assert chunks[2].heading_path == ("# 一", "## 1.2")


def test_chunk_pages_uses_page_numbers_as_anchors():
    chunks = chunk_pages(["第一页内容", "第二页内容"], parser_id="pdfminer")

    assert [chunk.anchor for chunk in chunks] == ["第 1 页", "第 2 页"]
    assert chunks[1].chunk_id == "pdfminer#c01"


def test_blank_text_yields_no_chunks():
    assert chunk_text("   \n\n  ", parser_id="local-text") == ()
