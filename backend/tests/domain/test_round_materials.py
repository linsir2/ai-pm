"""轮次记住"这一轮带进了哪些材料块"（工具层 C-2）。

只记**引用**（material_id / chunk_id / eid）——块正文在材料表里，别在轮次 payload 里放第二份。
"""

from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.infrastructure.sqlite.serialization import round_from_payload, round_to_payload
from tests.support.builders import NOW, PROJECT, ROUND_ID, make_round


def round_with_materials() -> Round:
    round_ = make_round()
    round_.add_materials(
        (
            MaterialRef(material_id="m1", chunk_id="m1#c00", eid=EvidenceId("E9")),
            MaterialRef(material_id="m1", chunk_id="m1#c01", eid=EvidenceId("E10")),
        )
    )
    return round_


def test_materials_are_recorded_in_order():
    round_ = round_with_materials()

    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00", "m1#c01"]
    assert str(round_.materials[0].eid) == "E9"


def test_a_round_without_materials_has_none():
    assert make_round().materials == ()


def test_payload_roundtrip_keeps_materials_and_old_rows_fall_back():
    round_ = round_with_materials()

    restored = round_from_payload(
        round_to_payload(round_),
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert [ref.chunk_id for ref in restored.materials] == ["m1#c00", "m1#c01"]

    old = round_to_payload(round_)
    del old["materials"]        # 老行：没有这把键
    restored_old = round_from_payload(
        old,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert restored_old.materials == ()


def test_material_refs_need_a_chunk_and_an_eid():
    import pytest

    from pmstudio.domain.shared.errors import ShapeError

    with pytest.raises(ShapeError):
        MaterialRef(material_id="m1", chunk_id="", eid=EvidenceId("E9"))

    with pytest.raises(ShapeError):
        Round.start(
            round_id=RoundId("r9"), project_id=PROJECT, user_input="x", now=NOW
        ).add_materials(())
