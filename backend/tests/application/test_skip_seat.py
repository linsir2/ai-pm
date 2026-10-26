"""用户主动跳过席位（新端点 + 界面入口）。

## 与自动跳过的分界

编排层自动跳过（`§11.0.67`：坏席位两次内容错 → 记名跳过）已经在了，这里测的是
**用户手动**的那一半：轮跑起来之后，用户在闭环区对还没交回的席位点"跳过"。
跑之前拿掉已有复述卡减法，不归这里管。

## 并发正确性（第 6/7/8 条的前提）

跳过只写 `round_skips` 控制行，不拿 action lease、不写 Round；交回仍在 `with uow`
里 fresh-read + save。两边都在 `SqliteUnitOfWork` 的 `BEGIN IMMEDIATE` 下串行：
- skip 先提交 → 交回 fresh-read 到控制行合入的 skipped → 丢弃 + 留痕；
- 交回先提交 → skip 读到当趟 review → 拒绝（"已交回则拒"不是 UX 选项，是正确性必需）。
"""

import asyncio

import pytest

from pmstudio.application.errors import SeatAlreadyReviewed
from pmstudio.domain.round.round import DropSource
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.errors import IllegalStepTransition, UnknownSeat

# 回复脚本不自造：`SEAT_REPLY` / `UNPARSABLE_SEAT_REPLY` / `claims_reply` 全是
# `test_seat_round` 里验证过的形状（跨测试 import 有先例，见 `test_database_busy`）。
from tests.application.test_seat_round import (  # noqa: E402
    SEAT_REPLY,
    SECOND_SEAT,
    UNPARSABLE_SEAT_REPLY,
    claims_reply,
)
from tests.support.builders import PROJECT, SEAT, make_seat
from tests.support.doubles import ScriptedModelGateway
from tests.support.harness import Harness


async def running_round(harness: Harness):
    """建轮 + 落主张 + 开场确认，停在席位轮跑起来（`step == seats_running`）。"""

    use_case = harness.start_round()
    round_id = await use_case.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await use_case.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))
    return round_id


class _HangOnceGateway(ScriptedModelGateway):
    """第一次席位调用挂住（等放行），之后照脚本回。只活在测试里。

    用来 deterministic 地造出竞态的两序：任务先起 → 用户跳过 → 任务再结束。
    `HangingSeatGateway` 不可用：它放行后抛 `AssertionError`（走 fatal 整轮失败），
    而这里要的是内容错/正常交回两种结局。
    """

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.release = asyncio.Event()
        self._hung_once = False

    async def complete(self, request):  # noqa: ANN001, ANN202
        if request.purpose == "seat_review" and not self._hung_once:
            self._hung_once = True
            await self.release.wait()
        return await super().complete(request)


def _harness(**kwargs):
    return Harness(
        replies={"claims": claims_reply()},
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
        **kwargs,
    )


# ── R1/R2：守门复用 ──────────────────────────────────────────────────────────


async def test_非跑中调跳过必须拒():
    """R：只有跑中才能跳——`skip_seat` 的 `_require_step(SEATS_RUNNING)` 就是守门，
    不另写 step 检查。刚建好的轮（STARTED）调必须拒。"""

    harness = _harness()
    harness.seed_project()
    use_case = harness.start_round()
    round_id = await use_case.create(project_id=PROJECT, user_input="想做个订单导出的功能")

    with pytest.raises(IllegalStepTransition):
        await harness.skip_seat()(round_id=round_id, seat_id=SEAT)


async def test_未知席位必须拒():
    """R：`UnknownSeat`（R7）复用——"不在本轮编制里"，一字不动。"""

    harness = _harness()
    harness.seed_project()
    round_id = await running_round(harness)

    with pytest.raises(UnknownSeat):
        await harness.skip_seat()(round_id=round_id, seat_id="seat.nope")


# ── R3/R4/R5：主路径 ─────────────────────────────────────────────────────────


async def test_已交回的跳过必须拒():
    """R：交回先提交、跳过后到 → skip 的 uow 读到当趟 review → 拒。
    这不是 UX 选项，是正确性必需：收了会导致"review 落库且席位被标跳过"，不变式破。"""

    harness = _harness(seat_replies={SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY})
    harness.seed_project()
    round_id = await running_round(harness)
    await harness.run_seat_round()(round_id=round_id)

    with pytest.raises(SeatAlreadyReviewed):
        await harness.skip_seat()(round_id=round_id, seat_id=SEAT)

    round_ = harness.rounds.get(round_id)
    assert round_.skipped_seats == ()
    assert len(round_.current_reviews) == 2


async def test_重复跳过幂等且不重复留痕():
    """R：第二次是空操作——控制行 latest-wins，DTO 只合成一条 USER_SKIPPED。"""

    harness = _harness()
    harness.seed_project()
    round_id = await running_round(harness)

    await harness.skip_seat()(round_id=round_id, seat_id=SEAT)
    view = await harness.skip_seat()(round_id=round_id, seat_id=SEAT)

    round_ = harness.rounds.get(round_id)
    assert round_.skipped_seats == (SEAT,)
    notes = [item for item in view.dropped if item.seat_id == SEAT]
    assert len(notes) == 1
    assert notes[0].source == "user_skipped"
    assert view.step == "seats_running"


