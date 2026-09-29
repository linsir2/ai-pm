import json

import pytest

from pmstudio.application.errors import PacketParseError
from pmstudio.application.use_cases import AnswerInput
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import OriginType
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import (
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_seat
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

JUDGMENT_ONLY_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "同类导出的常见上限",
                "evidence_refs": ["E1"],
            }
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


def questionnaire_packet(*, title: str = "单次导出的最大行数是多少？") -> str:
    draft = question_draft()
    draft["title"] = title
    return json.dumps(
        {"questions": [draft], "list_items": [], "proposals": []},
        ensure_ascii=False,
    )


def attributed_questionnaire_packet() -> str:
    """一道**带归因**的题：`from_opinions` 指向 `SEAT` 的第一条意见。

    归因是装配层算的（不看模型自填的 `asked_by`）——缩编的路径 ① 就靠它。
    """

    draft = question_draft() | {"from_opinions": [f"{SEAT}:O1"]}
    return json.dumps(
        {"questions": [draft], "list_items": [], "proposals": []},
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


def citing_draft_packet() -> str:
    """引用了"这条答案"的草案：这条流程里 `E3` 就是 `q1` 那条答案进账本后拿到的编号
    （E1 用户输入、E2 席位判断、E3 card_answer）。"""
    return draft_packet().replace('"evidence_refs": ["E1"]', '"evidence_refs": ["E1", "E3"]')


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


async def test_integrate_round_bad_two_failures_degrades_to_open_questions():
    """整合两次都过不了校验时，不再把整轮判死。

    旧行为（整轮 failed + 点"继续/重试"又跑同样的提示词、原地撞墙）在 `.codex/docs/设计/
    04-未决.md` C9 里定性为故障：作者真跑时席位交了 9 条疑问却无处可答。
    新语义：席位的疑问**原样**落成 s11 开放问题并出草案卡，失败原因写进缺口行。
    """

    bad = draft_packet(field="exceptionss")
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": [bad, bad]},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    # 不授权的话坏提案会被当成"新功能"转成一道题，压根走不到字段校验那一步
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.failure is None
    draft = round_.draft
    assert draft is not None
    # SEAT_REPLY 里只有一条疑问（O1），它要被原样接住
    assert len(draft.proposals) == 1
    proposal = draft.proposals[0]
    assert proposal.target.section_id == "s11"
    assert proposal.kind is BlockKind.QUESTION
    assert proposal.origin.type is OriginType.SYSTEM_DEFAULT
    assert "量级决定同步还是异步" in proposal.text
    # 文档里那句话保持干净（不塞内部席位 id）；"谁提的"从出处查得到
    evidence = harness.evidences.get(PROJECT).get(proposal.origin.refs[0])
    assert evidence.kind is EvidenceKind.SYSTEM_DEFAULT
    assert SEAT in evidence.text
    assert "整合失败" in evidence.source.rationale
    assert any("整合" in gap for gap in draft.gaps)


async def test_integrate_round_bad_two_failures_without_seat_questions_still_fails():
    """没有可降级的内容（席位只给了判断）时，仍然是整轮失败——失败必须是失败。"""

    bad = draft_packet(field="exceptionss")
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": [bad, bad]},
        seat_replies={SEAT: JUDGMENT_ONLY_REPLY},
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


async def test_integrate_round_stops_asking_after_two_questionnaires():
    """`04-未决.md` C6：默认最多两批问卷，第三批不再问，题目全部显式化成 s11 开放问题。

    真模型冒烟实测：一轮里被问了三批（用户体感是"没完没了"）。这条闸门保证——
    用户永远能走到草案卡，剩下的缺口在文档里挂着，想补随时补。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                questionnaire_packet(title="第一批：单次导出的最大行数是多少？"),
                questionnaire_packet(title="第二批：对端不可用时要怎么降级？"),
                questionnaire_packet(title="第三批：导出文件要不要留档？"),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    for index in range(2):  # 前两轮正常出问卷、答卷、回到席位轮
        await harness.integrate_round()(round_id=round_id)
        round_ = harness.rounds.get(round_id)
        assert round_.step is Step.AWAITING_ANSWERS
        # 题号按**轮次**发（2026-09-29 拍板 D4）：第一批 q1、第二批 q2（不再每批从 q1 重排）
        await harness.submit_answers()(
            round_id=round_id,
            form_id=f"f{index + 1}",
            answers=(),
            skipped=(f"q{index + 1}",),
        )
        await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.seat_round_count == 3
    assert len(round_.answer_sets) == 2  # 已经答了两批

    await harness.integrate_round()(round_id=round_id)  # 第 3 次整合：模型还想问

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.waiting_on is WaitingOn.DRAFT
    assert round_.draft is not None
    downgraded = [
        proposal
        for proposal in round_.draft.proposals
        if proposal.origin.type.value == "system_default"
        and "问卷已满" in (proposal.origin.rationale or "")
    ]
    assert downgraded
    assert all(str(item.target.section_id) == "s11" for item in downgraded)
    assert any("问卷已满" in gap and "不再追问" in gap for gap in round_.draft.gaps)


def test_stop_asking_reason_covers_both_gates():
    """两道闸门各自说得清是哪一条响的；问卷闸门优先（用户最先感受到的就是它）。"""

    from pmstudio.application.use_cases import _stop_asking_reason

    def make_round_with_history(*, answer_sets: int, seat_round_count: int) -> Round:
        return Round(
            round_id=ROUND_ID,
            project_id=PROJECT,
            user_input="想做个订单导出的功能",
            created_at=NOW,
            seat_round_count=seat_round_count,
            answer_sets=tuple(
                AnswerSet(
                    form_id=FormId(f"f{index + 1}"),
                    answers=(),
                    answered_at=NOW,
                    skipped=(QuestionId("q1"),),
                )
                for index in range(answer_sets)
            ),
        )

    two_answers = make_round_with_history(answer_sets=2, seat_round_count=1)
    assert _stop_asking_reason(two_answers) == "问卷已满 2 批"

    seat_limit = make_round_with_history(answer_sets=1, seat_round_count=3)
    assert _stop_asking_reason(seat_limit) == "已到席位轮上限"

    fresh = make_round_with_history(answer_sets=0, seat_round_count=1)
    assert _stop_asking_reason(fresh) is None


# —— P5c Task 8：答案轮缩编 ——

OTHER_SEAT = "seat.rules"

OTHER_SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "状态与规则",
                "claim": "驳回后回到草稿态",
                "because": "同类流程的常见做法",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)


async def test_second_pass_only_runs_the_narrowed_seats():
    """答完问卷之后：第二趟只调缩编名单里的席位（不看关键词、不看常驻）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [attributed_questionnaire_packet(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY, OTHER_SEAT: OTHER_SEAT_REPLY},
        # 两个席位的映射都不碰 s8：这道题的 target 正是 s8，
        # 所以只有"提过这道题"的 SEAT 会留下（这就是 C8 要的效果）
        seats=(make_seat(SEAT, sections=("s1",)), make_seat(OTHER_SEAT, sections=("s1",))),
    )
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, OTHER_SEAT))
    await harness.run_seat_round()(round_id=round_id)  # 第一趟：两个席位都跑
    await harness.integrate_round()(round_id=round_id)  # 出一道题（target=s8，asking=SEAT）

    round_ = harness.rounds.get(round_id)
    assert round_.questionnaire is not None
    authorize_order_export(harness, round_id)  # 第二趟的草案要落到已有功能的字段上

    await harness.submit_answers()(
        round_id=round_id, form_id="f1", answers=(), skipped=("q1",)
    )
    await harness.run_seat_round()(round_id=round_id)  # 第二趟：只跑缩编名单

    assert harness.gateway.purposes.count("seat_review") == 3  # 第一趟 2 + 第二趟 1
    round_ = harness.rounds.get(round_id)
    assert round_.active_seats == (SEAT,)
    assert round_.seats == (SEAT, OTHER_SEAT)  # 用户编制不动


