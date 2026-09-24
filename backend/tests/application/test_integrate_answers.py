import json

import pytest

from pmstudio.application.errors import PacketParseError
from pmstudio.application.use_cases import AnswerInput
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from tests.support.builders import PROJECT, SEAT
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
            }
        ],
        "seats": [
            {"seat_id": SEAT, "why": "导出要评估数据量与耗时"},
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
                "form": "question",
                "about": "导出的数据量级",
                "claim": "量级决定同步还是异步，必须问用户",
                "because": "1 万以内可同步，10 万以上必须异步",
                "evidence_refs": ["E1"],
            },
            {
                "local_id": "O2",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "同类导出的常见上限",
                "evidence_refs": ["E1"],
            },
        ],
    },
    ensure_ascii=False,
)


def question_draft() -> dict:
    return {
        "kind": "gap",
        "title": "单次导出的最大行数是多少？",
        "why": "量级决定同步还是异步",
        "consequence_if_skipped": "按同步实现，超时风险由 PM 承担",
        "target": {"zone": "body", "section_id": "s8"},
        "type": "single",
        "options": [
            {"option_id": "a", "text": "少于 1 万"},
            {"option_id": "b", "text": "1 万–10 万"},
        ],
        "evidence_refs": ["E1"],
        "asked_by": [SEAT],
    }


def questionnaire_packet() -> str:
    return json.dumps(
        {"questions": [question_draft()], "list_items": [], "proposals": []},
        ensure_ascii=False,
    )


def draft_packet(*, field: str = "exceptions") -> str:
    return json.dumps(
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
                        "field": field,
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


async def round_in_seat_pass(harness: Harness):
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    return round_id


def authorize_order_export(harness: Harness, round_id) -> None:  # type: ignore[no-untyped-def]
    round_ = harness.rounds.get(round_id)
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
    harness.rounds.save(round_)


async def test_integrate_round_asks_when_there_are_questions():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": questionnaire_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_ANSWERS
    assert round_.waiting_on is WaitingOn.QUESTIONNAIRE
    assert str(round_.questionnaire.questions[0].question_id) == "q1"
    assert round_.draft is None


async def test_integrate_round_writes_draft_when_nothing_to_ask():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.waiting_on is WaitingOn.DRAFT
    assert round_.draft is not None
    assert round_.draft.proposals[0].kind.value == "bullet_list"  # 字段类型查 schema 表
    assert str(round_.draft.proposals[0].entry_id) == f"{round_id}#01"


async def test_integrate_round_repairs_once_when_validation_fails():
    bad = draft_packet(field="exceptionss")  # schema 表里没有这个字段
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": [bad, draft_packet()]},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    integration_calls = [call for call in harness.prompts.calls if call[0] == "integrator"]
    assert len(integration_calls) == 2
    assert integration_calls[0][1]["validation_error"] == ""
    assert "exceptionss" in integration_calls[1][1]["validation_error"]
    assert harness.rounds.get(round_id).step is Step.AWAITING_CONFIRM


async def test_integrate_round_bad_two_failures_fail_the_round():
    bad = draft_packet(field="exceptionss")
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": [bad, bad]},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    with pytest.raises(PacketParseError):
        await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert round_.draft is None  # 零落库


async def test_integrate_round_turns_skipped_questions_into_open_questions():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)  # 出问卷
    await harness.submit_answers()(
        round_id=round_id, form_id="f1", answers=(), skipped=("q1",)
    )  # 跳过
    await harness.run_seat_round()(round_id=round_id)  # 回席位轮

    await harness.integrate_round()(round_id=round_id)

    draft = harness.rounds.get(round_id).draft
    open_questions = [
        proposal for proposal in draft.proposals if proposal.origin.type.value == "system_default"
    ]
    assert len(open_questions) == 1
    assert "单次导出的最大行数是多少？" in open_questions[0].text
    assert "按同步实现，超时风险由 PM 承担" in open_questions[0].text  # 后果取题面原句
    assert str(open_questions[0].target.section_id) == "s11"
    assert isinstance(draft.gaps, tuple)  # 干跑给出的真实缺口


async def test_submit_answers_records_card_answer_and_returns_to_seat_round():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": questionnaire_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    await harness.integrate_round()(round_id=round_id)

    await harness.submit_answers()(
        round_id=round_id,
        form_id="f1",
        answers=(AnswerInput(question_id="q1", selected=("b",), text="大概是这个量级"),),
        skipped=(),
    )

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.SEATS_RUNNING
    assert round_.seat_round_count == 2
    answers = [
        evidence
        for evidence in harness.evidences.get(PROJECT).evidences()
        if evidence.kind.value == "card_answer"
    ]
    assert len(answers) == 1
    assert answers[0].source.question_id is not None
    assert "1 万–10 万" in answers[0].text
    assert "大概是这个量级" in answers[0].text


async def test_submit_answers_authorizes_new_feature_when_user_says_yes():
    decision = {
        "kind": "decision",
        "title": "「order-export」是一个独立功能吗？",
        "why": "新建功能子树会改变文档结构",
        "consequence_if_skipped": "先按不新建处理",
        "target": {"zone": "body", "section_id": "s5", "feature_slug": "order-export"},
        "type": "single",
        "options": [
            {"option_id": "new_feature_yes", "text": "是独立功能"},
            {"option_id": "new_feature_no", "text": "不是"},
        ],
        "evidence_refs": ["E1"],
        "asked_by": [SEAT],
    }
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": json.dumps(
                {"questions": [decision], "list_items": [], "proposals": []},
                ensure_ascii=False,
            ),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    await harness.integrate_round()(round_id=round_id)

    await harness.submit_answers()(
        round_id=round_id,
        form_id="f1",
        answers=(AnswerInput(question_id="q1", selected=("new_feature_yes",)),),
    )

    round_ = harness.rounds.get(round_id)
    assert round_.authorized_new_features == frozenset({FeatureSlug("order-export")})


async def test_integrate_round_at_seat_limit_forces_a_draft_instead_of_asking_again():
    """`02` §7：到席位轮上限仍有缺口 → 全部显式化，不再追问。

    真模型冒烟实测：第二轮整合又出了一轮问卷；如果没有这条规则，用户会被卡在
    "回答 → 又问 → 回答 → 撞上限报错"的死循环里。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet(), questionnaire_packet(), questionnaire_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    for index in range(2):  # 前两轮正常出问卷、答卷、回到席位轮
        await harness.integrate_round()(round_id=round_id)
        round_ = harness.rounds.get(round_id)
        assert round_.step is Step.AWAITING_ANSWERS
        await harness.submit_answers()(
            round_id=round_id, form_id=f"f{index + 1}", answers=(), skipped=("q1",)
        )
        await harness.run_seat_round()(round_id=round_id)

    assert harness.rounds.get(round_id).seat_round_count == 3  # 到了上限

    await harness.integrate_round()(round_id=round_id)  # 第 3 次整合：模型还想问

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.waiting_on is WaitingOn.DRAFT
    assert round_.draft is not None
    downgraded = [
        proposal
        for proposal in round_.draft.proposals
        if proposal.origin.type.value == "system_default"
        and "上限" in (proposal.origin.rationale or "")
    ]
    assert downgraded
    assert all(str(item.target.section_id) == "s11" for item in downgraded)
    assert any("不再追问" in gap for gap in round_.draft.gaps)