async def test_正常跳过记名留痕且不再请():
    """R：三件套——skipped 有名、留痕 1 条（来源 USER_SKIPPED）、pending 排除。
    view 里该包 status 必须是 skipped（前端第三态的数据源）。"""

    harness = _harness()
    harness.seed_project()
    round_id = await running_round(harness)

    view = await harness.skip_seat()(round_id=round_id, seat_id=SEAT)

    round_ = harness.rounds.get(round_id)
    assert round_.skipped_seats == (SEAT,)
    assert SEAT not in round_.pending_seats
    assert SECOND_SEAT in round_.pending_seats
    notes = [item for item in view.dropped if item.seat_id == SEAT]
    assert len(notes) == 1
    assert notes[0].source == "user_skipped"
    assert notes[0].handle == SEAT
    assert round_.dropped == ()
    packet = next(p for p in view.seat_packets if p.seat_id == SEAT)
    assert packet.status == "skipped"
    other = next(p for p in view.seat_packets if p.seat_id == SECOND_SEAT)
    assert other.status == "pending"


# ── R6/R7：竞态两序 ──────────────────────────────────────────────────────────


async def test_跳过后迟交被丢弃且留痕():
    """R：skip 先提交 → 在飞任务交回时读到 skipped → 不 attest + 记迟交。
    整合无感（`current_reviews` 里没它），但"一条都不许默默丢掉"仍成立。"""

    gateway = _HangOnceGateway(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY},
    )
    harness = _harness(gateway=gateway)
    harness.seed_project()
    round_id = await running_round(harness)

    running = asyncio.create_task(harness.run_seat_round()(round_id=round_id))
    await asyncio.sleep(0.05)  # 让席位任务跑到 hang 点
    await harness.skip_seat()(round_id=round_id, seat_id=SEAT)
    gateway.release.set()
    await running

    round_ = harness.rounds.get(round_id)
    assert [r.seat_id for r in round_.current_reviews] == [SECOND_SEAT]
    view = await harness.get_round()(round_id=round_id)
    notes = [item for item in view.dropped if item.seat_id == SEAT]
    assert len(notes) == 2  # 控制行合成 1 条 + 迟交丢弃 1 条
    assert {item.source for item in notes} == {"user_skipped"}
    late = [item for item in round_.dropped if item.seat_id == SEAT]
    assert len(late) == 1
    assert late[0].source is DropSource.USER_SKIPPED
    assert "跳过之后才交回" in late[0].reason
    assert round_.step is Step.SEATS_RUNNING


async def test_跳过后任务失败不记谎言留痕():
    """R：跳过后在飞任务**失败**（内容错）→ 落进 failures 分支时该席位已在
    skipped 里 → `skip_seat` 幂等无操作，且**不许再记一条**"两次尝试都未能交回"。
    那条 reason 是谎言（它不是两次失败，是用户跳的），且会重复留痕。"""

    gateway = _HangOnceGateway(
        replies={"claims": claims_reply()},
        seat_replies={
            SEAT: [UNPARSABLE_SEAT_REPLY, UNPARSABLE_SEAT_REPLY],
            SECOND_SEAT: SEAT_REPLY,
        },
    )
    harness = _harness(gateway=gateway)
    harness.seed_project()
    round_id = await running_round(harness)

    running = asyncio.create_task(harness.run_seat_round()(round_id=round_id))
    await asyncio.sleep(0.05)
    await harness.skip_seat()(round_id=round_id, seat_id=SEAT)
    gateway.release.set()
    await running  # 不抛：SECOND_SEAT 交回了，整轮继续

    round_ = harness.rounds.get(round_id)
    view = await harness.get_round()(round_id=round_id)
    notes = [item for item in view.dropped if item.seat_id == SEAT]
    assert len(notes) == 1, f"多记了谎言留痕：{[n.reason for n in notes]}"
    assert notes[0].source == "user_skipped"
    assert [item for item in round_.dropped if item.seat_id == SEAT] == []
    assert [r.seat_id for r in round_.current_reviews] == [SECOND_SEAT]


async def test_跳过之后跑起来只请没跳过的():
    """R：`pending_seats` 排除跳过是现货（domain 已有）——resume 只补 SECOND，
    SEAT 的任务再也不起来。钉住这条不退化。"""

    harness = _harness(seat_replies={SEAT: SEAT_REPLY, SECOND_SEAT: SEAT_REPLY})
    harness.seed_project()
    round_id = await running_round(harness)
    await harness.skip_seat()(round_id=round_id, seat_id=SEAT)

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [r.seat_id for r in round_.current_reviews] == [SECOND_SEAT]
