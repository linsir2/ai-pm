"""章节槽：s8 的六个固定子块（骨架，不是内容）。"""

from pmstudio.domain.shared.ids import BlockId
from pmstudio.domain.shared.sections import SECTION_SLOTS, section_slot, slot_block_id
from pmstudio.domain.shared.writing import BlockKind


def test_s8_has_six_nfr_slots():
    slots = SECTION_SLOTS["s8"]
    assert [slot.name for slot in slots] == [
        "performance",
        "availability",
        "security",
        "compatibility",
        "observability",
        "cost",
    ]
    assert all(slot.kind is BlockKind.NFR for slot in slots)
    assert [slot.title for slot in slots][:2] == ["性能", "可用性"]
    assert slot_block_id("s8", "performance") == BlockId("s8.performance")


def test_other_sections_have_no_slots():
    assert section_slot("s1", "performance") is None
    assert section_slot("s8", "不存在") is None
    assert section_slot("s8", "performance") is SECTION_SLOTS["s8"][0]
