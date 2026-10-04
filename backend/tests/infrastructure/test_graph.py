import asyncio
import json

import pytest

from pmstudio.application.errors import RoundBusy
from pmstudio.application.use_cases import AnswerInput
from pmstudio.domain.round.step import Step
from pmstudio.harness.errors import ModelCallFailed
from pmstudio.infrastructure.graph.graph import RESUME_ROUTES, RoundCommand
from tests.support.builders import make_round
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "order-export",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "异常与边界",
                "claim": "无导出权限的用户不显示导出入口",
                "because": "订单含客户信息",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)

QUESTION_REPLY = json.dumps(
    {
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数是多少？",
                "why": "量级决定同步还是异步",
                "consequence_if_skipped": "按同步实现",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万–10 万"},
                ],
                "evidence_refs": ["E1"],
                "asked_by": ["seat.data_scale"],
            }
        ],
        "list_items": [],
        "proposals": [],
        # 席位那条判断这一趟没被采用——夹具替它申报掉（`02` §11.0.37 的点名重装就
        # 不会插进来多消耗一次脚本回复，图的那条路径才是这条用例要测的东西）。
        "dropped_opinions": [
            {"handle": "seat.data_scale:O1", "reason": "这一趟先不写，留给下一轮"}
        ],
    },
    ensure_ascii=False,
)

DRAFT_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "exceptions",
                },
                "text": "无导出权限的用户不显示导出入口",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "订单含客户信息",
            }
        ],
        # 同 `QUESTION_REPLY`：夹具把席位那条判断的去向申报掉。
        "dropped_opinions": [
            {"handle": "seat.data_scale:O1", "reason": "与上面那条提案说的是同一件事"}
        ],
    },
    ensure_ascii=False,
)


def harness_for_question_path() -> Harness:
    return Harness(
        replies={"claims": CLAIMS_REPLY, "integration": QUESTION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )


def harness_for_draft_path() -> Harness:
    return Harness(
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )


async def test_graph_start_stops_at_the_opening_card():
    harness = harness_for_question_path()
    harness.seed_project()

    view = await harness.runner().run(
        RoundCommand(kind="start", project_id="p1", user_input="想做个订单导出的功能")
    )

    assert view.waiting_on == "opening_card"
    assert view.step == "claims_ready"
    assert [claim.eid for claim in view.claims] == ["E1"]


async def test_graph_opening_runs_seats_and_stops_at_the_questionnaire():
    harness = harness_for_question_path()
    harness.seed_project()
    runner = harness.runner()
    view = await runner.run(
        RoundCommand(kind="start", project_id="p1", user_input="想做个订单导出的功能")
    )

    view = await runner.run(
        RoundCommand(kind="submit_opening", round_id=view.round_id, seats=("seat.data_scale",))
    )

    assert view.waiting_on == "questionnaire"
    assert view.questions[0].title == "单次导出的最大行数是多少？"
    assert view.seat_packets[0].status == "completed"


async def test_graph_answers_loop_back_to_seats_and_produce_a_draft():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": [QUESTION_REPLY, DRAFT_REPLY]},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )
    harness.seed_project()
    runner = harness.runner()
    view = await runner.run(
        RoundCommand(kind="start", project_id="p1", user_input="想做个订单导出的功能")
    )
    view = await runner.run(
        RoundCommand(kind="submit_opening", round_id=view.round_id, seats=("seat.data_scale",))
    )

    view = await runner.run(
        RoundCommand(
            kind="submit_answers",
            round_id=view.round_id,
            form_id=view.form_id or "f1",
            answers=(AnswerInput(question_id="q1", selected=("b",)),),
        )
    )

    assert view.waiting_on == "draft_card"
    assert view.seat_round_count == 2
    assert view.draft is not None
    assert view.draft.groups[-1][0] == "它替你判断的"


async def test_graph_confirm_writes_the_document():
    harness = harness_for_draft_path()
    harness.seed_project()
    runner = harness.runner()
    view = await runner.run(
        RoundCommand(kind="start", project_id="p1", user_input="想做个订单导出的功能")
    )
    view = await runner.run(
        RoundCommand(kind="submit_opening", round_id=view.round_id, seats=("seat.data_scale",))
    )
    accepted = tuple(proposal.proposal_id for proposal in view.draft.proposals)

    view = await runner.run(
        RoundCommand(kind="confirm", round_id=view.round_id, accepted=accepted)
    )

    assert view.step == "done"
    assert view.confirmed_version_no == 2


async def test_graph_retry_routes_back_to_the_failed_step():
    harness = Harness(replies={}, seat_replies={})  # 什么都不脚本化 → 第一步就失败
    harness.seed_project()
    runner = harness.runner()
    with pytest.raises(ModelCallFailed):
        await runner.run(
            RoundCommand(kind="start", project_id="p1", user_input="想做个订单导出的功能")
        )

    with pytest.raises(ModelCallFailed):
        await runner.run(RoundCommand(kind="retry", round_id="r1"))  # 恢复后重跑 analyze，还是失败

    assert harness.rounds.get("r1").step.value == "failed"


def test_graph_has_no_checkpointer():
    """计划 §3.7：进度只认 `Round`，不认图快照。真要加 checkpointer，
    必须先改这条测试与那一节——不允许悄悄多开一份"走到哪了"。"""

    harness = harness_for_draft_path()
    harness.seed_project()
    assert getattr(harness.runner().graph, "checkpointer", None) is None


def test_resume_routes_have_an_exit_for_every_step():
    """每个 step 都要有出口：老库里的坏行（step=failed）不能再撞 KeyError 变成 500。"""

    for step in Step:
        assert step.value in RESUME_ROUTES, f"{step} 没有续跑出口"
    assert RESUME_ROUTES[Step.FAILED.value] == "__end__"


class BlockingGateway:
    """第一次调用就卡住：用来把一轮钉在"正在处理中"，好测并发保护。"""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, request):  # type: ignore[no-untyped-def]
        self.entered.set()
        await self.release.wait()
        raise ModelCallFailed("测试用：卡住之后不打算成功", purpose=str(request.purpose))


async def test_a_second_action_on_the_same_round_is_rejected_while_one_is_running():
    """同一个 round 同时只允许一个动作——这就是把 `at_step` 写成 failed 的那条路。"""

    gateway = BlockingGateway()
    harness = Harness(gateway=gateway, seats=None)
    harness.seed_project()
    broken = make_round()
    broken.fail("先失败一次")  # 模拟"上一轮已经失败，用户点了继续/重试"
    harness.rounds.save(broken)
    runner = harness.runner()

    first = asyncio.create_task(runner.run(RoundCommand(kind="retry", round_id="r1")))
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)

    with pytest.raises(RoundBusy):
        await runner.run(RoundCommand(kind="retry", round_id="r1"))

    gateway.release.set()
    with pytest.raises(ModelCallFailed):
        await first
