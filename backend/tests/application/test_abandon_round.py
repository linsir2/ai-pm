"""放弃这一轮（`01` F15 / `02` §7）：先把状态写下来，再把正在跑的那个任务取消掉。

取消之所以安全：模型调用一律在事务外（`test_no_await_in_uow.py` 守着的那条），
所以 `CancelledError` 只可能落在 `await` 点，不会落在 `with uow:` 中间——
不会出现"写了一半的文档或账本"。
"""

import asyncio
import json

from pmstudio.application.commands import RoundCommand
from pmstudio.domain.round.step import Step
from tests.support.builders import PROJECT, SEAT
from tests.support.doubles import HangingSeatGateway
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "导出要快",
                "raw_span": "导出要快一点",
                "label": "constraint",
                "zone": "body",
                "section_id": "s8",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)


async def test_abandon_cancels_the_running_action_and_marks_the_round():
    harness = Harness(
        replies={},
        seat_replies={},
        gateway=HangingSeatGateway(replies={"claims": CLAIMS_REPLY}),
    )
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="导出要快一点")
    await start.analyze(round_id=round_id)

    runner = harness.runner()
    running = asyncio.create_task(
        runner.run(
            RoundCommand(kind="submit_opening", round_id=str(round_id), seats=(SEAT,))
        )
    )
    await asyncio.sleep(0.05)  # 让它跑到席位那次 await（挂住）

    view = await harness.abandon_round()(round_id=round_id)
    outcome = (await asyncio.gather(running, return_exceptions=True))[0]

    assert view.step == "abandoned"
    assert view.abandon_reason == "用户放弃了这一轮"
    assert harness.rounds.get(round_id).step is Step.ABANDONED
    assert isinstance(outcome, asyncio.CancelledError)  # 取消**不吞**：交回调用者
    assert harness.uow.rollbacks == 0  # 没有写了一半的事务


async def test_abandon_works_when_nothing_is_running():
    """进程崩过、或停在等人：没有在跑的任务也要能放弃（不然那一轮永远占着）。"""

    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="导出要快一点")

    view = await harness.abandon_round()(round_id=round_id)

    assert view.step == "abandoned"
    assert view.abandon_reason == "用户放弃了这一轮"


async def test_abandon_is_idempotent_and_blocks_further_actions():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="导出要快一点")
    use_case = harness.abandon_round()

    first = await use_case(round_id=round_id, reason="不想等了")
    second = await use_case(round_id=round_id, reason="又说了一次")

    assert first.abandon_reason == "不想等了"
    assert second.abandon_reason == "不想等了"

    # 真实路径：界面上那个"继续/重试"按钮（走图的 resume 路由）不许把放弃的轮复活。
    # （不直接调 `start.analyze`：它是图节点，绕过路由表调用不是真实路径。）
    await harness.runner().run(RoundCommand(kind="retry", round_id=str(round_id)))
    assert harness.rounds.get(round_id).step is Step.ABANDONED


async def test_abandon_publishes_round_updated():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="导出要快一点")

    await harness.abandon_round()(round_id=round_id)

    # `create` 自己也会发一次 round.updated：看最后一条
    assert harness.events.names[-1] == "round.updated"
    assert harness.events.published[-1].payload["step"] == "abandoned"