# —— 2026-09-28 作者拍板：去处写错只丢那一条 ——


def packet_with_one_bad_target() -> str:
    """真模型实测的报文形状：一条去处写错（`s2 + goal`），一条正常（s8 的槽）。"""

    return json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s2", "field": "goal"},
                    "text": "这条去处写错了",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                    "rationale": "模型把功能字段名当成章节字段用了",
                },
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8", "field": "performance"},
                    "text": "5 万条 60 秒内可下载",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                    "rationale": "量级在 1 万级",
                },
            ],
        },
        ensure_ascii=False,
    )


async def test_bad_target_loses_one_item_not_the_whole_round():
    """去处写错一条：只丢那一条并留痕，同一批里其它提案照常出草案卡（不重试、不降级）。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": packet_with_one_bad_target()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM  # 没有整轮降级
    assert round_.draft is not None
    assert [proposal.text for proposal in round_.draft.proposals] == ["5 万条 60 秒内可下载"]
    assert not any(
        str(proposal.target.section_id) == "s11" for proposal in round_.draft.proposals
    )
    assert [
        (item.handle, item.source.value)
        for item in round_.dropped
        if item.handle == "提案 #1"
    ] == [("提案 #1", "assembly")]
    assert "去处不合法" in next(
        item.reason for item in round_.dropped if item.handle == "提案 #1"
    )
    # 一次就过：不再"两次都过不了校验"
    assert harness.gateway.purposes.count("integration") == 1


def packet_with_an_untraceable_judgment() -> str:
    """真模型实测的形状：一条有依据（E1 用户输入），一条依据是"没依据的席位判断"。

    第二条以前会被标成 `derived` 进"它替你判断的"那组，用户勾了 → 提交时 409
    （`invariant:E4 回溯不到用户输入`）。现在装配层按账本算，它落到"它提议的"。
    """

    return json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8", "field": "performance"},
                    "text": "5 万条 60 秒内可下载",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                    "rationale": "量级在 1 万级",
                },
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8", "field": "observability"},
                    "text": "外部调用都要接链路追踪",
                    "origin_type": "derived",
                    "evidence_refs": ["E2"],  # E2 = 席位自己的判断，且它没有依据
                    "rationale": "外部调用没有追踪就只能靠猜",
                },
            ],
        },
        ensure_ascii=False,
    )


async def test_untraceable_proposal_lands_in_proposed_and_confirm_succeeds():
    """不可追溯的那条落"它提议的"；用户照样勾选提交 → 写入成功（不再 409）。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": packet_with_an_untraceable_judgment()},
        seat_replies={
            SEAT: json.dumps(
                {
                    "basis": "读了 s1–s13 与 E1",
                    "opinions": [
                        {
                            "local_id": "O1",
                            "form": "judgment",
                            "about": "可观测性",
                            "claim": "外部调用都要接链路追踪",
                            "because": "纯专业判断（不引用依据）",
                            "evidence_refs": [],
                            "basis_note": "没有可引用的编号，这是经验判断",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        },
    )
    round_id = await round_in_seat_pass(harness)
    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None
    by_text = {proposal.text: proposal.origin.type.value for proposal in round_.draft.proposals}
    assert by_text["5 万条 60 秒内可下载"] == "derived"
    assert by_text["外部调用都要接链路追踪"] == "proposed"

    accepted = tuple(str(proposal.proposal_id) for proposal in round_.draft.proposals)
    result = await harness.confirm_draft()(round_id=round_id, accepted=accepted)

    assert result.version.version_no == 2  # 写进去了：不再被 invariant:E4 拦住


def packet_with_a_dropped_middle_proposal() -> str:
    """三条提案、中间那条去处不合法（会被装配层丢掉）→ 编号出现空洞（p01、p03）。"""

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
                        "field": "acceptance",
                    },
                    "text": "5 万条 60 秒内可下载",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                },
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s9", "field": "compatibility"},
                    "text": "这条的去处不合法（s9 没有这个槽）",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                },
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": "现状：导出靠手工复制",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                    "kind": "bullet_list",
                },
            ],
        },
        ensure_ascii=False,
    )


