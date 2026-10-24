"""模拟调研/评测的材料节：按 query 选块到预算（P2.7b）。

主链路已经有 `RoundContextPreparer` 的每步投影；这两个旁路只需要证明
"`Round.materials` 长大以后，提示词不会跟着涨"。
"""

from pmstudio.application.context import MATERIAL_BUDGET
from pmstudio.application.use_cases import external_material_text
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.round import MaterialRef
from tests.application.test_materials import make_material
from tests.support.builders import NOW, PROJECT, make_round
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
