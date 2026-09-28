"""留痕要多一个入口。

`Round.integrate(dropped=…)` 是整合那一步的入口；席位轮（超限截断）与 claims
（目录外的席位提名）都需要记留痕，但它们都不在整合那一步。
"""

from pmstudio.domain.round.round import DroppedItem, DropSource
from tests.support.builders import make_round


def make_item(handle: str = "seat.data_scale:O9") -> DroppedItem:
    return DroppedItem(
        handle=handle,
        reason="超出单席位上限 12 条，第 9 条起未进卡片",
        source=DropSource.ASSEMBLY,
        seat_id="seat.data_scale",
        pass_index=1,
    )


def test_note_dropped_appends_without_touching_step():
    round_ = make_round()
    before_step, before_waiting = round_.step, round_.waiting_on

    round_.note_dropped([make_item()])

    assert [item.handle for item in round_.dropped] == ["seat.data_scale:O9"]
    assert round_.step is before_step
    assert round_.waiting_on is before_waiting


def test_note_dropped_is_additive():
    """留痕是历史记录，不是集合：同一件事发生两次就该有两条。"""

    round_ = make_round()

    round_.note_dropped([make_item("席位提名 seat.observability")])
    round_.note_dropped([make_item("席位提名 seat.observability")])

    assert len(round_.dropped) == 2