async def test_open_question_ids_do_not_collide_with_kept_proposals():
    """真跑实测（P7 验证）：装配层丢一条 → 编号有空洞 → 后面按 `len+1` 续编就撞号，
    `DuplicateProposalId` 把整轮打成 500（`p15` 重复）。

    编号必须按"现有最大编号 + 1"续，而不是按条数续。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet(), packet_with_a_dropped_middle_proposal()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)  # 出问卷
    await harness.submit_answers()(
        round_id=round_id, form_id="f1", answers=(), skipped=("q1",)
    )  # 跳过那道题
    await harness.run_seat_round()(round_id=round_id)

    await harness.integrate_round()(round_id=round_id)

    draft = harness.rounds.get(round_id).draft
    ids = [str(proposal.proposal_id) for proposal in draft.proposals]
    assert len(ids) == len(set(ids))  # 不撞号
    assert ids == ["p01", "p03", "p04"]  # p02 那条被丢了，续编从 p04 起


CLAIMS_REPLY_WITH_FEATURE = json.dumps(
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
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)


async def test_integration_prompt_carries_the_features_named_by_this_round_claims():
    """主张点名的功能已授权 → 提示词里必须写明，否则模型把内容降级成章节级（报告 F1）。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY_WITH_FEATURE, "integration": questionnaire_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    await harness.integrate_round()(round_id=round_id)

    slots = harness.prompts.calls[-1][1]
    assert "order-export" in slots["authorized_features"]
    assert "已授权" in slots["authorized_features"]


