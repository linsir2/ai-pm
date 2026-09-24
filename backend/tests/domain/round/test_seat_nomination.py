"""席位提名是领域事实：名单来自 Analyzer 的判断，要能去重、要能落库、要能回放。

它和 `seats`（用户最终选的那批）是两件事：提名是"建议谁来"，seats 是"这一轮真跑谁"。
"""

from datetime import datetime

import pytest

from pmstudio.domain.round.round import Round, SeatNomination
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import ProjectId, RoundId

NOW = datetime(2026, 9, 24, 10, 0, 0)


def make_round() -> Round:
    return Round.start(
        round_id=RoundId("r1"),
        project_id=ProjectId("p1"),
        user_input="想做个订单导出的功能",
        now=NOW,
    )


def test_nominate_keeps_order_and_drops_repeats():
    round_ = make_round()
    round_.nominate(
        [
            SeatNomination(seat_id="seat.export_import", why="你说到导出"),
            SeatNomination(seat_id="seat.permissions", why="导出涉及数据外流"),
            SeatNomination(seat_id="seat.export_import", why="重复提一次，留第一条"),
        ]
    )

    assert [item.seat_id for item in round_.nominated_seats] == [
        "seat.export_import",
        "seat.permissions",
    ]
    assert round_.nominated_seats[0].why == "你说到导出"


def test_nomination_requires_a_reason():
    with pytest.raises(ShapeError):
        SeatNomination(seat_id="seat.export_import", why="   ")


def test_nomination_requires_a_seat():
    with pytest.raises(ShapeError):
        SeatNomination(seat_id="", why="总得有个理由")


def test_empty_nomination_is_refused():
    round_ = make_round()
    with pytest.raises(ShapeError):
        round_.nominate([])


def test_nomination_does_not_move_the_round():
    """提名只记名单：轮次仍然停在"等你纠正复述"那一步。"""

    round_ = make_round()
    before = (round_.step, round_.waiting_on)
    round_.nominate([SeatNomination(seat_id="seat.permissions", why="涉及权限")])
    assert (round_.step, round_.waiting_on) == before


def test_nomination_is_not_the_lineup():
    """提名 ≠ 选中：R7 看的仍然是用户提交的那批 `seats`，名单只是建议。"""

    round_ = make_round()
    round_.nominate([SeatNomination(seat_id="seat.permissions", why="涉及权限")])
    assert round_.seats == ()
