import json

import pytest

from pmstudio.application.errors import ModelCallFailed
from pmstudio.application.use_cases import AnswerInput
from pmstudio.infrastructure.graph.graph import RoundCommand
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