# —— 轮内未决队列（P9）——


def packet_with_questions(count: int, *, prefix: str = "") -> str:
    return json.dumps(
        {
            "questions": [
                question_draft() | {"title": f"{prefix}第 {index} 题"}
                for index in range(1, count + 1)
            ],
            "list_items": [],
            "proposals": [],
        },
        ensure_ascii=False,
    )


async def test_questions_squeezed_out_by_the_batch_cap_come_back_next_batch():
    """真跑实测：第 6 题起原来被写成"留到下一轮"的 list_items，然后随问卷被覆盖而消失。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                packet_with_questions(7),
                packet_with_questions(1, prefix="第二批"),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)
    first = harness.rounds.get(round_id).questionnaire
    assert [item.title for item in first.questions] == [f"第 {i} 题" for i in range(1, 6)]
    assert len(harness.rounds.get(round_id).deferred) == 2  # 第 6、7 题进了队列

    await harness.submit_answers()(round_id=round_id, form_id="f1", answers=(), skipped=("q1",))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    second = harness.rounds.get(round_id).questionnaire
    assert [item.title for item in second.questions] == ["第 6 题", "第 7 题", "第二批第 1 题"]
    assert harness.rounds.get(round_id).deferred == ()


async def test_deferred_queue_is_flushed_to_s11_when_the_round_stops_asking():
    """走到闸门（问卷两批）时：队列**清空**，压在里面的题**一条不落**地进 s11。

    队列刻意不设硬上限：上限之外的那部分只能在**问卷步骤**落地，而问卷那一支不写提案——
    要么丢内容，要么借 `list_items` 复活"留到下一轮"的假承诺。体积本来就被
    "每批题目数 × 批数"限住（个位数），所以不设上限。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                packet_with_questions(12),  # 第 1 批：5 问 + 7 进队列
                packet_with_questions(1, prefix="第二批"),
                draft_packet(),  # 第 3 批：模型没新题，队列里的题要落 s11
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)
    assert len(harness.rounds.get(round_id).deferred) == 7

    await harness.submit_answers()(round_id=round_id, form_id="f1", answers=(), skipped=("q1",))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)  # carried 优先问 → 还是问卷
    still_queued = [item.title for item in harness.rounds.get(round_id).deferred]
    assert still_queued  # 还有压着的

    # 题号按**轮次**发（2026-09-29 拍板 D4）：第一批问了 q1–q5，所以第二批这一道是 q6
    await harness.submit_answers()(round_id=round_id, form_id="f2", answers=(), skipped=("q6",))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)  # 闸门：两批已满 → 降级 + 清队列

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None
    assert round_.deferred == ()
    s11 = " ".join(
        item.text for item in round_.draft.proposals if str(item.target.section_id) == "s11"
    )
    for title in still_queued:
        assert title in s11


