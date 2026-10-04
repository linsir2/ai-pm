import json

import pytest

from pmstudio.application.commands import RoundCommand
from pmstudio.application.dto import RoundOutcomeView
from pmstudio.application.errors import PacketParseError
from pmstudio.application.use_cases import AnswerInput
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    ListItem,
    Question,
    QuestionKind,
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
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import (
    NOW,
    PROJECT,
    ROUND_ID,
    SEAT,
    document_with_feature,
    make_document,
    make_round,
    make_seat,
)
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

#: 夹具补全：把这一轮两条意见的**去向写全**（`02` §11.0.37）。
#:
#: 真模型该写成这样：疑问要么写进某道题的 `from_opinions`，要么申报"没采用 + 为什么"；
#: 判断要么被引用编号，要么申报。夹具不写全，点名重装就会插进来多消耗一次脚本回复，
#: 把那些**不考归因**的用例（去处写错、题挤不下、dig……）整体搅乱。
SEAT_OPINIONS_ACCOUNTED = [
    {"handle": f"{SEAT}:O1", "reason": "与本轮的题/提案说的是同一件事"},
    {"handle": f"{SEAT}:O2", "reason": "本轮先不写，留给下一轮"},
]

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
    # 夹具补成"模型把意见交代完整"的样子（`02` §11.0.37）：这道题的**地址**指向那条疑问，
    # 没采用的那条判断逐条申报。不这么做的话，点名重装会插进来多消耗一次脚本回复，
    # 把所有不考归因的用例搅乱。
    draft = question_draft() | {"from_opinions": [f"{SEAT}:O1"]}
    draft["title"] = title
    return json.dumps(
        {
            "questions": [draft],
            "list_items": [],
            "proposals": [],
            "dropped_opinions": [{"handle": f"{SEAT}:O2", "reason": "本轮先不写，留给下一轮"}],
        },
        ensure_ascii=False,
    )


def questionnaire_packet_with_a_list_item(
    text: str = "第一条清单项：模板字段要不要可配置？",
) -> str:
    """一批问卷 + 一条清单项（超出本批的记下来，不挡这一轮）。"""

    draft = question_draft()
    return json.dumps(
        {
            "questions": [draft],
            "list_items": [
                {
                    "text": text,
                    "target": {"zone": "body", "section_id": "s8"},
                    "reason": "本批装不下，先记下来",
                }
            ],
            "proposals": [],
            "dropped_opinions": [
                {"handle": f"{SEAT}:O1", "reason": "与本批那道题说的是同一件事"},
                {"handle": f"{SEAT}:O2", "reason": "本轮先不写，留给下一轮"},
            ],
        },
        ensure_ascii=False,
    )


