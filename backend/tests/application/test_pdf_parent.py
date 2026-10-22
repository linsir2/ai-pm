"""PDF 的**父块**：命中子块 → 喂给模型的是**同一页的完整内容**（批 16 · R5）。

## 为什么只对 PDF

PDF 的块按**段落**切：实测真 PDF（11 页）中位块只 **34 字**，**完整句只占 24%**
→ 喂给模型的证据是一堆残片。同一页的段落合起来才是**一段完整的话**
（10 个父块 / 中位 105 字 / 完整句 **100%**）。

**docx / xlsx 不做**：它们的块**已经是结构单元**（一节一块 / 一表一块），
父块=自己（实测体积比 1.00×）→ 零增量。

## 三条口径

1. **父块替代碎片，不是追加** → 同样预算下**覆盖同样的页数、字数一样**；
2. **父块不进索引、不参与打分** → **检索指标一个字都不变**（子块检索、父块返回）；
3. **编号与锚点仍指子块** → 引用链不变（用户点开还是原来那一段）。
"""

from pmstudio.application.context import (
    _material_block_cost,
    parent_text,
    render_materials,
)
from pmstudio.application.dto import MaterialChunk, MaterialPacket, ParsedMaterial


def _pdf_packet() -> MaterialPacket:
    """一份 PDF 材料的形状：两页，每页两段（锚点带页号）。"""
    chunks = (
        MaterialChunk(chunk_id="m1#c00", ordinal=0, text="第一页第一段。",
                      heading_path=(), anchor="第 1 页（第 1 段）"),
        MaterialChunk(chunk_id="m1#c01", ordinal=1, text="第一页第二段。",
                      heading_path=(), anchor="第 1 页（第 2 段）"),
        MaterialChunk(chunk_id="m1#c02", ordinal=2, text="第二页第一段。",
                      heading_path=(), anchor="第 2 页（第 1 段）"),
    )
    return MaterialPacket(
        material_id="m1", project_id="p1", created_at="2026-10-21T00:00:00+00:00",
        parsed=ParsedMaterial(
            origin="upload", title="规范.pdf", source_ref="attachment:规范.pdf",
            media_type="application/pdf", bytes_len=100, chunks=chunks,
            parser_id="pdfminer", parser_version="pdfminer", truncated=False, warnings=(),
        ),
    )


def _docx_packet() -> MaterialPacket:
    """docx 的形状：块**已经是结构单元**（一节一块）→ 父块=自己。"""
    chunks = (
        MaterialChunk(chunk_id="m2#c00", ordinal=0, text="第一节正文。",
                      heading_path=("# 第一节",), anchor="第 1–3 段"),
        MaterialChunk(chunk_id="m2#c01", ordinal=1, text="第二节正文。",
                      heading_path=("# 第二节",), anchor="第 4–6 段"),
    )
    return MaterialPacket(
        material_id="m2", project_id="p1", created_at="2026-10-21T00:00:00+00:00",
        parsed=ParsedMaterial(
            origin="upload", title="手册.docx", source_ref="attachment:手册.docx",
            media_type="application/vnd.openxmlformats-officedocument"
                       ".wordprocessingml.document",
            bytes_len=100, chunks=chunks, parser_id="python-docx",
            parser_version="python-docx", truncated=False, warnings=(),
        ),
    )


def test_a_pdf_hit_is_rendered_with_its_whole_page():
    """R：命中 PDF 的一个子块 → 喂给模型的是**同一页的所有段**（完整的一段话）。"""
    packet = _pdf_packet()

    parent = parent_text(packet, packet.parsed.chunks[0])

    assert parent == "第一页第一段。\n第一页第二段。", parent
    assert parent_text(packet, packet.parsed.chunks[2]) == "第二页第一段。"


def test_the_number_and_the_anchor_still_point_at_the_child():
    """R：编号与锚点**仍指子块**（引用链不变：用户点开还是原来那一段）。"""
    packet = _pdf_packet()

    rendered = render_materials([(packet, {"m1#c01": "E1"})])

    assert "[E1] 第 1 页（第 2 段）" in rendered.text
    assert "第一页第一段。" in rendered.text, "父块内容没进提示词"
    assert rendered.used_chunk_ids == ("m1#c01",)


def test_the_cost_counts_the_parent_not_the_child():
    """R：成本按**父块**算（与渲染同源）——否则成本少算 3 倍，预算失真。"""
    packet = _pdf_packet()

    child = packet.parsed.chunks[0]
    parent = _material_block_cost(packet, child)

    assert parent > len(child.text), "成本没算父块"


def test_a_docx_material_is_not_given_a_parent():
    """R：docx **不做父块**（块已经是结构单元 → 父块=自己，逐字不变）。"""
    packet = _docx_packet()

    assert parent_text(packet, packet.parsed.chunks[0]) == "第一节正文。"


def test_the_same_page_is_rendered_once_with_the_numbers_merged():
    """R：同一页的多个块 → **只渲染一次**，编号**合并**写在一行。

    为什么：不合并的话，同一页被选中 3 次 → 整页渲染 3 次 → 上下文全是同一段；
    而合并后如果只留一个编号，另两个编号就"在账本里、提示词里没出现"
    → "每个编号都有消费者"那条不变量就破了（`02` §11.0.30）。
    """
    packet = _pdf_packet()

    rendered = render_materials(
        [(packet, {"m1#c00": "E1", "m1#c01": "E2", "m1#c02": "E3"})]
    )

    assert "[E1][E2] 第 1 页（第 1 段）" in rendered.text, rendered.text
    assert rendered.text.count("本规范") == 0
    assert rendered.text.count("第一页第一段。") == 1, "同一页渲染了不止一次"
    assert rendered.used_chunk_ids == ("m1#c00", "m1#c01", "m1#c02")