def seed_open_item(harness: Harness, *, entry_id: str, text: str) -> None:
    """往项目的 s11 里预置一条未决条目（模拟"前几轮留下的"）。"""

    document = harness.documents.get(PROJECT)
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-seed",
                entry_id=EntryId(entry_id),
                op=WriteOp.ADD,
                target=Target.body("s11"),
                text=text,
                refs=(EvidenceId("E1"),),
                kind=BlockKind.QUESTION,
            )
        ],
        now=NOW,
        summary="测试：预置未决项",
        authorized_new_features=frozenset(),
    )
    harness.documents.save(document)


OPEN_ITEM_TEXT = (
    "〔未决·阻塞〕点检完成率的统计口径（未决定，待决人：PM；跳过后果：报表口径不清）"
)


async def test_an_open_item_is_asked_in_the_next_round_with_its_entry_id():
    """跨轮队列 = s11 未决条目：这一轮把它问回来，并记住它来自哪条条目。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": questionnaire_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    seed_open_item(harness, entry_id="r0#open01", text=OPEN_ITEM_TEXT)

    await harness.integrate_round()(round_id=round_id)

    questions = harness.rounds.get(round_id).questionnaire.questions
    assert questions[0].title == "点检完成率的统计口径"
    assert questions[0].from_entry == "r0#open01"
    assert questions[0].blocking is True


async def test_open_items_are_rendered_for_the_integrator():
    """模型要在提示词里看见"前几轮留下的未决项 + 它们的 entry id"，才撤得对（P9-B）。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": questionnaire_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    seed_open_item(harness, entry_id="r0#open01", text=OPEN_ITEM_TEXT)

    await harness.integrate_round()(round_id=round_id)

    slots = harness.prompts.calls[-1][1]
    assert "点检完成率的统计口径" in slots["open_items"]
    assert "r0#open01" in slots["open_items"]