def attributed_questionnaire_packet() -> str:
    """一道**带归因**的题：`from_opinions` 指向 `SEAT` 的第一条意见。

    归因是装配层算的（不看模型自填的 `asked_by`）——缩编的路径 ① 就靠它。
    """

    draft = question_draft() | {"from_opinions": [f"{SEAT}:O1"]}
    return json.dumps(
        {
            "questions": [draft],
            "list_items": [],
            "proposals": [],
            "dropped_opinions": [{"handle": f"{SEAT}:O2", "reason": "本轮先不写，留给下一轮"}],
        },
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
            "dropped_opinions": [
                {"handle": f"{SEAT}:O1", "reason": "本轮没有必须问用户的东西"},
                {"handle": f"{SEAT}:O2", "reason": "与上面那条提案说的是同一件事"},
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


async def test_the_next_batch_prompt_carries_the_previous_list_items():
    """F8：清单项"说完就扔"——下一批的整合者要看得到上一批记下的东西。

    真相源在**轮次**上（不是当轮问卷）：问卷一批换一批，挂在问卷上的清单项跟着就没了。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet_with_a_list_item(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)  # 第一批：出问卷 + 记下清单项
    first = harness.rounds.get(round_id).questionnaire
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
    await harness.integrate_round()(round_id=round_id)  # 第二批：提示词里要看得到

    calls = [slots for name, slots in harness.prompts.calls if name == "integrator"]
    assert "第一条清单项：模板字段要不要可配置？" in calls[-1]["previous_list_items"]
    assert [
        item.text for item in harness.rounds.get(round_id).list_items
    ] == ["第一条清单项：模板字段要不要可配置？"]


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


def test_the_progress_line_counts_required_feature_fields():
    """F10：用户点确认前要看得见"这份文档离可交付还差多少"。"""

    from pmstudio.application.use_cases import _feature_progress
    from pmstudio.domain.authoring.schema import REQUIRED_FEATURE_FIELDS

    line = _feature_progress(document_with_feature())  # order-export 只填了 one_liner

    assert line is not None
    assert line.startswith("s5 已填 1/")
    assert str(len(REQUIRED_FEATURE_FIELDS)) in line


def test_there_is_no_progress_line_without_any_feature():
    """没有功能树就没有"必填字段补齐度"这回事——别在空文档上报一句 0/0。"""

    from pmstudio.application.use_cases import _feature_progress

    assert _feature_progress(make_document()) is None


def backbone_filled_document():  # type: ignore[no-untyped-def]
    """把骨架六章填到达标：s1/s3/s10 各 1 条、s2/s4 各 2 条、s12 一条验收标准。"""

    document = make_document()
    plan = (
        ("s1", 1, BlockKind.TEXT),
        ("s2", 2, BlockKind.TEXT),
        ("s3", 1, BlockKind.TEXT),
        ("s4", 2, BlockKind.TEXT),
        ("s10", 1, BlockKind.TEXT),
        ("s12", 1, BlockKind.ACCEPTANCE),
    )
    instructions = [
        WriteInstruction(
            instruction_id=f"i-{section}-{offset}",
            entry_id=EntryId(f"en-{section}-{offset}"),
            op=WriteOp.ADD,
            target=Target.body(section),
            text=f"{section} 的第 {offset + 1} 条",
            refs=(EvidenceId("E1"),),
            kind=kind,
        )
        for section, count, kind in plan
        for offset in range(count)
    ]
    document.apply(instructions, now=NOW, summary="干跑", authorized_new_features=frozenset())
    return document


def test_dry_run_puts_the_progress_line_first():
    """进度行走 `gaps` 这条既有通道（前端已经在渲染 gaps，一个字都不用改）。"""

    from pmstudio.application.use_cases import dry_run

    gaps = dry_run(document_with_feature(), (), round_=make_round(), now=NOW)

    assert gaps[0].startswith("s5 已填 1/6 个必填字段")
    assert "还有 5 个空着" in gaps[0]
    assert any("仍然为空" in item for item in gaps[1:])  # 逐字段缺口照旧跟在后面


async def test_the_draft_card_says_how_much_is_still_missing():
    """草案卡的第一行缺口就是补齐度（F10），后面才是逐字段的"仍然为空"。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet(field="io")},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    harness.documents.save(document_with_feature())  # 文档里已经有一棵 order-export

    await harness.integrate_round()(round_id=round_id)

    draft = harness.rounds.get(round_id).draft
    assert draft is not None
    assert draft.gaps[0].startswith("s5 已填 1/6 个必填字段")
    assert "还有 5 个空着" in draft.gaps[0]
    assert any("仍然为空" in item for item in draft.gaps[1:])


async def test_the_draft_card_names_the_empty_backbone_chapters():
    """报告 F18：叙述式输入第 1 轮 12 条主张有 10 条落 s5，骨架六章零条 → 8 个阻塞，
    而草案卡上什么都不说。现在卡片自己得说出"这几章还是空的、这一轮也没写"。
    """

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    harness.documents.save(make_document())  # 空文档：骨架六章一条都没有

    await harness.integrate_round()(round_id=round_id)

    draft = harness.rounds.get(round_id).draft
    assert draft is not None
    assert any("还是空的" in gap and "s1" in gap for gap in draft.gaps)


async def test_the_draft_card_shuts_up_once_the_backbone_is_filled():
    """骨架填上之后不许再吵——否则项目跑到第二轮，每轮都弹一次假警报。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    harness.documents.save(backbone_filled_document())

    await harness.integrate_round()(round_id=round_id)

    draft = harness.rounds.get(round_id).draft
    assert draft is not None
    assert not any("还是空的" in gap for gap in draft.gaps)


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

    # 第一趟的报文：给 SEAT 那道题带上归因，**并且**替另一个席位申报掉它那条没被采用的判断
    # ——否则 §11.0.37 的点名重装会插进来多消耗一次脚本回复，剧本就乱了。
    first = json.loads(attributed_questionnaire_packet())
    first["dropped_opinions"].append(
        {"handle": f"{OTHER_SEAT}:O1", "reason": "本轮先不写，留给下一轮"}
    )
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [json.dumps(first, ensure_ascii=False), draft_packet()],
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
            "dropped_opinions": SEAT_OPINIONS_ACCOUNTED,
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
            "dropped_opinions": SEAT_OPINIONS_ACCOUNTED,
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


async def test_three_oldest_open_items_come_back_in_the_first_batch():
    """F14：消化配额从 2 提到 3——一轮两批 ⇒ 最多消化 6 条未决项。

    口径（C3）：**不设"必须清零"的目标**。一轮可能暴露十几条缺口，而一轮最多问 10 题，
    产生大于消化是算术事实；未决清单是**随交付物一起给**的东西，不是假装能清零的东西。
    """

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": questionnaire_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    for index in range(1, 5):  # 预置 4 条：最老的 3 条该被问回，第 4 条继续在 s11 排队
        seed_open_item(harness, entry_id=f"r0#open0{index}", text=OPEN_ITEM_TEXT)

    await harness.integrate_round()(round_id=round_id)

    asked = harness.rounds.get(round_id).questionnaire.questions
    carried = [item for item in asked if item.from_entry is not None]
    assert [item.from_entry for item in carried] == ["r0#open01", "r0#open02", "r0#open03"]


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

    夹具说明（2026-09-29，每批配额 2 → 3 时同步扩了一条）：要验"**跨批**都要撤下来"，
    未决条数必须**多过一批能吃下的量**。配额是 3，所以预置 4 条：第一批问 1–3 条，第二批问第 4 条。
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
    seed_open_item(
        harness,
        entry_id="r0#open04",
        text="〔未决·阻塞〕第四条未决（未决定，待决人：PM；跳过后果：后果四）",
    )

    # 第一批：carried 前三条未决（每批上限 3）+ 模型那道题（都答掉）
    await harness.integrate_round()(round_id=round_id)
    first = harness.rounds.get(round_id).questionnaire
    assert first.questions[0].from_entry == "r0#open01"
    assert first.questions[1].from_entry == "r0#open02"
    assert first.questions[2].from_entry == "r0#open03"
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

    # 第二批：carried 第四条未决（前三条已经问过，不再重复问）
    await harness.integrate_round()(round_id=round_id)
    second = harness.rounds.get(round_id).questionnaire
    assert second.questions[0].from_entry == "r0#open04"
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
    assert removals == ["r0#open01", "r0#open02", "r0#open03", "r0#open04"]


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


def test_the_open_items_checklist_names_the_entry_address():
    """地址要**有名字**、并说清它只用来撤条目（learnings L13 / M6f 对记忆 id 的同一手法）：
    真跑里模型把裸 id 当引用抄进了标题 —— 变成正文里一句 `（open61/open67/open112）`。"""

    from pmstudio.application.use_cases import _render_open_items

    carried = (
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s7"),
            type=QuestionType.TEXT,
            title="菜单增删改价与订单改撤是否保留历史",
            why="前几轮留下的未决项",
            consequence_if_skipped="先搁着",
            from_entry="r1#open61",
        ),
    )

    text = _render_open_items(carried)

    assert "条目地址" in text
    assert "r1#open61" in text
    assert "不许写进" in text


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


def test_a_mechanical_remove_does_not_count_as_citing_an_answer():
    """机械撤下未决条（`remove`）只是**记账**：它不写任何内容，不能算"这条答案被引用了"（P11 T4）。

    否则"carried 题的答案"天然会被它自己那条 remove 引走 → 机械落地永远不触发 → 结论照样蒸发
    （真跑 `p-0b212145` 第二轮就是这么丢的：四条答案只留下一条 remove 的理由）。
    """

    from pmstudio.application.use_cases import _cited_eids

    written = Proposal(
        proposal_id=ProposalId("p01"),
        entry_id=EntryId("r1#01"),
        op=WriteOp.ADD,
        target=Target.body("s2"),
        text="目标：5 分钟送达率 ≥ 95%",
        origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
        kind=BlockKind.TEXT,
    )
    retired = Proposal(
        proposal_id=ProposalId("p02"),
        entry_id=EntryId("r1#open01"),
        op=WriteOp.REMOVE,
        target=Target.body("s11"),
        text="本轮已回答，撤下这条未决项",
        origin=Origin(OriginType.SYSTEM_DEFAULT, (EvidenceId("E2"),)),
        kind=BlockKind.QUESTION,
    )

    cited = _cited_eids([written, retired], [])

    assert cited == frozenset({EvidenceId("E1")})


# —— 矛盾的机械通道：analyzer 报对子 → 装配层盖章（T8）——


CONTRADICTION_CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了这一轮输入",
        "claims": [
            {
                "text": "点检记录提交后不许改",
                "raw_span": "点检记录不许改",
                "label": "constraint",
                "zone": "body",
                "section_id": "s5",
            },
            {
                "text": "点错了要能改",
                "raw_span": "点错了要能改",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            },
        ],
        "seats": [{"seat_id": SEAT, "why": "两条规则互相打架"}],
        "contradictions": [{"claim_a": 1, "claim_b": 2, "why": "一个说不能改，一个说要能改"}],
    },
    ensure_ascii=False,
)


def conflict_question_packet() -> str:
    """模型出的一道题**同时引用了互斥两侧**（E1 与 E2），但它自己把 kind 写成 gap。"""

    draft = question_draft() | {
        "title": "点检记录提交后到底能不能改？",
        "kind": "gap",
        "evidence_refs": ["E1", "E2"],
    }
    return json.dumps(
        {"questions": [draft], "list_items": [], "proposals": []}, ensure_ascii=False
    )


async def test_a_question_covering_a_contradiction_is_stamped_conflict_end_to_end():
    """端到端：analyzer 标了互斥 → 装配层给覆盖两侧的题盖章 `conflict`（不看模型怎么写）。"""

    harness = Harness(
        replies={
            "claims": CONTRADICTION_CLAIMS_REPLY,
            "integration": conflict_question_packet(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(
        project_id=PROJECT, user_input="点检记录不许改；点错了要能改"
    )
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)

    await harness.integrate_round()(round_id=round_id)

    questions = harness.rounds.get(round_id).questionnaire.questions
    assert questions[0].kind is QuestionKind.CONFLICT
    assert [str(ref) for ref in questions[0].evidence_refs] == ["E1", "E2"]


# —— 续跑口径：重试不再盲调（E3，P13）——

#: 只交判断、不交疑问的席位回复：整合两次失败时**不会降级**，一定判死（才走得到重试那条路）
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


async def test_retrying_after_an_integration_failure_carries_the_last_error():
    """E3 剩余：重试不是"盲调"——上次为什么没通过，要递进第一次尝试。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": ["{这不是 JSON", "{这不是 JSON", questionnaire_packet()],
        },
        seat_replies={SEAT: JUDGMENT_ONLY_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    with pytest.raises(PacketParseError):
        await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    # 两条信息分开放：`failure.reason` 是**给我们看的**（几次都没过），
    # `retry_hint` 是**给模型看的**（上一版错在哪、该怎么改）
    assert "两次都过不了校验" in round_.failure.reason
    assert "上一次的报文没通过校验" in round_.retry_hint

    await harness.retry_round()(round_id=round_id)  # 失败 → resume 回 seats_running
    await harness.run_seat_round()(round_id=round_id)  # 席位齐了 → 一个模型调用都不发
    await harness.integrate_round()(round_id=round_id)  # 第三次脚本是好的 → 出问卷

    slots = [slots for name, slots in harness.prompts.calls if name == "integrator"][-1]
    assert "上一次的报文没通过校验" in slots["validation_error"]  # 第一次尝试就带上了
    assert harness.rounds.get(round_id).retry_hint is None  # 用过就清


async def test_retry_after_a_failed_integration_does_not_re_run_the_seats():
    """核验：09-28 报告那句"重试是整轮重来"在**今天**不成立——走真图，席位一个都不重跑。

    这条是**钉子**（T1/T2 之前就该成立）：`RunSeatRound` 只跑 `Round.pending_seats`，
    图的路由 `seats_running → seat_round → integrate` 让"已交回的不重跑"端到端生效。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": ["{这不是 JSON", "{这不是 JSON", questionnaire_packet()],
        },
        seat_replies={SEAT: JUDGMENT_ONLY_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    with pytest.raises(PacketParseError):
        await harness.integrate_round()(round_id=round_id)

    seats_before = harness.gateway.purposes.count("seat_review")

    await harness.runner().run(RoundCommand(kind="retry", round_id=str(round_id)))

    assert harness.gateway.purposes.count("seat_review") == seats_before  # 席位零重跑
    assert harness.rounds.get(round_id).retry_hint is None  # T1 的提示被用掉了
    assert harness.rounds.get(round_id).questionnaire is not None  # 接着跑到底了


# —— 清单项的去向：出草案那一刻落 s11（A2，P13）——


async def test_list_items_nobody_handled_land_in_s11_when_the_draft_is_written():
    """清单项有去处：没被处理的那条，出草案时落 s11（非阻塞、带去处、跨轮看得见）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet_with_a_list_item(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)  # 第一批：记下清单项
    first = harness.rounds.get(round_id).questionnaire
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
    await harness.integrate_round()(round_id=round_id)  # 第二批：出草案

    draft = harness.rounds.get(round_id).draft
    assert draft is not None
    s11 = [
        item.text for item in draft.proposals if str(item.target.section_id) == "s11"
    ]
    landed = [text for text in s11 if "模板字段要不要可配置" in text]
    assert len(landed) == 1
    assert not landed[0].startswith("〔未决·阻塞〕")  # C4：不堵交付
    assert landed[0].endswith("〔去处：s8〕")  # 带去处：下一轮 carried 问得回原章节
    assert "（未决定，待决人：PM；跳过后果：" in landed[0]  # s11 标准格式，能被解析回来
    assert any("清单项" in gap for gap in draft.gaps)  # C7：用户看得见


def test_a_list_item_the_draft_already_covers_is_not_written_to_s11():
    """已经处理过的清单项不再落一遍（机械判据：同去处 + 归一化文本）。"""

    from pmstudio.application.use_cases import _unhandled_list_items

    round_ = make_round()
    round_.remember_list_items(
        (
            ListItem(
                text="模板字段要不要可配置？", target=Target.body("s8"), reason="本批装不下"
            ),
        )
    )
    covered = Proposal(
        proposal_id=ProposalId("p01"),
        entry_id=EntryId("r1#01"),
        op=WriteOp.ADD,
        target=Target.body("s8"),
        text="模板字段要不要可配置？",
        origin=Origin(OriginType.USER, (EvidenceId("E1"),), "用户说过"),
        kind=BlockKind.TEXT,
    )

    assert _unhandled_list_items(round_, questions=(), proposals=(covered,)) == ()
    assert _unhandled_list_items(round_, questions=(), proposals=()) == round_.list_items


async def test_a_list_item_already_in_s11_is_not_landed_a_second_time():
    """同一句话已经在 s11 里 → 不再落一遍，只在 gaps 里说一句（A3-②：不静默丢）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [questionnaire_packet_with_a_list_item(), draft_packet()],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    # s11 里已经有一条**同名同去处**的未决条（它会在第一批被问回来）
    seed_open_item(
        harness,
        entry_id="r0#open01",
        text=(
            "第一条清单项：模板字段要不要可配置？（未决定，待决人：PM；跳过后果：先搁着）"
            "〔去处：s8〕"
        ),
    )

    await harness.integrate_round()(round_id=round_id)  # 第一批：问回它 + 记下同名清单项
    first = harness.rounds.get(round_id).questionnaire
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
    await harness.integrate_round()(round_id=round_id)  # 第二批：出草案

    draft = harness.rounds.get(round_id).draft
    s11 = [item.text for item in draft.proposals if str(item.target.section_id) == "s11"]
    assert [text for text in s11 if "模板字段要不要可配置" in text] == []  # 不再落第二遍
    assert any("已经在 s11" in gap for gap in draft.gaps)


# —— C4：出草案时算"连续几轮没采用席位提议"（P14）——


async def test_writing_a_draft_records_how_many_rounds_ignored_the_proposals():
    """判据在**历史**上：查最近几轮（只读投影）→ 纯函数算连续 → 存进这一轮。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    harness.index.set_round_outcomes(
        str(PROJECT),
        (
            RoundOutcomeView("r3", "done", 2, 0),
            RoundOutcomeView("r2", "done", 1, 0),
            RoundOutcomeView("r1", "done", 3, 0),
        ),
    )

    await harness.integrate_round()(round_id=round_id)

    assert harness.rounds.get(round_id).proposal_ignore_streak == 3
    view = await harness.get_round()(round_id=round_id)
    assert view.advisories[0].startswith("⚠ 最近连续 3 轮")


async def test_a_round_whose_history_took_a_proposal_has_no_streak():
    """最近的轮次里只要**采用过**一条，连续计数就停在它前面——到不了阈值，也就不提示。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": draft_packet()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    harness.index.set_round_outcomes(
        str(PROJECT),
        (
            RoundOutcomeView("r3", "done", 2, 0),
            RoundOutcomeView("r2", "done", 2, 1),  # 这一轮采用了 → 断了
        ),
    )

    await harness.integrate_round()(round_id=round_id)

    assert harness.rounds.get(round_id).proposal_ignore_streak == 1  # 只有最近那一轮算数
    view = await harness.get_round()(round_id=round_id)
    assert view.advisories == ()  # 1 < 3 → 不说


# —— C6：草案卡上的"继续深挖"（P14）——


async def test_the_deferred_queue_is_not_cleared_when_the_draft_is_written():
    """C6 的前置：出草案时轮内队列**留着**。

    以前这里是 `clear_deferred()`——题落进 s11 之后队列就空了，于是"继续深挖"只可能
    再端出一份一模一样的草案。现在队列留着，dig 之后它们会被 carried 真的问出来（见 T5 的用例）。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                questionnaire_packet(title="第一批的问题？"),
                questionnaire_packet(title="第二批的问题？"),
                # 第三趟：闸门（问卷已满两批）这时候响 → 5 题降级进 s11、2 题进轮内队列、出草案
                packet_with_questions(7, prefix="第三批"),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)  # 第一批问卷
    for _ in range(2):  # 两批问卷，全部按跳过走
        form = harness.rounds.get(round_id).questionnaire
        await harness.submit_answers()(
            round_id=round_id,
            form_id=str(form.form_id),
            answers=(),
            skipped=tuple(str(item.question_id) for item in form.questions),
        )
        await harness.run_seat_round()(round_id=round_id)
        await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert [item.title for item in round_.deferred] == ["第三批第 6 题", "第三批第 7 题"]
    assert round_.draft is not None
    # 队列里的题同时落了 s11（草案没确认之前，文档里什么都没有；这是给"不再深挖"的用户看的）
    s11 = [item.text for item in round_.draft.proposals if str(item.target.section_id) == "s11"]
    assert any("第三批第 6 题" in text for text in s11)


async def test_dig_makes_the_round_ask_the_questions_it_had_to_drop():
    """C6 的全部意义：dig 之后**又出问卷**，而且问的正是刚才没问上的那些题。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                questionnaire_packet(title="第一批的问题？"),
                questionnaire_packet(title="第二批的问题？"),
                packet_with_questions(7, prefix="第三批"),
                questionnaire_packet(title="深挖这一批？"),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    for _ in range(2):
        form = harness.rounds.get(round_id).questionnaire
        await harness.submit_answers()(
            round_id=round_id,
            form_id=str(form.form_id),
            answers=(),
            skipped=tuple(str(item.question_id) for item in form.questions),
        )
        await harness.run_seat_round()(round_id=round_id)
        await harness.integrate_round()(round_id=round_id)
    assert harness.rounds.get(round_id).step is Step.AWAITING_CONFIRM

    await harness.dig_round()(round_id=round_id)
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_ANSWERS
    assert round_.extra_batches == 1
    assert round_.draft is None
    assert round_.deferred == ()  # 队列里的两条这趟被 carried 问上了
    titles = [item.title for item in round_.questionnaire.questions]
    assert "第三批第 6 题" in titles  # 刚才没问上的题，这次真的问出来了


async def test_the_dig_command_goes_straight_to_a_new_questionnaire():
    """走真图：`dig` → 席位轮（**零调用**）→ 整合 → 新问卷。

    dig **不新开**席位轮：已交回的意见还在，用不着为"再问一批"重烧 3–6 次模型调用；
    它只花一次整合调用，把队列里的题端出来。
    """

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [
                questionnaire_packet(title="第一批的问题？"),
                questionnaire_packet(title="第二批的问题？"),
                packet_with_questions(7, prefix="第三批"),
                questionnaire_packet(title="深挖这一批？"),
            ],
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)
    await harness.integrate_round()(round_id=round_id)
    for _ in range(2):
        form = harness.rounds.get(round_id).questionnaire
        await harness.submit_answers()(
            round_id=round_id,
            form_id=str(form.form_id),
            answers=(),
            skipped=tuple(str(item.question_id) for item in form.questions),
        )
        await harness.run_seat_round()(round_id=round_id)
        await harness.integrate_round()(round_id=round_id)
    seats_before = harness.gateway.purposes.count("seat_review")

    await harness.runner().run(RoundCommand(kind="dig", round_id=str(round_id)))

    round_ = harness.rounds.get(round_id)
    assert harness.gateway.purposes.count("seat_review") == seats_before  # 席位零重跑
    assert round_.step is Step.AWAITING_ANSWERS
    assert [item.title for item in round_.questionnaire.questions][:2] == [
        "第三批第 6 题",
        "第三批第 7 题",
    ]


# —— P25：条目不存在不再判死整轮（与"去处写错"同一件） ——


def packet_with_a_missing_entry(*, only_bad: bool = False) -> str:
    """一份"能出草案"的报文，但第一条提案 `replace` 指到不存在的条目。"""

    proposals = [
        {
            "op": "replace",
            "entry_id": "r-不存在#99",
            "target": {
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "order-export",
                "field": "acceptance",
            },
            "text": "这条要改的条目根本不存在",
            "origin_type": "derived",
            "evidence_refs": ["E1"],
        }
    ]
    if not only_bad:
        proposals.append(
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
                "evidence_refs": ["E1", "E2"],
            }
        )
    return json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": proposals,
            # 两条席位意见都交代掉，免得归因补交代那条小调用插进来（那不是本用例要测的）
            "dropped_opinions": [
                {"handle": f"{SEAT}:O1", "reason": "本轮没有必须问用户的东西"},
                {"handle": f"{SEAT}:O2", "reason": "已经落在上面那条提案里"},
            ],
        },
        ensure_ascii=False,
    )


async def test_a_missing_entry_no_longer_degrades_the_round():
    """真跑现场（P24）：`replace` 指到不存在的条目 → 整包两次不过 → 整轮降级。

    现在：**丢那一条 + 留痕**，其余照常出草案；一次整包调用就够（不再重装）。
    """

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": packet_with_a_missing_entry()},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert harness.gateway.purposes.count("integration") == 1  # 不再重装
    assert round_.draft is not None  # 没降级
    assert [proposal.text for proposal in round_.draft.proposals] == [
        "无导出权限的用户不显示导出入口"
    ]
    assert not any("两次都过不了校验" in gap for gap in round_.draft.gaps)
    assert [
        (item.handle, item.reason) for item in round_.dropped if item.source.value == "assembly"
    ] == [("提案 #1", "要改的条目不存在：r-不存在#99")]


async def test_a_packet_whose_proposals_all_miss_their_entries_degrades():
    """**全是坏条目**：装配完什么都不剩 → 判成报文错，走既有的降级出口（不是 409）。"""

    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": packet_with_a_missing_entry(only_bad=True),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)
    # 先把功能授权掉：不然"没授权的功能"会生成一道决策题，装配完就不是"什么都不剩"
    # ——那是另一条路（人工拍板），本用例要测的是"全坏条目 → 降级"。
    authorize_order_export(harness, round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM  # 走的是降级，不是 409
    assert any("未归并" in gap for gap in round_.draft.gaps)
    # 席位那一条疑问原样落 s11（判断没落点，照旧算"没交代"——`02` §11.1 的口径）
    assert [proposal.target.section_id for proposal in round_.draft.proposals] == ["s11"]


async def test_an_empty_packet_still_takes_the_graceful_exit():
    """空包（`{}`）也一样：不再撞 `draft_of(None)` → ShapeError → 409，而是走降级。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": "{}"},
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await round_in_seat_pass(harness)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert any("未归并" in gap for gap in round_.draft.gaps)


async def test_an_empty_text_remove_no_longer_degrades_the_round():
    """P25 现场形状：模型写的 `remove` **文本是空的** → 整包两次不过校验 → 整轮降级。

    照收之后：一次整包调用就出草案，那条 `remove` 照旧生效（要撤下的条目真被撤下）。
    """

    reply = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "remove",
                    "entry_id": "en-open",
                    "target": {"zone": "body", "section_id": "s11"},
                    "text": "",
                    "origin_type": "system_default",
                    "evidence_refs": ["E1"],
                }
            ],
            "dropped_opinions": [
                {"handle": f"{SEAT}:O1", "reason": "本轮没有必须问用户的东西"},
                {"handle": f"{SEAT}:O2", "reason": "已经落在文档已有内容里"},
            ],
        },
        ensure_ascii=False,
    )
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": reply},
        seat_replies={SEAT: SEAT_REPLY},
    )
    harness.seed_project()
    # 先造一条"要被撤下"的 s11 条目：remove 的 entry_id 必须真存在
    document = harness.documents.get(PROJECT)
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-seed",
                entry_id=EntryId("en-open"),
                op=WriteOp.ADD,
                target=Target.body("s11"),
                text="这条未决项要被撤下",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.QUESTION,
            )
        ],
        now=harness.clock.now(),
        summary="种一条少人看的条目",
    )
    harness.documents.seed(document)
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert harness.gateway.purposes.count("integration") == 1  # 不再重装
    assert round_.draft is not None  # 没降级
    assert not any("两次都过不了校验" in gap for gap in round_.draft.gaps)
    assert [item.op for item in round_.draft.proposals] == [WriteOp.REMOVE]
