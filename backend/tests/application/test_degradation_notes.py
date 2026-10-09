"""降级要照实说：`use_cases` 里两处"静默截断"的回归。

- `_render_list_items`：只带前 10 条，要说清一共几条；
- `external_material_text`：材料已不在库里时不静默 `continue`，要说清少了几份。
"""

from pmstudio.application.use_cases import _render_list_items, external_material_text
from pmstudio.domain.round.questionnaire import ListItem
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId
from pmstudio.domain.shared.target import Target
from tests.support.builders import NOW, SEAT, make_claim


def _round_with_list_items(count: int) -> Round:
    round_ = Round.start(
        round_id=RoundId("r1"), project_id=ProjectId("p1"), user_input="测试", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.remember_list_items(
        tuple(
            ListItem(text=f"清单{i}", target=Target.body("s11"), reason="题位满了")
            for i in range(count)
        )
    )
    return round_


def test_render_list_items_notes_the_omitted_ones() -> None:
    round_ = _round_with_list_items(14)

    text = _render_list_items(round_)

    assert "清单0" in text
    assert "清单9" in text
    assert "清单10" not in text
    assert "共 14 条" in text
    assert "只带了前 10 条" in text


def test_render_list_items_no_note_when_under_limit() -> None:
    assert "只带了前" not in _render_list_items(_round_with_list_items(4))


class _MissingStore:
    """材料表里查不到的仓储替身（材料被删后的真实形状）。"""

    def get(self, material_id):  # noqa: ANN001
        return None


def test_external_material_text_notes_deleted_materials() -> None:
    round_ = Round.start(
        round_id=RoundId("r1"), project_id=ProjectId("p1"), user_input="测试", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.add_materials(
        (MaterialRef(material_id="m1", chunk_id="m1#c0", eid=EvidenceId("E5")),)
    )

    text = external_material_text(round_, store=_MissingStore())

    assert "1 份材料已不在库里" in text
    assert "未带入" in text