async def test_answering_an_open_item_retires_it_from_s11():
    """真跑实测：s11 只涨不消（3→13），ready 永远过不了（报告 F14）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    seed_open_item(harness, entry_id="r0#open01", text=OPEN_ITEM_TEXT)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    question = harness.rounds.get(round_id).questionnaire.questions[0]

    await harness.submit_answers()(
        round_id=round_id,
        form_id="f1",
        answers=(AnswerInput(question_id=str(question.question_id), text="按设备台数算"),),
        skipped=(),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.draft is not None
    removals = [item for item in round_.draft.proposals if str(item.op) == "remove"]
    assert [str(item.entry_id) for item in removals] == ["r0#open01"]
    assert "本轮已回答" in removals[0].text
    assert removals[0].origin.type is OriginType.SYSTEM_DEFAULT


async def test_skipping_an_open_item_does_not_retire_it():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    seed_open_item(harness, entry_id="r0#open01", text=OPEN_ITEM_TEXT)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    question = harness.rounds.get(round_id).questionnaire.questions[0]

    await harness.submit_answers()(
        round_id=round_id,
        form_id="f1",
        answers=(),
        skipped=(str(question.question_id),),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item for item in round_.draft.proposals if str(item.op) == "remove"] == []


async def test_open_items_answered_in_different_batches_are_all_retired():
    """真跑第 2 轮暴露：`question_id` 每一批都从 `q1` 重排——来源必须按 `form_id + question_id` 记。

    否则后一批的来源会覆盖前一批，"第一批答掉的那条未决项"永远撤不下来。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                questionnaire_packet(),
                questionnaire_packet(),
                draft_packet(),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    seed_open_item(
        harness,
        entry_id="r0#open01",
        text="〔未决·阻塞〕第一条未决（未决定，待决人：PM；跳过后果：后果一）",
    )
    seed_open_item(
        harness,
        entry_id="r0#open02",
        text="〔未决·阻塞〕第二条未决（未决定，待决人：PM；跳过后果：后果二）",
    )
    seed_open_item(
        harness,
        entry_id="r0#open03",
        text="〔未决·阻塞〕第三条未决（未决定，待决人：PM；跳过后果：后果三）",
    )

    # 第一批：carried 前两条未决（每批上限 2）+ 模型那道题（都答掉）
    await harness.integrate_round()(round_id=round_id)
    first = harness.rounds.get(round_id).questionnaire
    assert first.questions[0].from_entry == "r0#open01"
    assert first.questions[1].from_entry == "r0#open02"
    await harness.submit_answers()(
        round_id=round_id,
        form_id=str(first.form_id),
        answers=tuple(
            AnswerInput(question_id=str(item.question_id), text="第一批的答案")
            for item in first.questions
        ),
        skipped=(),
    )
    await harness.run_seat_round()(round_id=round_id)

    # 第二批：carried 第三条未决（前两条已经问过，不再重复问）+ 模型那道题（都答掉）
    await harness.integrate_round()(round_id=round_id)
    second = harness.rounds.get(round_id).questionnaire
    assert second.questions[0].from_entry == "r0#open03"
    await harness.submit_answers()(
        round_id=round_id,
        form_id=str(second.form_id),
        answers=tuple(
            AnswerInput(question_id=str(item.question_id), text="第二批的答案")
            for item in second.questions
        ),
        skipped=(),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)  # 两批已满 → 降级 + 出草案

    round_ = harness.rounds.get(round_id)
    removals = sorted(
        str(item.entry_id) for item in round_.draft.proposals if str(item.op) == "remove"
    )
    assert removals == ["r0#open01", "r0#open02", "r0#open03"]


# —— 答案的身份：题面回历史的批、编号按 (轮次, 题号) 认（2026-09-29 拍板 D4）——


def test_the_open_items_checklist_shows_where_each_item_belongs():
    """给整合者看的未决清单要带**去处**（P11）：它看得见"这个去处已经有老问题在等"，就少重出一遍。"""

    from pmstudio.application.use_cases import _render_open_items

    carried = (
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s8", field="compatibility"),
            type=QuestionType.TEXT,
            title="导出的兼容性口径",
            why="前几轮留下的未决项",
            consequence_if_skipped="先不兼容",
            from_entry="r1#open01",
        ),
    )

    text = _render_open_items(carried)

    assert "〔去处：s8.compatibility〕" in text
    assert "r1#open01" in text
    assert "导出的兼容性口径" in text


def test_skipped_question_defaults_use_its_own_batchs_title():
    """跳过的题补 system_default 出处时，文本要写**它自己那一批**的题面（计划 §0.1 第 4 处）。"""

    from pmstudio.application.use_cases import _system_defaults

    round_ = skipped_two_batch_round()
    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="想做个订单导出",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=NOW,
    )

    defaults = _system_defaults(round_, ledger=ledger, now=NOW)

    assert "第一批的题：最大行数？" in ledger.get(defaults[QuestionId("q1")]).text
    assert "第二批的题：对端不可用时怎么降级？" in ledger.get(defaults[QuestionId("q2")]).text


