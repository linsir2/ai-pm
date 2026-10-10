"""材料解析器：文本 / PDF / HTML / Word / Excel 五条真路径，外加"抽不出来就说抽不出来"。

PDF 那份 fixture 是**手写的真 PDF 字节**（不是替身）——pdfminer 真读它。
"""

import io

import openpyxl
import pytest
from docx import Document as open_docx

from pmstudio.application.errors import UnsupportedMaterial
from pmstudio.infrastructure.tools.extract import (
    CHUNK_CHARS,
    HTML_EXTRACT_REVISION,
    MAX_MATERIAL_CHARS,
    LocalMaterialParser,
    chunk_pages,
    chunk_text,
    html_parser_version,
)

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

parser = LocalMaterialParser()

#: 1500 字、**没有换行也没有句读**的一行——专门打"无处可切只能硬切"那一档
WIDE_LINE = "导出权限" * 300
#: 60 行真实文本，每行约 30 字——打"按行切"那一档（不许从句子中间斩）
MANY_LINES = "\n".join(
    f"第 {index} 句关于权限设计的完整论述，句子有标点结尾。" for index in range(60)
)


def _docx_with(body: str) -> bytes:
    """一份最小真 docx：一个一级标题 + 一段正文。"""

    document = open_docx()
    document.add_heading("导出权限", level=1)
    document.add_paragraph(body)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _xlsx_with(cell: str) -> bytes:
    """一份最小真 xlsx：一个工作表，一行两列，第一列超长。"""

    workbook = openpyxl.Workbook()
    workbook.active.title = "明细"
    workbook.active.append([cell, "备注"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


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


def test_a_utf8_file_is_read_with_no_encoding_note():
    parsed = parser.parse(
        filename="需求.md", media_type="text/markdown", data="# 目标\n导出要有权限".encode()
    )

    assert parsed.chunks[0].text == "# 目标\n导出要有权限"
    assert parsed.warnings == ()          # UTF-8 是本分，不许拿"我猜了编码"烦用户


def test_a_gbk_file_is_read_and_says_which_encoding_it_used():
    """GBK 是国内最常见的遗留编码：读得对，而且要**照实说**自己按 GB18030 兜的底。

    改之前这里是 `decode("utf-8", errors="replace")`——中文全变乱码、`warnings` 是空的。
    读错比读不了更坏：读不了会 422，读错会静默污染提示词。
    """

    parsed = parser.parse(
        filename="legacy.txt",
        media_type="text/plain",
        data="权限系统：谁能看报表？\n管理员可以，普通用户不行。\n".encode("gbk"),
    )

    assert parsed.chunks[0].text == "权限系统：谁能看报表？\n管理员可以，普通用户不行。"
    assert any("GB18030" in warning for warning in parsed.warnings)


def test_a_utf8_bom_is_not_left_in_the_text():
    payload = "# 目标\n导出".encode("utf-8-sig")

    parsed = parser.parse(filename="bom.txt", media_type="text/plain", data=payload)

    assert parsed.chunks[0].text.startswith("# 目标")
    assert not parsed.chunks[0].text.startswith("\ufeff")


def test_bytes_no_known_encoding_can_read_are_refused_not_corrupted():
    """两种编码都解不动 → 明确的拒绝。**不许**再退回 `errors="replace"`。"""

    with pytest.raises(UnsupportedMaterial) as error:
        parser.parse(filename="坏.txt", media_type="text/plain", data=b"\xff\xfe\x00\x81")

    assert "UTF-8" in str(error.value) and "GB18030" in str(error.value)


def test_bad_bytes_are_refused_not_silently_replaced():
    """（原 `test_bad_bytes_do_not_explode`，**断言被有意改写**——见下）

    原断言是 `"ok" in parsed.chunks[0].text`，它钉住的正是本次要修的缺陷：
    `errors="replace"` 把 `b"\\xff\\xfe ok"` 读成 `"\\ufffd\\ufffd ok"`，"ok" 确实在里面，
    于是"字节坏了"这件事一个字都不告诉用户。测试的**本意**（坏字节不许炸成 500）保留——
    现在炸出来的是一个明确的 `UnsupportedMaterial`，比原来的静默乱码严格更强。
    除这一条外，本轮没有改动任何既有测试的断言。
    """

    with pytest.raises(UnsupportedMaterial) as error:
        parser.parse(filename="a.txt", media_type="text/plain", data=b"\xff\xfe ok")

    assert error.value.code == "unsupported_material"


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


# ── 网页正文抽取（`02 §11.0.53`）───────────────────────────────────────
#
# `html_text` 的契约是"HTML → 正文"，而旧实现只是"剥 script/style 后 get_text"——两者
# 不等价：导航、侧栏、页脚全算正文（实测典型博客页噪音 70+ 字 vs 正文 60 字）。
# 两级：先剥语义确定的站点噪音标签，再用 readability 从剩下的树里挑主内容。


def test_a_web_page_keeps_the_article_and_drops_the_site_chrome():
    html = (
        "<html><head><title>权限设计指南</title></head><body>"
        "<nav>首页 产品 文档 博客 关于我们 登录 注册 免费试用</nav>"
        "<div class='sidebar'>相关阅读 产品更新 社区 帮助中心</div>"
        "<article><h1>权限设计指南</h1><p>角色与权限的映射应该单向："
        "一个用户属于多个角色，权限从角色汇集。</p></article>"
        "<footer>版权所有 © 2026 备案号 京ICP备00000000</footer>"
        "</body></html>"
    ).encode()

    parsed = parser.parse(filename="page.html", media_type="text/html", data=html)

    text = "\n".join(chunk.text for chunk in parsed.chunks)
    assert "角色与权限的映射应该单向" in text
    assert "首页 产品 文档 博客" not in text        # 站点导航不是正文
    assert "版权所有" not in text                    # 页脚不是正文


def test_a_menu_div_without_semantic_tags_stays():
    """没有语义标签的站点（裸 `div.menu`）**剥不掉**——这是已知边界，不是缺陷。

    主内容抽取（readability 那一级）试过又撤了：三个真实页面增益只有 1–6%，而它会静默
    整块丢掉"它认为不是正文"的节点。噪声的大头（nav/aside/footer）已经被语义标签拿走，
    剩下这点由选块粗排补（命中本轮输入的块排在前面）——同一个动作不请两把刀。
    这条测试钉的就是这个取舍的**后果**：裸 div 菜单会留在正文里。
    """

    html = (
        "<html><head><title>会议纪要</title></head><body><div class='wrap'>"
        "<div class='menu'><a href='/a'>首页</a><a href='/b'>产品</a></div>"
        "<div class='content'><p>今天定了三件事：一、导出按角色；二、季度复盘改到月底；"
        "三、下版不做审批流。另外关于权限模型的收敛方案，还要再约一次评审对齐口径。</p></div>"
        "</div></body></html>"
    ).encode()

    parsed = parser.parse(filename="m.html", media_type="text/html", data=html)

    text = "\n".join(chunk.text for chunk in parsed.chunks)
    assert "今天定了三件事" in text          # 正文一个字都没丢
    assert "首页" in text                    # 裸 div 菜单也还在——宁留不删


def test_the_html_identity_moved_so_old_materials_are_flagged_for_reparse():
    """换了正文抽取算法，解析器身份必须跟着变——否则新旧材料两种质量且界面上看不出区别。

    `needs_reparse`（`dto.py`）的判据就是这一条。原始字节都还在，用户点一次"重解析"即受益。
    """

    version = html_parser_version()

    assert "beautifulsoup4" in version
    assert f"bs4-{HTML_EXTRACT_REVISION}" in version   # 档位在，下次换算法必须 +1


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


# ── 块宽不变式（`02 §11.0.53`）───────────────────────────────────────────
#
# `limits.py` 说 `CHUNK_CHARS` 是"一个块的目标宽度"、extract 与 office "两边必须是同一个数"。
# 那句话一度是假的：Excel 一个工作表一块（只判 `MAX_MATERIAL_CHARS`）、`chunk_text` 只在遇到
# 新行时判宽（单个长行永不被切）。后果不是"块比较大"：`select_chunks` 第一个块就超预算 →
# 选中 0 块 → 提示词写"这一轮没有外部材料"（用户勾了材料却拿到空）。
# 所以这里钉**不变式本身**：五条真路径，一个都不许超宽。


def test_no_parser_path_emits_a_block_wider_than_chunk_chars():
    payloads = [
        ("长行.txt", "text/plain", WIDE_LINE.encode()),
        ("长行.html", "text/html", f"<html><body><p>{WIDE_LINE}</p></body></html>".encode()),
        ("长行.pdf", "application/pdf", minimal_pdf("word " * 600)),
        ("长行.docx", DOCX_MEDIA_TYPE, _docx_with(WIDE_LINE)),
        ("长行.xlsx", XLSX_MEDIA_TYPE, _xlsx_with(WIDE_LINE)),
    ]

    for filename, media_type, data in payloads:
        parsed = parser.parse(filename=filename, media_type=media_type, data=data)

        assert all(len(chunk.text) <= CHUNK_CHARS for chunk in parsed.chunks), filename
        # 一条 1500 字的输入只切成一块，等于没切
        assert len(parsed.chunks) > 1, filename


def test_splitting_prefers_a_line_boundary_over_a_hard_cut():
    """有换行可切的时候，切点必须落在换行处——不是从一句话中间硬斩。"""

    parsed = parser.parse(filename="多行.txt", media_type="text/plain", data=MANY_LINES.encode())

    assert len(parsed.chunks) > 1
    assert all(len(chunk.text) <= CHUNK_CHARS for chunk in parsed.chunks)
    for chunk in parsed.chunks:
        # 每块最后一行仍是完整一句（带句号），没有被腰斩
        assert chunk.text.split("\n")[-1].endswith("。"), chunk.anchor
