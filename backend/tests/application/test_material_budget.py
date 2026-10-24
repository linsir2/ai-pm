"""模拟调研/评测的材料节：按 query 选块到预算（P2.7b）。

主链路已经有 `RoundContextPreparer` 的每步投影；这两个旁路只需要证明
"`Round.materials` 长大以后，提示词不会跟着涨"。
"""

from pmstudio.application.context import (
    MATERIAL_BUDGET,
    WEB_BUDGET,
    WEB_PARTIAL_NOTE,
    WEB_SECTION_OVERHEAD,
    render_web_results,
    select_chunks,
    web_block_cost,
)
from pmstudio.application.dto import MaterialChunk, MaterialPacket, ParsedMaterial
from pmstudio.application.use_cases import external_material_text
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.round import MaterialRef
from pmstudio.domain.shared.ids import EvidenceId
from tests.application.test_materials import make_material
from tests.support.builders import (
    NOW,
    PROJECT,
    make_material_packet,
    make_round,
)
from tests.support.harness import Harness


def _round_with_material(packet) -> tuple[Harness, object]:
    harness = Harness(replies={})
    harness.seed_project()
    harness.materials.seed(packet)
    round_ = make_round()
    ledger = harness.evidences.get(PROJECT)
    refs = []
    for chunk in packet.parsed.chunks:
        eid = ledger.record(
            kind=EvidenceKind.EXTERNAL,
            text=chunk.text,
            source=EvidenceSource(
                external_ref=f"attachment:需求.md#c{chunk.ordinal:02d}"
            ),
            now=NOW,
        )
        refs.append(MaterialRef(packet.material_id, chunk.chunk_id, eid))
    round_.add_materials(tuple(refs))
    harness.evidences.save(ledger)
    return harness, round_


def test_a_query_caps_the_material_section_and_says_it_truncated():
    packet = make_material(
        *[f"第 {index} 段" * 20 for index in range(100)], material_id="m1"
    )
    harness, round_ = _round_with_material(packet)

    text = external_material_text(
        round_, store=harness.materials, query="第 1 段"
    )

    assert len(text) <= MATERIAL_BUDGET
    assert "只带了" in text, "选块后要照实说没带全"


def test_a_query_that_selects_nothing_does_not_claim_there_is_no_material():
    packet = make_material("x" * 5000, material_id="m1")
    harness, round_ = _round_with_material(packet)

    text = external_material_text(round_, store=harness.materials, query="x")

    assert "本步没有选中" in text
    assert "这一轮没有外部材料" not in text


def test_without_a_query_the_old_rendering_is_unchanged():
    packet = make_material("短材料", material_id="m1")
    harness, round_ = _round_with_material(packet)

    text = external_material_text(round_, store=harness.materials)

    assert "短材料" in text
    assert "本步没有选中" not in text


def test_without_a_query_still_caps_and_says_the_pool_may_have_more():
    packet = make_material(
        *[f"第 {index} 段" * 50 for index in range(100)], material_id="m1"
    )
    harness, round_ = _round_with_material(packet)

    text = external_material_text(round_, store=harness.materials)

    assert len(text) <= MATERIAL_BUDGET
    assert "本轮带了" in text
    assert "没带进来的" in text


def test_the_web_section_respects_its_budget():
    from pmstudio.application.context import WEB_BUDGET, render_web_results
    from pmstudio.domain.shared.ids import EvidenceId

    packet = make_material("联网正文" * 240, material_id="w1")
    entries = [
        (packet, [packet.parsed.chunks[0]], EvidenceId(f"E{index}"))
        for index in range(1, 4)
    ]

    text = render_web_results(entries).text

    assert len(text) <= WEB_BUDGET


def _pdf_packet(
    *, pages: int = 10, per_page: int = 40, size: int = 100, material_id: str = "m1"
) -> MaterialPacket:
    chunks = tuple(
        MaterialChunk(
            chunk_id=f"{material_id}#c{page * per_page + index:03d}",
            ordinal=page * per_page + index,
            text="字" * size,
            heading_path=(),
            anchor=f"第 {page + 1} 页（第 {index + 1} 段）",
        )
        for page in range(pages)
        for index in range(per_page)
    )
    return MaterialPacket(
        material_id=material_id,
        project_id=str(PROJECT),
        parsed=ParsedMaterial(
            origin="upload",
            title="需求.pdf",
            source_ref="attachment:需求.pdf",
            media_type="application/pdf",
            bytes_len=size * len(chunks),
            chunks=chunks,
            parser_id="pdf",
            parser_version="v1",
        ),
        created_at=NOW,
    )


def test_a_pdf_parent_page_cannot_escape_the_budget_when_only_one_child_is_numbered():
    """PDF 的一页会作为父块整页回喂；成本若只算子块，选块 10 块 → 渲染 400 块。"""

    packet = _pdf_packet()
    harness = Harness(replies={})
    harness.seed_project()
    harness.materials.seed(packet)
    round_ = make_round()
    ledger = harness.evidences.get(PROJECT)
    refs = []
    for page in range(10):
        chunk = packet.parsed.chunks[page * 40]
        eid = ledger.record(
            kind=EvidenceKind.EXTERNAL,
            text=chunk.text,
            source=EvidenceSource(external_ref=f"attachment:需求.pdf#{chunk.ordinal}"),
            now=NOW,
        )
        refs.append(MaterialRef(packet.material_id, chunk.chunk_id, eid))
    round_.add_materials(refs)
    harness.evidences.save(ledger)

    text = external_material_text(round_, store=harness.materials)

    assert len(text) <= MATERIAL_BUDGET, f"渲染 {len(text)} 字 > {MATERIAL_BUDGET}"


def test_web_selection_and_render_share_one_budget():
    """选块成本漏算换行时，渲染会丢掉已选中的编号（账本有、模型看不到）。"""

    packets = [
        make_material_packet(
            *(["段" * 12] * 13),
            title="t",
            source_ref="u",
            material_id=f"w{index}",
            origin="web_search",
        )
        for index in range(11)
    ]
    picked, _ = select_chunks(
        packets,
        budget=WEB_BUDGET,
        cost=web_block_cost,
        section_overhead=WEB_SECTION_OVERHEAD,
    )
    entries = []
    for index, packet in enumerate(packets):
        chunks = [chunk for material_id, chunk in picked if material_id == packet.material_id]
        if chunks:
            entries.append((packet, chunks, EvidenceId(f"E{index + 1}")))

    rendered = render_web_results(entries)

    assert len(rendered.text) <= WEB_BUDGET, f"渲染 {len(rendered.text)} 字 > {WEB_BUDGET}"
    assert {chunk.chunk_id for _, chunk in picked} <= set(rendered.used_chunk_ids), (
        "选中的块必须全部渲染出来"
    )


def test_a_partial_web_result_is_said_out():
    packet = make_material_packet(
        "甲", "乙", "丙", material_id="w1", origin="web_search", title="t", source_ref="u"
    )

    rendered = render_web_results(
        [(packet, [packet.parsed.chunks[0]], EvidenceId("E1"))]
    )

    assert WEB_PARTIAL_NOTE in rendered.text
    assert len(rendered.text) <= WEB_BUDGET