def test_retiring_an_answered_open_item_cites_this_rounds_answer():
    """撤下未决项时引的出处编号必须是**本轮**那条答案（计划 §0.1 第 5 处）。

    老实现收整个项目账本、按 `setdefault` 取第一条：别的轮里同号的那条答案会被引走。
    """

    from pmstudio.application.use_cases import _retire_answered_open_items

    other_round = RoundId("r0")
    round_ = Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="想做个订单导出",
        created_at=NOW,
        answer_sets=(
            AnswerSet(
                form_id=FormId("f2"),
                answers=(Answer(question_id=QuestionId("q1"), text="这一轮的答案"),),
                answered_at=NOW,
            ),
        ),
        carried_sources={"f2#q1": "r0#open01"},
    )
    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text="上一轮的答案",
        source=EvidenceSource(round_id=other_round, question_id=QuestionId("q1")),
        now=NOW,
    )
    mine = ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text="这一轮的答案",
        source=EvidenceSource(round_id=ROUND_ID, question_id=QuestionId("q1")),
        now=NOW,
    )

    proposals = _retire_answered_open_items(round_, ledger=ledger, start_index=1)

    assert [item.origin.refs for item in proposals] == [(mine,)]
    assert [str(item.entry_id) for item in proposals] == ["r0#open01"]


def skipped_two_batch_round() -> Round:
    """两批题都被跳过的轮次（直接构造：这一层要验的是"回哪一批找题面"）。"""

    first = Questionnaire(
        form_id=FormId("f1"),
        round_id=ROUND_ID,
        questions=(
            Question(
                question_id=QuestionId("q1"),
                target=Target.body("s5"),
                type=QuestionType.TEXT,
                title="第一批的题：最大行数？",
                why="量级决定同步还是异步",
                consequence_if_skipped="按同步实现",
            ),
        ),
    )
    second = Questionnaire(
        form_id=FormId("f2"),
        round_id=ROUND_ID,
        questions=(
            Question(
                question_id=QuestionId("q2"),
                target=Target.body("s8"),
                type=QuestionType.TEXT,
                title="第二批的题：对端不可用时怎么降级？",
                why="缺一个只有你能给的信息",
                consequence_if_skipped="先按不降级处理",
            ),
        ),
    )
    return Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="想做个订单导出",
        created_at=NOW,
        step=Step.SEATS_RUNNING,
        questionnaire=second,
        questionnaire_history=(first, second),
        answer_sets=(
            AnswerSet(
                form_id=FormId("f1"),
                answers=(),
                answered_at=NOW,
                skipped=(QuestionId("q1"),),
            ),
            AnswerSet(
                form_id=FormId("f2"),
                answers=(),
                answered_at=NOW,
                skipped=(QuestionId("q2"),),
            ),
        ),
    )


async def test_an_answered_question_lands_as_content_when_the_draft_ignores_it():
    """D3：整合者没引用这条答案 ⇒ 机械落一条「用户已定：…」。

    跨轮去重只认文档，答案不落地下一轮必然再问（报告 F6）。落地文本用**选项文案**。
    """

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
        round_id=round_id,
        form_id="f1",
        answers=(
            AnswerInput(question_id="q1", selected=("b",), text="大概是这个量级"),
        ),
        skipped=(),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)  # 出草案：只引了 E1，没引这条答案

    proposals = harness.rounds.get(round_id).draft.proposals
    landed = [item for item in proposals if item.text.startswith("用户已定：")]

    assert len(landed) == 1
    assert landed[0].text == "用户已定：1 万–10 万 / 大概是这个量级"
    assert [str(item.target.section_id) for item in landed] == ["s8"]
    assert landed[0].origin.type.value == "user"


async def test_a_landed_answer_does_not_repeat_after_the_draft_cites_it():
    """同一件事别落两遍：整合者引用了答案 ⇒ 机械通道闭嘴。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet(), citing_draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)
    await harness.submit_answers()(
        round_id=round_id,
        form_id="f1",
        answers=(AnswerInput(question_id="q1", selected=("b",), text=""),),
        skipped=(),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    proposals = harness.rounds.get(round_id).draft.proposals
    assert [item for item in proposals if item.text.startswith("用户已定：")] == []
