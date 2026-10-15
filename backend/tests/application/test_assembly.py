from dataclasses import replace

import pytest

from pmstudio.application import assembly
from pmstudio.application.agents import schemas
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    AnswerSet,
    Option,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import DropSource, Round
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.ids import (
    EntryId,
    EvidenceId,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.harness.errors import PacketParseError
from tests.support.builders import (
    NOW,
    ROUND_ID,
    SEAT,
    STD,
    document_with_feature,
    make_document,
    make_round,
    make_round_with_contradictions,
)

PROJECT = ProjectId("p1")
SECOND_SEAT = "seat.rules"


def ledger_with_two_entries() -> EvidenceLedger:
    """E1 用户输入 + E2 席位判断（判断带 seat_id，用它可以验出新功能题是谁问的）。"""

    ledger = EvidenceLedger.empty(PROJECT)
    e1 = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出应在 60 秒内完成",
        source=EvidenceSource(seat_id=SEAT, derived_from=(e1,), rationale="量级在 1 万级"),
        now=NOW,
    )
    return ledger


def packet(**overrides: object) -> schemas.IntegrationPacket:
    payload: dict[str, object] = {"questions": [], "list_items": [], "proposals": []}
    payload.update(overrides)
    return schemas.IntegrationPacket.model_validate(payload)


def proposal_draft(
    *, field: str = "acceptance", text: str = "5 万条内 60 秒可下载"
) -> dict:
    return {
        "op": "add",
        "target": {
            "zone": "body",
            "section_id": "s5",
            "feature_slug": "order-export",
            "field": field,
        },
        "text": text,
        "origin_type": "derived",
        "evidence_refs": ["E1", "E2"],
        "rationale": "量级在 1 万级",
    }


def test_assemble_assigns_ids_and_field_kind():
    result = assembly.assemble(
        packet(proposals=[proposal_draft()]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    proposal = result.proposals[0]
    assert str(proposal.proposal_id) == "p01"
    assert str(proposal.entry_id) == f"{ROUND_ID}#01"
    assert proposal.op is WriteOp.ADD
    assert proposal.kind.value == "acceptance"  # 字段类型查 schema 表，不信任模型给的
    assert proposal.origin.refs == (EvidenceId("E1"), EvidenceId("E2"))


def test_a_section_level_proposal_for_s6_lands_as_steps_whatever_the_model_wrote():
    """第 6 章的章节级内容**只有一种形态**：`steps`——模型写 `text`（或干脆不写）都按 steps 落。

    为什么由**系统**盖章，而不是继续改提示词（P36 真跑实测）：同一个用例跑了 6 次，
    2 次把流程写成 `kind=text`、1 次一条 s6 提案都没出；写成 text 的后果是
    `01` §4 那条判据不认（"有功能却没有任何 steps 条目 → warn"照旧挂）、前端也不按步骤排。
    这与矛盾题的 `conflict` 由 `_covers_a_contradiction` **机械盖章**是同一个先例：
    `kind` 是**结构事实**，不该赌模型记得写。
    """

    def s6_draft(kind: str | None) -> dict:
        draft: dict = {
            "op": "add",
            "target": {"zone": "body", "section_id": "s6"},
            "text": "1. 点检员点检；2. 不合格转报修；3. 主管派单。",
            "origin_type": "derived",
            "evidence_refs": ["E1"],
        }
        if kind is not None:
            draft["kind"] = kind
        return draft

    for written in ("text", None):
        result = assembly.assemble(
            packet(proposals=[s6_draft(written)]),
            round_=make_round(),
            document=document_with_feature(),
            ledger=ledger_with_two_entries(),
        )
        assert result.proposals[0].kind is BlockKind.STEPS, f"模型写的是 {written!r}"

    # 别的章不跟着变：章节级没写 kind 仍然是普通正文
    result = assembly.assemble(
        packet(
            proposals=[
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": "现状是纸单子。",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                }
            ]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )
    assert result.proposals[0].kind is BlockKind.TEXT


def round_with_two_seats_attested() -> Round:
    """两个席位都交回了意见的那一轮：归因要能落到"哪条意见"这一层。"""

    round_ = make_round(seats=(SEAT, SECOND_SEAT))
    round_.attest(
        SeatReview(
            seat_id=SEAT,
            basis="读了 E1",
            opinions=(
                Opinion(
                    local_id="O1",
                    form=OpinionForm.QUESTION,
                    about="导出的数据量级",
                    claim="量级决定同步还是异步，必须问用户",
                    because="1 万以内可同步，10 万以上必须异步",
                    evidence_refs=(EvidenceId("E1"),),
                ),
                Opinion(
                    local_id="O2",
                    form=OpinionForm.JUDGMENT,
                    about="导出的性能上限",
                    claim="单次导出应在 60 秒内完成",
                    because="量级在 1 万级",
                    evidence_refs=(EvidenceId("E1"),),
                ),
            ),
        )
    )
    round_.attest(
        SeatReview(
            seat_id=SECOND_SEAT,
            basis="读了 s5",
            opinions=(
                Opinion(
                    local_id="O1",
                    form=OpinionForm.QUESTION,
                    about="谁能看导出文件",
                    claim="导出文件的可见范围必须问用户",
                    because="涉及数据权限",
                    evidence_refs=(EvidenceId("E1"),),
                ),
            ),
        )
    )
    return round_


def question_draft(**overrides: object) -> dict:
    draft: dict[str, object] = {
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
    }
    draft.update(overrides)
    return draft


def test_question_asked_by_comes_from_from_opinions():
    """`from_opinions` 写的是席位条目地址——归因由它决定，不由模型另写一份 asked_by。"""

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(
                    evidence_refs=[], from_opinions=[f"{SECOND_SEAT}:O1"]
                )
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions[0].asked_by == (SECOND_SEAT,)
    assert result.dropped == ()


def test_question_asked_by_also_comes_from_the_cited_evidence():
    """引用了某个席位的判断（E2 带 seat_id），就等于说这条问题出在那个席位。"""

    result = assembly.assemble(
        packet(questions=[question_draft(evidence_refs=["E2"], from_opinions=[])]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions[0].asked_by == (SEAT,)


def test_question_numbering_continues_across_batches_of_the_same_round():
    """题号按**轮次**发：第一批已经问了 2 题 ⇒ 这一批从 q3 起（2026-09-29 拍板 D4）。

    原来每批都从 `q1` 重排，同一个轮次里 `q1` 有两个意思，六处"按题号回查"就全指错
    （真跑里第二批的答案挂着第一批的题面与编号，见计划 §0.2）。
    """

    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.TEXT,
                    title="第一批第一题？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                ),
                Question(
                    question_id=QuestionId("q2"),
                    target=Target.body("s8"),
                    type=QuestionType.TEXT,
                    title="第一批第二题？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                ),
            ),
        )
    )

    result = assembly.assemble(
        packet(questions=[question_draft(), question_draft(title="第二道新题？")]),
        round_=round_,
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [str(item.question_id) for item in result.questions] == ["q3", "q4"]


def round_with_two_batches_skipped(*, round_id: str = "r1") -> Round:
    """一轮两批，两批的题**都被跳过**：s11 里的题面要各回各的批。"""

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
        questionnaire_history=(first, second),
        questionnaire=second,
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


def test_skipped_questions_keep_their_own_batchs_title():
    """跳过的题落 s11 时，题面与跳过后果要回到**出题那一批**去找（计划 §0.1 第 3 处）。

    老实现用**当前批**的问卷反查题号：第一批的 `q1` 在第二批的问卷里不存在，于是这条被静默丢掉，
    同一件事在文档里查不到。现在按 `(表单, 题号)` 回历史里找。
    """

    items = assembly.open_question_proposals(
        round_with_two_batches_skipped(),
        system_defaults={
            QuestionId("q1"): EvidenceId("E1"),
            QuestionId("q2"): EvidenceId("E2"),
        },
        start_index=1,
    )

    assert len(items) == 2
    assert "第一批的题：最大行数？" in items[0].text
    assert "按同步实现" in items[0].text
    assert "第二批的题：对端不可用时怎么降级？" in items[1].text


def round_that_already_asked(title: str, *, target: Target | None = None) -> Round:
    """这一轮已经问过一道题（题号 q1），用来验跨批去重。"""

    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=target or Target.body("s8"),
                    type=QuestionType.TEXT,
                    title=title,
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                ),
            ),
        )
    )
    return round_


def test_a_question_already_asked_this_round_is_not_asked_again():
    """同一轮里问过的题不再问第二遍——**已答与已跳过都算问过**（2026-09-29 拍板 D2）。

    真跑里第一批量级那道题在第二批被原样再问一遍（报告 F6）。去重原来只看"carried + 本批"，
    历史里问过的题不在种子里。
    """

    result = assembly.assemble(
        packet(questions=[question_draft()]),  # 同一个去处 + 同一个问法
        round_=round_that_already_asked("单次导出的最大行数是多少？"),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions == ()
    assert result.deferred == ()


def test_a_question_asked_twice_in_one_batch_leaves_a_trace():
    """**同一批里**问了两遍的那一份要留痕——丢东西却什么都不说，是整个装配层
    唯一一处这样的地方（同类丢弃：去处写错 `:646`、条目不存在 `:567`、让位 `:671`
    全都留痕）。

    后果有两层：模型收不到任何提示、它这一趟的 token 白烧了；系统也没留下证据，
    于是 `no_duplicate_questions` 判据读问卷（去重**之后**的产物）恒真，测不到
    任何模型行为（2026-10-09 真跑确认：10/10 通过、零鉴别力）。
    """

    result = assembly.assemble(
        packet(questions=[question_draft(), question_draft()]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert len(result.questions) == 1
    assert len(result.dropped) == 1


def test_the_duplicate_trace_says_which_question_was_dropped_and_why():
    """留痕要能回答"丢的是哪一道、为什么丢"：题目面 + 一句原因 + 报文里的第几条。

    `handle` 用 `题目 #N`——与让位那条留痕（`:671`）同一格式，不另发明。
    """

    result = assembly.assemble(
        packet(questions=[question_draft(), question_draft()]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    (trace,) = result.dropped
    assert trace.handle == "题目 #2"
    assert trace.source is DropSource.ASSEMBLY
    assert "单次导出的最大行数是多少？" in trace.reason
    assert "同一件事" in trace.reason


def test_a_question_the_model_wrote_twice_in_one_packet_is_still_its_fault():
    """**同一包里写两遍**仍然是模型的错——判"该不该算它的错"不能一刀切。

    这是 2026-10-09 拆分留痕时踩到的洞：第一版把"撞用户已表过态的题"当唯一例外，
    结果把这一类比成"正确行为"了。而它明明是模型自己在一份输出里重复了
    （真跑 `r-b6eb3c45` 那 3 条里最后一条正是这种：第 2 批的报文自己重了）。

    所以分的是**三种**：自己写重 / 问出去过而用户没碰过（提示词里没有它）/
    其余（模型本来就知道）。
    """

    result = assembly.assemble(
        packet(questions=[question_draft(), question_draft()]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    (trace,) = result.dropped
    assert "同一件事问了两遍" in trace.reason


def test_a_repeat_of_a_question_the_user_already_answered_is_not_the_models_fault():

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(title="谁能执行导出？", target={"zone": "body", "section_id": "s3"})
            ]
        ),
        round_=round_after_skipping_a_question(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions == ()
    (trace,) = result.dropped
    assert trace.source is DropSource.ASSEMBLY
    assert "又问了一遍" in trace.reason
    assert "谁能执行导出？" in trace.reason
    # 判据只认"模型看不见"那一类，所以这句话不能被它当成红灯
    assert "同一件事问了两遍" not in trace.reason


def test_the_same_place_asked_differently_is_not_a_duplicate():
    """挪一个问法就不是同一道题：同一去处上的另一件事照问（别把去重做过头）。"""

    result = assembly.assemble(
        packet(questions=[question_draft(title="单次导出最多能有多少行？")]),
        round_=round_that_already_asked("单次导出的最大行数是多少？"),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [str(item.question_id) for item in result.questions] == ["q2"]


def test_the_same_question_with_a_different_trailing_punctuation_is_a_duplicate():
    """**标点不算问法**（P28 T0 真案例）：同一去处、同一句话，只差一个句末问号 → 同一道题。

    真跑现场（`.codex/runs/2026-10-02/P28-T0测量.md` §2.2）：模型同一轮里出了两份
    「会员请假审批的时限与超时转交规则？／…规则」（少一个问号），一份进了问卷、一份被降级成 s11，
    下一轮又把它当 carried 问了一遍——用户被同一件事问了两次。
    """

    result = assembly.assemble(
        packet(questions=[question_draft(title="单次导出的最大行数是多少")]),  # 少一个问号
        round_=round_that_already_asked("单次导出的最大行数是多少？"),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions == ()
    assert result.deferred == ()


def test_question_key_ignores_surrounding_punctuation_only():
    """归一化只动**首尾标点**：中间的问号、破折号、括号一律保留（那是不同的问法）。"""

    def key(title: str) -> tuple[str, str]:
        return assembly.question_key(
            Question(
                question_id=QuestionId("q1"),
                target=Target.body("s8"),
                type=QuestionType.TEXT,
                title=title,
                why="验判重键",
                consequence_if_skipped="按默认走",
            )
        )

    assert key("最大行数是多少？") == key("最大行数是多少")
    assert key(" 最大行数是多少？！ ") == key("最大行数是多少")
    assert key("“最大行数是多少”") == key("最大行数是多少")
    assert key("最大行数是多少？还是多少？") != key("最大行数是多少")


def test_question_from_unparsable_opinion_is_dropped_not_fatal():
    """归因解析不出来只丢归因、留痕，不判死整轮（题目本身可能没问题）。"""

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(
                    evidence_refs=[],
                    from_opinions=["seat.nope:O1", f"{SECOND_SEAT}:O9"],
                )
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert len(result.questions) == 1
    assert result.questions[0].asked_by == ()
    assert [(item.handle, item.source) for item in result.dropped] == [
        ("seat.nope:O1", DropSource.ASSEMBLY),
        (f"{SECOND_SEAT}:O9", DropSource.ASSEMBLY),
    ]


def test_question_is_blocking_when_target_hits_a_required_feature_field():
    result = assembly.assemble(
        packet(
            questions=[
                question_draft(
                    target={
                        "zone": "body",
                        "section_id": "s5",
                        "feature_slug": "order-export",
                        "field": "acceptance",
                    }
                )
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions[0].blocking is True


def test_question_is_not_blocking_for_a_non_blocking_section_or_preference():
    assert assembly.is_blocking(Target.body("s7")) is False
    assert assembly.is_blocking(Target.body("s8", field="performance")) is True
    assert assembly.is_blocking(Target.preference()) is False


def test_skipped_blocking_question_lands_in_s11_with_the_marker():
    blocking = Question(
        question_id=QuestionId("q1"),
        target=Target.feature_field("order-export", "acceptance", home=STD.feature_home),
        type=QuestionType.TEXT,
        title="单次导出的最大行数是多少？",
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现，超时风险由 PM 承担",
        blocking=True,
    )
    plain = Question(
        question_id=QuestionId("q2"),
        target=Target.body("s7"),
        type=QuestionType.TEXT,
        title="导出文件要不要留档？",
        why="影响存储与合规",
        consequence_if_skipped="先不留档",
    )

    marked = assembly.open_question_proposal(
        blocking, eid=EvidenceId("E9"), index=1, round_id=ROUND_ID, rationale="跳过的题按题面后果"
    )
    plain_proposal = assembly.open_question_proposal(
        plain, eid=EvidenceId("E10"), index=2, round_id=ROUND_ID, rationale="跳过的题按题面后果"
    )

    assert marked.text.startswith("〔未决·阻塞〕")
    assert not plain_proposal.text.startswith("〔未决·阻塞〕")
    assert marked.target.section_id == "s11"
    assert marked.kind is BlockKind.QUESTION


def test_assemble_creates_feature_subtree_only_when_authorized():
    result = assembly.assemble(
        packet(proposals=[proposal_draft()]),
        round_=make_round(),  # 这一轮没有授权 order-export
        document=make_document(),  # 空文档：既没有功能，也没有授权
        ledger=ledger_with_two_entries(),
    )

    assert result.proposals == ()
    assert len(result.questions) == 1
    question = result.questions[0]
    assert question.kind is QuestionKind.DECISION
    assert question.type is QuestionType.SINGLE
    assert [option.option_id for option in question.options] == [
        assembly.NEW_FEATURE_YES,
        assembly.NEW_FEATURE_NO,
    ]
    assert str(question.target.feature_slug) == "order-export"
    assert question.asked_by == (SEAT,)


def test_assemble_keeps_first_five_questions_and_defers_the_rest():
    """前 5 题进问卷，其余进**轮内队列**（不是 list_items——那个没人读，真跑里它们会消失）。"""

    drafts = [
        {
            "kind": "gap",
            "title": f"问题 {index}？",
            "why": "只有用户知道",
            "consequence_if_skipped": "先按默认处理",
            "target": {"zone": "body", "section_id": "s8"},
            "type": "single",
            "options": [{"option_id": "a", "text": "甲"}, {"option_id": "b", "text": "乙"}],
            "evidence_refs": ["E1"],
            "from_opinions": [f"{SEAT}:O1"],
        }
        for index in range(1, 8)
    ]
    result = assembly.assemble(
        packet(questions=drafts),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [str(question.question_id) for question in result.questions] == [
        "q1",
        "q2",
        "q3",
        "q4",
        "q5",
    ]
    assert [item.title for item in result.deferred] == ["问题 6？", "问题 7？"]
    assert result.list_items == ()


def test_assemble_rejects_unknown_evidence_refs():
    bad = proposal_draft()
    bad["evidence_refs"] = ["E77"]
    with pytest.raises(PacketParseError) as exc:
        assembly.assemble(
            packet(proposals=[bad]),
            round_=make_round(),
            document=document_with_feature(),
            ledger=ledger_with_two_entries(),
        )
    assert "E77" in exc.value.message


def test_assemble_a_replace_with_a_missing_entry_is_dropped_with_a_trace():
    """条目 id 写错（指到不存在的条目）→ 丢**这一条** + 留痕，不放整轮失败。

    与"去处写错"完全同一件（`02` §11.0.2，2026-09-28 拍板）：一次笔误不该让整批白干。
    真跑现场（P24）：模型两次都写了不存在的条目 id → 整包两次过不了校验 → 整轮降级，
    12 条判断全成"没交代"。
    """

    bad = proposal_draft()
    bad["op"] = "replace"
    bad["entry_id"] = "不存在的条目"
    good = proposal_draft(text="5 万条内 60 秒可下载")
    result = assembly.assemble(
        packet(proposals=[bad, good]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [item.text for item in result.proposals] == ["5 万条内 60 秒可下载"]  # 好的照进
    assert [
        (item.handle, item.source.value) for item in result.dropped
    ] == [("提案 #1", "assembly")]
    assert "不存在的条目" in result.dropped[0].reason


def test_assemble_a_remove_with_a_missing_entry_is_dropped_too():
    bad = proposal_draft()
    bad["op"] = "remove"
    bad["entry_id"] = "也不存在"
    result = assembly.assemble(
        packet(proposals=[bad, proposal_draft()]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert len(result.proposals) == 1
    assert [item.handle for item in result.dropped] == ["提案 #1"]


def test_a_replace_without_an_entry_id_is_still_a_packet_error():
    """反方向不放松：**字段没给**（不是写错）照旧判死——`entry_id` 是写入语义字段，
    在 `_WRITE_SEMANTICS_FIELDS` 黑名单里，不许用默认值悄悄改语义。"""

    bad = proposal_draft()
    bad["op"] = "replace"
    with pytest.raises(PacketParseError):
        assembly.assemble(
            packet(proposals=[bad]),
            round_=make_round(),
            document=document_with_feature(),
            ledger=ledger_with_two_entries(),
        )


def test_an_add_with_empty_text_is_dropped_with_a_trace():
    """`add` / `replace` 的空文本：没有内容可写 → 丢**这一条** + 留痕（不判死整轮）。"""

    bad = proposal_draft(text="   ")
    result = assembly.assemble(
        packet(proposals=[bad, proposal_draft(text="5 万条内 60 秒可下载")]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [item.text for item in result.proposals] == ["5 万条内 60 秒可下载"]
    assert [item.handle for item in result.dropped] == ["提案 #1"]
    assert "文本为空" in result.dropped[0].reason


def test_a_remove_with_empty_text_fills_in_what_it_deletes():
    """`remove` 的空文本**照收**，并补上**被撤下那条的正文**（从文档里引用，不是编内容）。

    真跑实测（5 个库 22 处空文本提案）**全部是 `remove`**：模型要撤下 s11 的未决条目时
    常常不写文本。丢掉它会**让那条该撤下的内容留在文档里**，所以不能丢；补正文之后
    卡片上是「删除｜<那句话>」而不是一行空白，也守住"每条写入指令都有文本"的不变量。
    """

    remove = proposal_draft()
    remove["op"] = "remove"
    remove["entry_id"] = "en-bootstrap"
    remove["text"] = ""
    result = assembly.assemble(
        packet(proposals=[remove]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [item.op for item in result.proposals] == [WriteOp.REMOVE]
    assert result.proposals[0].text == "把订单导出成表格"  # en-bootstrap 那条的正文
    assert result.dropped == ()


def test_a_remove_is_not_skipped_as_a_duplicate():
    """去重只对 `add` 生效：remove 的文本正好照抄了被删那条时，不许被当成"重复"跳过。

    跳过的后果与上面相反——本该撤下的内容悄悄留在文档里。
    """

    remove = proposal_draft(text="把订单导出成表格")  # 与被删那条一字不差
    remove["op"] = "remove"
    remove["entry_id"] = "en-bootstrap"
    result = assembly.assemble(
        packet(proposals=[remove]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [item.op for item in result.proposals] == [WriteOp.REMOVE]


def test_assemble_replace_accepts_real_entry_id():
    ok = proposal_draft(text="把订单导出成表格（可筛选）")
    ok["op"] = "replace"
    ok["entry_id"] = "en-bootstrap"
    result = assembly.assemble(
        packet(proposals=[ok]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )
    assert result.proposals[0].op is WriteOp.REPLACE
    assert str(result.proposals[0].entry_id) == "en-bootstrap"


def test_assemble_drops_proposal_that_duplicates_the_document():
    duplicate = proposal_draft(field="one_liner", text="把订单导出成表格")
    duplicate["kind"] = "text"
    result = assembly.assemble(
        packet(proposals=[duplicate]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )
    assert result.proposals == ()
    assert result.questions == ()


def test_questionnaire_of_returns_none_without_questions():
    assert assembly.questionnaire_of(ROUND_ID, FormId("f1"), (), ()) is None


def round_after_skipping_a_question():
    """走一遍"出问卷 → 跳过那道题"，回到席位轮。"""

    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s3"),
                    type=QuestionType.SINGLE,
                    title="谁能执行导出？",
                    why="决定权限口径",
                    consequence_if_skipped="默认只有运营专员",
                    options=(Option("a", "运营专员"), Option("b", "运营 + 主管")),
                ),
            ),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(),
            skipped=(QuestionId("q1"),),
            answered_at=NOW,
        )
    )
    return round_


def test_open_question_proposals_use_system_default_evidence_and_s11():
    proposals = assembly.open_question_proposals(
        round_after_skipping_a_question(),
        system_defaults={QuestionId("q1"): EvidenceId("E9")},
        start_index=3,
    )

    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.origin.type is OriginType.SYSTEM_DEFAULT
    assert proposal.origin.refs == (EvidenceId("E9"),)
    assert str(proposal.target.section_id) == "s11"
    assert str(proposal.proposal_id) == "p03"
    assert str(proposal.entry_id) == f"{ROUND_ID}#open03"
    assert proposal.kind.value == "question"
    assert "谁能执行导出？" in proposal.text
    assert "默认只有运营专员" in proposal.text


def test_open_question_proposals_bad_missing_system_default_eid():
    with pytest.raises(PacketParseError) as exc:
        assembly.open_question_proposals(
            round_after_skipping_a_question(), system_defaults={}, start_index=1
        )
    assert exc.value.details["question_id"] == "q1"


# —— P5b Task 2：丢弃留痕的三个来源 ——


def ledger_with_own_eid_for_the_judgment() -> EvidenceLedger:
    """E1 用户输入 + E2 席位判断，且判断带 `local_id`（find_by_local 才反查得到它）。

    形状照抄真实写法：`local_id=f"p{pass}:{O1}"`——汇合点的幂等编号键。
    """

    ledger = EvidenceLedger.empty(PROJECT)
    e1 = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="导出要快",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="导出要快一点"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出应在 60 秒内完成",
        source=EvidenceSource(
            seat_id=SEAT,
            local_id="p1:O2",
            derived_from=(e1,),
            rationale="量级在 1 万级",
        ),
        now=NOW,
    )
    return ledger


def test_packet_declared_drops_land_in_the_channel():
    """模型申报的丢弃：原因照抄、席位按地址前缀解析；写歪了也留痕，只是认不出席位。"""

    declared = schemas.IntegrationPacket(
        dropped_opinions=[
            {"handle": f"{SEAT}:O2", "reason": "与文档已有内容重复"},
            {"handle": "查不到:O9", "reason": "写歪了地址"},
        ]
    )

    drops = assembly.dropped_from_packet(
        declared,
        reviews=round_with_two_seats_attested().current_reviews,
        pass_index=1,
    )

    assert [(item.handle, item.reason, item.source, item.seat_id) for item in drops] == [
        (f"{SEAT}:O2", "与文档已有内容重复", DropSource.INTEGRATOR, SEAT),
        ("查不到:O9", "写歪了地址", DropSource.INTEGRATOR, None),
    ]


def test_unaccounted_opinion_is_reported():
    """整合既没说丢了它、也没交代它落到哪儿 → 装配层替它留痕。"""

    drops = assembly.unaccounted_drops(
        round_with_two_seats_attested().current_reviews,
        ledger=ledger_with_own_eid_for_the_judgment(),
        accounted_handles=frozenset(),
        accounted_eids=frozenset(),
        declared=frozenset(),
    )

    assert [(item.handle, item.source, item.pass_index) for item in drops] == [
        (f"{SEAT}:O1", DropSource.UNACCOUNTED, 1),
        (f"{SEAT}:O2", DropSource.UNACCOUNTED, 1),
        (f"{SECOND_SEAT}:O1", DropSource.UNACCOUNTED, 1),
    ]


def test_an_unaccounted_opinion_says_what_it_was_about():
    """F5：光说"没人交代它"没法查——把意见本身带进留痕，用户才知道丢的是哪句话。"""

    review = SeatReview(
        seat_id=SEAT,
        basis="读了 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.JUDGMENT,
                about="导出的数据量级",
                claim="量级决定同步还是异步，必须问用户",
                because="1 万以内可同步，10 万以上必须异步",
                evidence_refs=(EvidenceId("E1"),),
            ),
        ),
    )

    items = assembly.unaccounted_drops(
        (review,),
        ledger=ledger_with_two_entries(),
        accounted_handles=frozenset(),
        accounted_eids=frozenset(),
        declared=frozenset(),
    )

    assert "导出的数据量级" in items[0].reason  # opinion.about
    assert "量级决定同步还是异步" in items[0].reason  # opinion.claim
    # 尾句按**形态**分两句（2026-10-01 F19）：原话术"既没说丢掉它，也没交代它落到了哪儿"
    # 一律套用，用户看了等于没看。这里这条是判断，所以说的是"没进任何提案或题目"。
    # 要守的意图没变：尾句说明的是**整合者没交代**，不是"这条意见没价值"。
    assert "没进任何提案或题目" in items[0].reason
    assert "整合也没申报丢弃" in items[0].reason


def test_a_long_unaccounted_claim_is_truncated_in_the_trace():
    """留痕是给人看的：长句截断，别把闭环区刷成一屏。"""

    review = SeatReview(
        seat_id=SEAT,
        basis="读了 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.QUESTION,
                about="导出的数据量级",
                claim="量" * 200,
                because="理由",
                evidence_refs=(EvidenceId("E1"),),
            ),
        ),
    )

    items = assembly.unaccounted_drops(
        (review,),
        ledger=ledger_with_two_entries(),
        accounted_handles=frozenset(),
        accounted_eids=frozenset(),
        declared=frozenset(),
    )

    assert "量" * 40 in items[0].reason
    assert "量" * 60 not in items[0].reason


def test_opinion_cited_by_a_question_is_not_reported():
    """地址被某道题的 from_opinions 点过 → 算交代过。"""

    drops = assembly.unaccounted_drops(
        round_with_two_seats_attested().current_reviews,
        ledger=ledger_with_own_eid_for_the_judgment(),
        accounted_handles=frozenset({f"{SEAT}:O1", f"{SEAT}:O2", f"{SECOND_SEAT}:O1"}),
        accounted_eids=frozenset(),
        declared=frozenset(),
    )

    assert drops == ()


def test_judgment_referenced_by_a_proposal_is_not_reported():
    """判断自己的编号被引用就算交代过——哪怕题目没写 from_opinions。"""

    drops = assembly.unaccounted_drops(
        round_with_two_seats_attested().current_reviews,
        ledger=ledger_with_own_eid_for_the_judgment(),
        accounted_handles=frozenset({f"{SEAT}:O1", f"{SECOND_SEAT}:O1"}),
        accounted_eids=frozenset({EvidenceId("E2")}),  # ← 那条判断自己的编号
        declared=frozenset(),
    )

    assert drops == ()


def test_declared_opinion_is_not_reported_twice():
    """模型申报过的，不许再报一条"没交代"（同一件事只留一次痕）。"""

    drops = assembly.unaccounted_drops(
        round_with_two_seats_attested().current_reviews,
        ledger=ledger_with_own_eid_for_the_judgment(),
        accounted_handles=frozenset(),
        accounted_eids=frozenset(),
        declared=frozenset({f"{SEAT}:O1", f"{SEAT}:O2", f"{SECOND_SEAT}:O1"}),
    )

    assert drops == ()


# —— 真模型实测踩到：章节槽（P5a）在报文层不可达 ——


def test_section_slot_target_is_accepted():
    """整合者往 s8 的章节槽里写（`{"section_id": "s8", "field": "compatibility"}`）必须能过。

    真模型实测：报文层要求"field 必须配 feature_slug"，于是整合两次都失败、整轮降级；
    P5a 定义的六个槽对 AI 路径根本不可达。
    """

    result = assembly.assemble(
        packet(
            proposals=[
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8", "field": "compatibility"},
                    "text": "只支持 Chrome 与 Safari 最近两个大版本",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                }
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    proposal = result.proposals[0]
    assert str(proposal.target.section_id) == "s8"
    assert proposal.target.field == "compatibility"
    assert proposal.kind is BlockKind.NFR  # 槽的类型来自槽表，不信模型给的 kind


def test_section_slot_with_unknown_name_is_rejected():
    """写错槽名不是"猜一个"：那一条被丢掉并留痕，**同一批里其它提案照常处理**。"""

    result = assembly.assemble(
        packet(
            proposals=[
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8", "field": "不存在的槽"},
                    "text": "随便写点什么",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                },
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s8", "field": "performance"},
                    "text": "5 万条 60 秒内可下载",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                },
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [proposal.text for proposal in result.proposals] == ["5 万条 60 秒内可下载"]
    assert len(result.dropped) == 1
    dropped = result.dropped[0]
    assert dropped.source is DropSource.ASSEMBLY
    assert dropped.seat_id is None
    assert dropped.handle == "提案 #1"
    assert "去处不合法" in dropped.reason and "不存在的槽" in dropped.reason


def test_bad_target_question_is_dropped_while_others_survive():
    """题目写错去处也一样：丢那一条、留痕，别的题照常出。"""

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(
                    target={"zone": "body", "section_id": "s2", "field": "goal"},
                    title="这题去处写错了",
                ),
                question_draft(title="这题去处是对的"),
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [question.title for question in result.questions] == ["这题去处是对的"]
    assert [(item.handle, item.source) for item in result.dropped] == [
        ("题目 #1", DropSource.ASSEMBLY)
    ]
    assert "去处不合法" in result.dropped[0].reason


def test_bad_target_in_a_list_item_is_dropped_too():
    """清单项（本批装不下的那些）写错去处也照此办理：丢一条、留痕。"""

    result = assembly.assemble(
        packet(
            list_items=[
                {
                    "text": "这条去处写错了",
                    "target": {"zone": "body", "section_id": "s1", "field": "one_liner"},
                    "reason": "本批只问 5 题，这条留到下一轮",
                }
            ]
        ),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.list_items == ()
    assert [item.handle for item in result.dropped] == ["清单项 #1"]


def test_bad_evidence_ref_is_still_fatal():
    """边界：**去处**写错可以降级，**编号**写错仍然整轮失败（内容没有出处 = 不可追溯）。"""

    with pytest.raises(PacketParseError):
        assembly.assemble(
            packet(
                proposals=[
                    {
                        "op": "add",
                        "target": {"zone": "body", "section_id": "s2"},
                        "text": "引用了不存在的编号",
                        "origin_type": "derived",
                        "evidence_refs": ["E404"],
                    }
                ]
            ),
            round_=round_with_two_seats_attested(),
            document=document_with_feature(),
            ledger=ledger_with_two_entries(),
        )


# —— 2026-09-28 拍板：`origin.type` 按**账本**算，不采信模型自报 ——


def ledger_with_an_untraceable_judgment() -> EvidenceLedger:
    """E1 用户输入 + E2 席位判断**自己没有任何依据**（真模型实测里 E9/E10 就是这个形状）。

    这种判断在账本里是 `agent_proposed`：它是对的，但回溯不到用户输入——
    按 `02` §4.1 的设计，用它的内容只能以"它提议的"出现（默认不勾）。
    """

    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_PROPOSED,
        text="所有外部调用要接链路追踪",
        source=EvidenceSource(seat_id=SEAT, rationale="纯专业判断：外部调用没有追踪就只能靠猜"),
        now=NOW,
    )
    return ledger


def slot_proposal(*, refs: list[str], origin_type: str = "derived") -> dict:
    """一条落到 s8 章节槽的提案（不用新功能授权，所以能直接进草案）。"""

    return {
        "op": "add",
        "target": {"zone": "body", "section_id": "s8", "field": "performance"},
        "text": "5 万条 60 秒内可下载",
        "origin_type": origin_type,
        "evidence_refs": refs,
        "rationale": "量级在 1 万级",
    }


def test_untraceable_derived_proposal_is_downgraded_to_proposed():
    """模型说"derived"，但依据全是席位自己的判断（回溯不到用户输入）→ 降级成"它提议的"。

    真模型实测：这类提案进了"它替你判断的"那组，用户勾了、提交时才撞
    `invariant:E4 回溯不到用户输入或问卷答案`——卡片上一个字都没提示。
    """

    result = assembly.assemble(
        packet(proposals=[slot_proposal(refs=["E2"])]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_an_untraceable_judgment(),
    )

    assert [proposal.origin.type for proposal in result.proposals] == [
        OriginType.PROPOSED
    ]


def test_traceable_derived_proposal_stays_derived():
    """能回溯到用户输入的，照旧是"它替你判断的"（不误伤）。"""

    result = assembly.assemble(
        packet(proposals=[slot_proposal(refs=["E2"])]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),  # E2 是 agent_derived，链上有 E1
    )

    assert [proposal.origin.type for proposal in result.proposals] == [
        OriginType.DERIVED
    ]


def test_user_origin_is_checked_too():
    """模型说"user"，依据却全是席位判断：同样是撒谎，一样降级。"""

    result = assembly.assemble(
        packet(proposals=[slot_proposal(refs=["E2"], origin_type="user")]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_an_untraceable_judgment(),
    )

    assert [proposal.origin.type for proposal in result.proposals] == [
        OriginType.PROPOSED
    ]


def test_proposal_with_zero_padded_ref_passes():
    """真跑实测：把 `E8` 写成 `E08` 会让整轮 502（报告 F3）——归一化后照常进卡片。"""

    result = assembly.assemble(
        packet(proposals=[slot_proposal(refs=["E002"])]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),  # 账本里是 E2
    )

    assert [proposal.origin.refs for proposal in result.proposals] == [
        (EvidenceId("E2"),)
    ]


# —— 轮内未决队列（P9）：超上限的题进 deferred，carried 题优先问 ——


def carried_question(title: str = "上一批没问上的题") -> Question:
    return Question(
        question_id=QuestionId("q0"),
        target=Target.body("s8"),
        type=QuestionType.TEXT,
        title=title,
        why="本批上限，留到下一批",
        consequence_if_skipped="继续留在队列里",
    )


def test_overflow_questions_are_returned_as_deferred_not_list_items():
    """真跑实测：它们原来被写成 list_items（理由"留到下一轮"），但没人读（报告 F12）。"""

    result = assembly.assemble(
        packet(questions=[question_draft(title=f"第 {index} 题") for index in range(1, 8)]),
        round_=round_with_two_seats_attested(),
        document=make_document(),
        ledger=ledger_with_two_entries(),
    )

    assert [item.title for item in result.questions] == [f"第 {i} 题" for i in range(1, 6)]
    assert [item.title for item in result.deferred] == ["第 6 题", "第 7 题"]
    assert result.list_items == ()  # 不是"清单项"，是"没问上的题"


def test_carried_questions_are_asked_before_the_new_ones():
    result = assembly.assemble(
        packet(questions=[question_draft(title="新题 A"), question_draft(title="新题 B")]),
        round_=round_with_two_seats_attested(),
        document=make_document(),
        ledger=ledger_with_two_entries(),
        carried=(carried_question("上一批没问上的题"),),
    )

    assert [item.title for item in result.questions] == [
        "上一批没问上的题",
        "新题 A",
        "新题 B",
    ]
    assert [str(item.question_id) for item in result.questions] == ["q1", "q2", "q3"]


def test_questions_backed_by_the_users_own_words_come_first():
    """F12：用户明确提出的需求，不能被模型自己发现的题挤掉这一批的 5 题配额。"""

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(title="模型自己发现的：要不要支持导出？", evidence_refs=["E2"]),
                question_draft(title="用户说过的：量级是多少？", evidence_refs=["E1"]),
            ]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),  # E1 = 用户输入，E2 = 席位判断
    )

    assert [item.title for item in result.questions] == [
        "用户说过的：量级是多少？",
        "模型自己发现的：要不要支持导出？",
    ]


def test_the_user_backed_order_does_not_break_carried_first():
    """carried（上一批没问上的）仍然排最前——P9 定的口径不许被 T1 挤掉。"""

    carried = carried_question("上一批没问上的题")
    result = assembly.assemble(
        packet(
            questions=[question_draft(title="用户说过的：量级是多少？", evidence_refs=["E1"])]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
        carried=(carried,),
    )

    assert [item.title for item in result.questions][0] == carried.title


def test_the_new_feature_decision_question_still_comes_before_the_users_named_ones():
    """修订 R1：结构门（要不要新建子树）先问，别被 T1 的排序挤到后面去。"""

    result = assembly.assemble(
        packet(
            proposals=[proposal_draft()],  # 未授权的 order-export → 装配层出一道决策题
            questions=[question_draft(title="用户说过的：量级是多少？", evidence_refs=["E1"])],
        ),
        round_=make_round(),  # 这一轮没有授权 order-export
        document=make_document(),  # 空文档
        ledger=ledger_with_two_entries(),
    )

    assert [item.kind for item in result.questions] == [
        QuestionKind.DECISION,
        QuestionKind.GAP,
    ]
    assert result.questions[1].title == "用户说过的：量级是多少？"


def test_a_carried_question_is_not_asked_twice_by_the_model():
    result = assembly.assemble(
        packet(questions=[question_draft(title="上一批没问上的题")]),
        round_=round_with_two_seats_attested(),
        document=make_document(),
        ledger=ledger_with_two_entries(),
        carried=(carried_question("上一批没问上的题"),),
    )

    assert [item.title for item in result.questions] == ["上一批没问上的题"]
    assert result.deferred == ()  # 模型重复问的那条被丢掉，不是又排进队列


def test_a_fresh_question_gives_way_to_a_carried_question_on_the_same_target():
    """F15 残留（P11 真跑复现两次）：同去处、措辞近义的两道题，只留 carried 那道。

    `…保存三年后的处置策略？`（carried，`s8.cost`）与 `…在三年后如何处置？`（新出，`s8.cost`）——
    归一问法撞不上，所以这条收口只针对"同一去处 + carried 是开放题"这一种**限定**情形。
    """

    carried = replace(
        carried_question("点检照片保存三年后的处置策略？"),
        target=Target.body("s8", field="cost"),
        from_entry="r1#open01",
    )
    result = assembly.assemble(
        packet(
            questions=[
                question_draft(
                    title="点检照片在三年后如何处置？",
                    target={"zone": "body", "section_id": "s8", "field": "cost"},
                )
            ]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
        carried=(carried,),
    )

    assert [item.title for item in result.questions] == [carried.title]
    assert any("先别重复问" in item.reason for item in result.dropped)
    # 留痕要带**被丢题目的标题**，用户才查得到"哪句话没被问"
    assert any("点检照片在三年后如何处置？" in item.reason for item in result.dropped)


def test_two_different_things_on_the_same_place_are_still_both_asked():
    """P6 定过的口径不许破：**普通**新题之间同去处照问（收口只对 carried 生效）。"""

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(title="量级是多少？"),
                question_draft(title="对端不可用时怎么降级？"),
            ]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert len(result.questions) == 2


def test_a_section_level_carried_question_does_not_silence_the_whole_section():
    """收口只认**指到具体字段**的去处：章节级未决题（`s8`）不许把整章的新题压掉。

    这条是既有用例 `test_carried_questions_are_asked_before_the_new_ones` 当场抓出来的过度收口：
    carried 只是"落在 s8 的一个问题"，不代表 s8 里别的字段也不能问了。
    """

    carried = replace(carried_question("上一批没问上的题"), from_entry="r1#open01")
    result = assembly.assemble(
        packet(questions=[question_draft(title="新题 A")]),  # 默认去处 s8（章节级）
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
        carried=(carried,),
    )

    assert [item.title for item in result.questions] == [carried.title, "新题 A"]
    assert result.dropped == ()


# —— 跨轮队列 = 文档 s11 的未决条目（P9-B）——


def document_with_open_items(items: list[str]) -> Document:
    """造一批 s11 未决条目。`items` 是 `"entry_id|文本"`，文本用我们自己的标准格式。"""

    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id=f"i{index}",
                entry_id=EntryId(entry_id),
                op=WriteOp.ADD,
                target=Target.body("s11"),
                text=text,
                refs=(EvidenceId("E1"),),
                kind=BlockKind.QUESTION,
            )
            for index, (entry_id, text) in enumerate(
                (item.split("|", 1) for item in items), start=1
            )
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset(),
    )
    return document


def test_parse_open_item_reads_our_own_format():
    text = "〔未决·阻塞〕点检完成率的统计口径（未决定，待决人：PM；跳过后果：报表口径不清）"

    parsed = assembly.parse_open_item(text)

    assert parsed is not None
    assert parsed.title == "点检完成率的统计口径"
    assert parsed.consequence == "报表口径不清"
    assert parsed.blocking is True
    assert parsed.target_label is None  # 老格式：没有去处标记


def test_open_item_text_carries_its_target():
    """s11 条目要记住它本来落在哪一章（P11 C1）：不然问回来只能回到 s11，答案也就没处落。"""

    proposal = assembly.open_question_proposal(
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s5", feature_slug="order-export", field="acceptance"),
            type=QuestionType.TEXT,
            title="单次导出的最大行数是多少？",
            why="量级决定同步还是异步",
            consequence_if_skipped="按同步实现",
        ),
        eid=EvidenceId("E9"),
        index=1,
        round_id=ROUND_ID,
        rationale="跳过的题按题面后果落开放问题",
    )

    assert proposal.text.endswith("〔去处：s5/order-export.acceptance〕")
    parsed = assembly.parse_open_item(proposal.text)
    assert parsed is not None
    assert parsed.target_label == "s5/order-export.acceptance"
    assert parsed.title == "单次导出的最大行数是多少？"
    assert parsed.consequence == "按同步实现"
    assert parsed.blocking is False


def test_an_open_item_with_a_chapter_slot_keeps_it():
    """章节槽（`s8.compatibility`）也要认——真跑里技术/性能类的未决项常落在这些槽上。"""

    proposal = assembly.open_question_proposal(
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s8", field="compatibility"),
            type=QuestionType.TEXT,
            title="导出要不要兼容老版 Excel？",
            why="兼容性影响验收口径",
            consequence_if_skipped="先不兼容",
        ),
        eid=EvidenceId("E9"),
        index=1,
        round_id=ROUND_ID,
        rationale="跳过的题按题面后果落开放问题",
    )

    parsed = assembly.parse_open_item(proposal.text)

    assert parsed is not None
    assert parsed.target_label == "s8.compatibility"


def test_parse_open_item_rejects_a_foreign_line():
    assert assembly.parse_open_item("这条是用户手写的普通条目") is None


def test_oldest_open_items_become_carried_questions_with_their_entry_id():
    document = document_with_open_items(
        [
            "r1#open01|〔未决·阻塞〕A（未决定，待决人：PM；跳过后果：后果A）",
            "r2#open01|B（未决定，待决人：PM；跳过后果：后果B）",
        ]
    )

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=1
    )

    assert len(carried) == 1  # 只取最老的一条
    assert carried[0].title == "A"
    assert carried[0].from_entry == "r1#open01"  # 记住来源：答完要撤下它
    assert carried[0].type is QuestionType.TEXT  # 结构已丢 → 按开放题重出
    assert carried[0].blocking is True  # 阻塞标记跟着走


def test_a_blocking_open_item_jumps_ahead_of_older_hints():
    """A3-①：只有阻塞项决定 ready 不 ready（`readiness._open_questions`）——配额先给它。

    今天这个函数是**纯 FIFO**：两条非阻塞的老条目会把堵交付的那条挤出去，
    而就绪度只看阻塞项——于是"永远 ready 不了"。
    """

    document = document_with_open_items(
        [
            "r0#open01|老的提示项（未决定，待决人：PM；跳过后果：无）〔去处：s8.cost〕",
            "r0#open02|更老的提示项（未决定，待决人：PM；跳过后果：无）〔去处：s8.performance〕",
            "r0#open03|〔未决·阻塞〕堵交付的那条（未决定，待决人：PM；跳过后果：交付不了）"
            "〔去处：s5/order-export.acceptance〕",
        ]
    )

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=1
    )

    assert [item.title for item in carried] == ["堵交付的那条"]
    assert carried[0].blocking is True
    assert carried[0].from_entry == "r0#open03"


def test_two_blockers_keep_the_oldest_first():
    """同龄之间仍然最老优先——不许把 FIFO 整个换成"阻塞优先"就算完。"""

    document = document_with_open_items(
        [
            "r0#open01|〔未决·阻塞〕先提的（未决定，待决人：PM；跳过后果：一）〔去处：s8.cost〕",
            "r0#open02|〔未决·阻塞〕后提的（未决定，待决人：PM；跳过后果：二）"
            "〔去处：s8.performance〕",
        ]
    )

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=1
    )

    assert [item.title for item in carried] == ["先提的"]


def test_open_item_keys_reads_the_active_ones_only():
    """s11 里**活跃**条目的身份 = `(去处 label, 归一化标题)`；解析不了的不猜。"""

    document = document_with_open_items(
        [
            "r0#open01|口径是什么？（未决定，待决人：PM；跳过后果：无）〔去处：s8.cost〕",
            "r0#open02|一行脏数据，不是我们写的",
        ]
    )

    assert assembly.open_item_keys(document) == {("s8.cost", "口径是什么？")}
    assert assembly.open_item_keys(make_document()) == frozenset()


def _question(title: str, *, target: Target | None = None) -> Question:
    return Question(
        question_id=QuestionId("q1"),
        target=target or Target.body("s6"),
        type=QuestionType.TEXT,
        title=title,
        why="主流程要看下一轮的话",
        consequence_if_skipped="先搁着，不影响本轮交付",
    )


def test_process_notes_are_stripped_before_landing_in_s11():
    """s11 条目只写"问题本身"——模型交代的台账话不进库（M7 A′、真跑 L5 原文）。"""

    proposal = assembly.open_question_proposal(
        _question("菜单能否在锁单后调整？本轮已出题（见题『菜单能否在锁单后调整？』）。"),
        eid=EvidenceId("E9"),
        index=1,
        round_id=ROUND_ID,
        rationale="跳过的题按题面后果落开放问题",
    )

    assert "本轮已出题" not in proposal.text
    assert "见题" not in proposal.text
    parsed = assembly.parse_open_item(proposal.text)
    # 问号是内容，照旧留着（与 `test_open_item_keys_reads_the_active_ones_only` 同一口径）
    assert parsed is not None and parsed.title == "菜单能否在锁单后调整？"


def test_entry_addresses_never_reach_the_document():
    """内部条目地址（`open61/open67`、`[r-x#open61]`）是"撤哪一条"用的地址，不许进正文（L13）。"""

    proposal = assembly.open_question_proposal(
        _question("菜单能否在锁单后调整（open61/open67/open112）：本轮仍未定。"),
        eid=EvidenceId("E9"),
        index=1,
        round_id=ROUND_ID,
        rationale="跳过的题按题面后果落开放问题",
    )

    assert "open61" not in proposal.text
    assert "open67" not in proposal.text
    parsed = assembly.parse_open_item(proposal.text)
    assert parsed is not None and parsed.title == "菜单能否在锁单后调整"


def test_seat_inclination_sentence_is_not_stripped():
    """**反例**：席位倾向是给用户的决策依据，不许被当台账话剥掉。"""

    title = (
        "菜单能否在锁单后调整（open61）？"
        "席位判断 E157 给出默认「要留痕」的倾向，但用户尚未拍板。"
    )
    proposal = assembly.open_question_proposal(
        _question(title),
        eid=EvidenceId("E9"),
        index=1,
        round_id=ROUND_ID,
        rationale="跳过的题按题面后果落开放问题",
    )

    assert "席位判断 E157 给出默认「要留痕」的倾向，但用户尚未拍板。" in proposal.text
    assert "open61" not in proposal.text


def test_ledger_notes_are_only_stripped_at_the_tail_and_only_in_those_shapes():
    """**反例（真跑原文回放抓到的两处剥过头）**：正当句子里的"留待下一轮""本轮仍未…"不许动。

    两条都是 `.codex/runs/2026-10-03/M4验证/导出-两轮后-L5.md` 的原句：
    - `非功能需求其余槽位：…；成本仍无口径，留待下一轮。`（"留待下一轮"是**真内容**）
    - `s8.cost 仍无口径：本轮仍未拿到成本表态（open71 留待下一轮），需在后续轮次补齐。`
      （第一版正则把后半句剪掉，留下一个悬空的 `（open71` —— 比不剥糟得多）
    """

    keep = [
        "非功能需求其余槽位：兼容与可观测本轮已由席位判断落成内容（CSV 导出规范 E49 / "
        "留痕粒度 E47、E54）；安全落成 E46 最小表态；成本仍无口径，留待下一轮。",
        "s8.cost 仍无口径：本轮仍未拿到成本表态（open71 留待下一轮），需在后续轮次补齐。",
    ]

    for text in keep:
        assert assembly.strip_process_notes(text) == text


def test_dedup_key_ignores_process_notes():
    """老条目里带着台账话，也要能被"干净的新题"认出是同一件事（判重身份走同一套剥离）。"""

    document = document_with_open_items(
        [
            "r0#open01|菜单能否在锁单后调整：本轮已出题（见题『菜单能否在锁单后调整？』）。"
            "（未决定，待决人：PM；跳过后果：无）〔去处：s6〕",
        ]
    )

    assert assembly.open_item_keys(document) == {("s6", "菜单能否在锁单后调整")}
    assert (
        assembly.unlanded_open_questions(
            (_question("菜单能否在锁单后调整"),), known=assembly.open_item_keys(document)
        )
        == ()
    )


def test_old_open_items_still_parse_and_compare():
    """**护栏**：老格式（没有 `〔去处：…〕`）照旧认，去处回落 s11；台账话照样剥。"""

    document = document_with_open_items(
        [
            "r0#open01|口径是什么？本轮仍未定，未出题（题位已满），留待下一轮。"
            "（未决定，待决人：PM；跳过后果：无）",
        ]
    )

    assert assembly.open_item_keys(document) == {("s11", "口径是什么？")}
    parsed = assembly.parse_open_item(
        "口径是什么？本轮仍未定，未出题（题位已满），留待下一轮。（未决定，待决人：PM；跳过后果：无）"
    )
    assert parsed is not None and parsed.target_label is None


def test_questions_already_in_s11_are_dropped_before_landing():
    """同一句话已经在 s11 挂着 → 不再落一遍（P10 真跑 removed 4 / added 18 里就有重复）。"""

    question = Question(
        question_id=QuestionId("q1"),
        target=Target.body("s8"),
        type=QuestionType.TEXT,
        title="单次导出的最大行数是多少？",
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现",
    )

    assert (
        assembly.unlanded_open_questions((question,), known={("s8", "单次导出的最大行数是多少？")})
        == ()
    )
    assert assembly.unlanded_open_questions((question,), known=frozenset()) == (question,)


def test_a_carried_question_goes_back_to_its_own_section():
    """带上去处的未决条问回来时回到**原章节**（P11 C1）。

    不回到原章节，答案就只能落回 s11 或干脆不落——真跑 `p-0b212145` 里四条答案就是这么蒸发的。
    """

    document = document_with_open_items(
        [
            "r1#open01|〔未决·阻塞〕点检完成率的统计口径"
            "（未决定，待决人：PM；跳过后果：报表口径不清）〔去处：s8.compatibility〕",
        ]
    )

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=5
    )

    assert len(carried) == 1
    assert carried[0].target == Target.body("s8", field="compatibility")
    assert carried[0].from_entry == "r1#open01"
    assert carried[0].title == "点检完成率的统计口径"
    assert carried[0].blocking is True


def test_a_carried_question_without_a_target_falls_back_to_s11():
    """老格式条目（没有去处标记）：行为与 P10 完全一致（回落 s11），新老两套行为不许打架。"""

    document = document_with_open_items(
        ["r1#open01|〔未决·阻塞〕点检完成率的统计口径（未决定，待决人：PM；跳过后果：报表口径不清）"]
    )

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=5
    )

    assert carried[0].target == Target.body("s11")


def test_a_carried_question_with_a_broken_target_falls_back_to_s11():
    """去处写歪了（章节不存在 / 形状不完整）：**回落 s11**，绝不把内容塞进别人家章节。"""

    document = document_with_open_items(
        [
            "r1#open01|A（未决定，待决人：PM；跳过后果：后果A）〔去处：s99〕",
            "r2#open02|B（未决定，待决人：PM；跳过后果：后果B）〔去处：s5/〕",
        ]
    )

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=5
    )

    assert [item.target for item in carried] == [Target.body("s11"), Target.body("s11")]


def test_unparsable_open_items_are_skipped_not_crashed():
    document = document_with_open_items(["r1#open01|用户手写的一行"])

    carried = assembly.deferred_from_open_items(
        document, ledger=ledger_with_two_entries(), limit=5
    )

    assert carried == ()


# —— 矛盾的机械通道：analyzer 报对子，装配层盖章（T8）——


def test_contradiction_pairs_map_claim_ids_to_eids():
    """映射只有这一处：`c1/c2` → 账本里的 `E1/E2`（`assemble` 盖章要用编号）。"""

    pairs = assembly.contradiction_pairs(make_round_with_contradictions())

    assert pairs == frozenset({frozenset({EvidenceId("E1"), EvidenceId("E2")})})


def test_a_question_covering_both_sides_is_stamped_as_conflict():
    """判断做一次（analyzer 读原话），标签由系统盖章——不再靠模型记得标。"""

    result = assembly.assemble(
        packet(questions=[question_draft(evidence_refs=["E1", "E2"])]),  # 模型自己写的是 gap
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
        contradictions=frozenset({frozenset({EvidenceId("E1"), EvidenceId("E2")})}),
    )

    assert result.questions[0].kind is QuestionKind.CONFLICT


def test_unaccounted_lookup_is_scoped_to_the_round():
    """真跑抓到（P22 回归）：两轮的 `p1:O1` 撞名，按 (席位, 条目) 反查会命中**上一轮**那条。

    后果有两个：① 它自己的编号被引用了，却仍被报成"没人交代"；
    ② 整合者看到的是上一轮的编号。所以反查必须带轮次。
    """

    ledger = EvidenceLedger.empty(PROJECT)
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r-one"), raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    old = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="上一轮：导出要分页",
        source=EvidenceSource(
            seat_id=SEAT,
            local_id="p1:O1",
            round_id=RoundId("r-one"),
            derived_from=(user,),
            rationale="同类做法",
        ),
        now=NOW,
    )
    own = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="这一轮：导出要脱敏",
        source=EvidenceSource(
            seat_id=SEAT,
            local_id="p1:O1",
            round_id=RoundId("r-two"),
            derived_from=(user,),
            rationale="合规要求",
        ),
        now=NOW,
    )
    review = SeatReview(
        seat_id=SEAT,
        basis="读了 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.JUDGMENT,
                about="导出的脱敏口径",
                claim="这一轮：导出要脱敏",
                because="合规要求",
                evidence_refs=(user,),
            ),
        ),
    )

    def drops(accounted: frozenset[EvidenceId]) -> tuple:  # type: ignore[type-arg]
        return assembly.unaccounted_drops(
            (review,),
            ledger=ledger,
            round_id=RoundId("r-two"),
            accounted_handles=frozenset(),
            accounted_eids=accounted,
            declared=frozenset(),
        )

    assert drops(frozenset({own})) == ()  # 它自己的编号被引用 → 交代过
    assert [item.handle for item in drops(frozenset({old}))] == [f"{SEAT}:O1"]  # 上一轮的不算数


# —— 骨架六章还空着：草案卡上要说一句（报告 F18） ——


def backbone_blocker(section_id: str) -> ReadinessItem:
    return ReadinessItem(
        section_id=section_id,
        code="section_min_entries",
        title=f"{section_id} 还缺 1 条内容",
        detail="至少 1 条：现状 / 问题 / 不做的代价",
        severity=Severity.BLOCKER,
    )


def backbone_proposal(section_id: str) -> Proposal:
    return Proposal(
        proposal_id=ProposalId("p01"),
        entry_id=EntryId(f"{ROUND_ID}#01"),
        op=WriteOp.ADD,
        target=Target.body(section_id),
        text="现状：车间还在用纸单子",
        origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
        kind=BlockKind.TEXT,
    )


def test_backbone_gaps_name_the_empty_chapters():
    lines = assembly.backbone_gap_lines(
        readiness=(backbone_blocker("s1"), backbone_blocker("s2"), backbone_blocker("s8")),
        proposals=(),
    )

    assert len(lines) == 1
    assert "s1" in lines[0] and "s2" in lines[0]
    assert "s8" not in lines[0]  # s8 是六个槽，不在这条告警的范围内
    assert "背景与问题" in lines[0] and "目标与非目标" in lines[0]
    assert lines[0].startswith("⚠")


def test_backbone_gaps_skip_the_chapter_this_round_writes_to():
    lines = assembly.backbone_gap_lines(
        readiness=(backbone_blocker("s1"), backbone_blocker("s2")),
        proposals=(backbone_proposal("s1"),),
    )

    assert len(lines) == 1
    assert "s2" in lines[0]
    assert "s1" not in lines[0]


def test_backbone_gaps_are_silent_when_nothing_is_blocking():
    """骨架填上了（就绪度不再报阻塞）→ 一个字都不说，别每轮误报一次。"""

    warn_only = replace(backbone_blocker("s1"), severity=Severity.WARN)
    assert assembly.backbone_gap_lines(readiness=(warn_only,), proposals=()) == ()
    assert assembly.backbone_gap_lines(readiness=(), proposals=()) == ()


def test_backbone_gaps_ignore_an_entry_of_the_wrong_kind():
    """s12 要的是**验收标准**（见 `SECTION_MIN_ENTRIES`）：写一条普通正文不算填上。

    真跑单轮抽样抓到的：这一轮往 s12 写了 1 条 `text`，文档里 s12 仍然报
    "还缺 1 条验收标准"，而我那句告警因为"写过 s12"就闭嘴了——等于漏报。
    """

    text_entry = replace(backbone_proposal("s12"), kind=BlockKind.TEXT)
    lines = assembly.backbone_gap_lines(
        readiness=(backbone_blocker("s12"),), proposals=(text_entry,)
    )

    assert len(lines) == 1
    assert "s12" in lines[0]

    acceptance = replace(backbone_proposal("s12"), kind=BlockKind.ACCEPTANCE)
    assert (
        assembly.backbone_gap_lines(readiness=(backbone_blocker("s12"),), proposals=(acceptance,))
        == ()
    )


# —— 阻塞题先拿配额（报告 F20） ——


def test_blocking_questions_get_the_quota_before_optional_ones():
    """真跑实测（`.codex/runs/2026-10-01/报告.md` F20）：一轮产出的阻塞题没被问出去，
    直接落成 s11 的〔未决·阻塞〕，于是 ready "永远差一轮"。

    配额只有 5 个名额（`MAX_QUESTIONS_PER_BATCH`），先给挡交付的那几道。
    `s7` 不在 `BLOCKING_SECTIONS` 里，所以是普通题；`s1/s3/s4` 在，所以是阻塞题。
    """

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(title="普通题一？", target={"zone": "body", "section_id": "s7"}),
                question_draft(title="普通题二？", target={"zone": "body", "section_id": "s7"}),
                question_draft(title="普通题三？", target={"zone": "body", "section_id": "s7"}),
                question_draft(title="阻塞题一？", target={"zone": "body", "section_id": "s1"}),
                question_draft(title="阻塞题二？", target={"zone": "body", "section_id": "s3"}),
                question_draft(title="阻塞题三？", target={"zone": "body", "section_id": "s4"}),
            ]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    asked = [item.title for item in result.questions]

    assert len(asked) == 5
    assert {"阻塞题一？", "阻塞题二？", "阻塞题三？"} <= set(asked)
    assert sum(1 for item in result.questions if item.blocking) == 3


def test_blocking_first_does_not_reorder_within_the_same_priority():
    """同级保持模型给的顺序（`sorted` 稳定）——别把"先阻塞"做成"乱序"。"""

    result = assembly.assemble(
        packet(
            questions=[
                question_draft(title="阻塞一？", target={"zone": "body", "section_id": "s1"}),
                question_draft(title="阻塞二？", target={"zone": "body", "section_id": "s2"}),
            ]
        ),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [item.title for item in result.questions] == ["阻塞一？", "阻塞二？"]


def test_a_question_with_only_one_side_stays_as_the_model_labelled_it():
    """只引了一侧 ⇒ 不盖章（别把普通题都标成矛盾）。"""

    result = assembly.assemble(
        packet(questions=[question_draft(evidence_refs=["E1"])]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
        contradictions=frozenset({frozenset({EvidenceId("E1"), EvidenceId("E2")})}),
    )

    assert result.questions[0].kind is QuestionKind.GAP


def test_a_model_labelled_conflict_without_a_pair_is_left_alone():
    """反方向不管：模型自己标了 conflict 但没对子 ⇒ 原样保留（它可能看到的是别种冲突）。"""

    result = assembly.assemble(
        packet(questions=[question_draft(evidence_refs=["E1"], kind="conflict")]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
        contradictions=frozenset(),
    )

    assert result.questions[0].kind is QuestionKind.CONFLICT


def ledger_with_material_backed_claim() -> EvidenceLedger:
    """E1 材料块 + E2 主张（读过 E1）：E46 那一幕落编号之后的样子。"""

    ledger = EvidenceLedger.empty(PROJECT)
    material = ledger.record(
        kind=EvidenceKind.SIMULATED,
        text="讨论追问",
        source=EvidenceSource(external_ref="discussion:d-1#C1"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="按材料写",
        source=EvidenceSource(
            round_id=ROUND_ID, raw_span="按材料写", derived_from=(material,)
        ),
        now=NOW,
    )
    return ledger


def test_proposal_citing_material_backed_claim_stays_derived():
    """主张挂了材料链，引用它的提案分组必须原样不动（副作用为零的证据）。"""

    result = assembly.assemble(
        packet(proposals=[slot_proposal(refs=["E2"])]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_material_backed_claim(),
    )

    assert [proposal.origin.type for proposal in result.proposals] == [
        OriginType.DERIVED
    ]


def ledger_with_a_simulated_chunk() -> EvidenceLedger:
    """E1 用户输入 + E2 模拟材料块（M10 收下回流后的样子）：只引 E2 的提案没有终点可回溯。"""

    from tests.support.builders import NOW, PROJECT, ROUND_ID

    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="想做个订单导出的功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.SIMULATED,
        text="待验证假设（模拟推演）H1：运营宁愿等一分钟也不愿分两次导",
        source=EvidenceSource(external_ref="simulation:inquiry:r1#H1"),
        now=NOW,
    )
    return ledger


def test_simulated_only_proposal_is_downgraded_to_proposed():
    """只引模拟块的提案：回溯不到任何终点 → 降级成"它提议的"（默认不勾）。

    M10 的整条回流就靠这一降：如果不断言，SIMULATED 材料的引用会悄悄变成"已验证"。
    """

    result = assembly.assemble(
        packet(proposals=[slot_proposal(refs=["E2"])]),
        round_=round_with_two_seats_attested(),
        document=document_with_feature(),
        ledger=ledger_with_a_simulated_chunk(),
    )

    assert [proposal.origin.type for proposal in result.proposals] == [
        OriginType.PROPOSED
    ]


# ── 席位间冲突检测 ────────────────────────────────────────────────


def _review(seat_id: str, opinions: list[Opinion]) -> SeatReview:
    return SeatReview(seat_id=seat_id, basis="依据", opinions=tuple(opinions))


def _judgment(local_id: str, about: str, claim: str) -> Opinion:
    return Opinion(
        local_id=local_id,
        form=OpinionForm.JUDGMENT,
        about=about,
        claim=claim,
        because="理由",
        evidence_refs=(EvidenceId("E1"),),
    )


def test_seat_conflict_pairs_detects_opposite_judgments():
    """两个席位对同一 about 持相反判断 → 检测为冲突。"""
    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O1", "导出性能", "导出不应在 60 秒内完成")]),
    )
    conflicts = assembly.seat_conflict_pairs(reviews)
    assert len(conflicts) == 1
    assert conflicts[0].about == "导出性能"
    assert conflicts[0].seat_a in ("seat.a", "seat.b")
    assert conflicts[0].seat_b in ("seat.a", "seat.b")
    assert conflicts[0].seat_a != conflicts[0].seat_b


def test_seat_conflict_pairs_same_stance_not_conflict():
    """两个席位对同一 about 持相同判断 → 不是冲突。"""
    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O1", "导出性能", "导出必须在 60 秒内完成")]),
    )
    conflicts = assembly.seat_conflict_pairs(reviews)
    assert len(conflicts) == 0


def test_seat_conflict_pairs_different_about_not_conflict():
    """不同 about → 不是冲突。"""
    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O1", "导出格式", "导出不应在 60 秒内完成")]),
    )
    conflicts = assembly.seat_conflict_pairs(reviews)
    assert len(conflicts) == 0


def test_seat_conflict_pairs_empty_reviews():
    """没有席位 → 空冲突。"""
    conflicts = assembly.seat_conflict_pairs(())
    assert conflicts == ()


def test_seat_conflict_pairs_same_seat_not_conflict():
    """同一席位的多个判断 → 不与自己冲突。"""
    reviews = (
        _review("seat.a", [
            _judgment("O1", "导出性能", "导出应在 60 秒内完成"),
            _judgment("O2", "导出性能", "导出不应在 60 秒内完成"),
        ]),
    )
    conflicts = assembly.seat_conflict_pairs(reviews)
    assert len(conflicts) == 0


def test_claims_contradict_positive_vs_negative():
    """肯定 vs 否定 → 矛盾。"""
    assert assembly._claims_contradict("应支持导出", "不应支持导出") is True


def test_claims_contradict_same_direction():
    """同方向 → 不矛盾。"""
    assert assembly._claims_contradict("应支持导出", "必须支持导出") is False


def test_claims_contradict_neutral():
    """中性文本 → 不矛盾。"""
    assert assembly._claims_contradict("导出功能", "导出功能") is False


def _ledger_with_two_seat_judgments() -> EvidenceLedger:
    """两条席位判断进账本（seat.a / seat.b 各一条），形状与真 assigner 一致。"""

    ledger = EvidenceLedger.empty(PROJECT)
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="导出应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.a",
            local_id="p1:O1",
            round_id=ROUND_ID,
            derived_from=(user,),
            rationale="体验优先",
        ),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="导出不应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.b",
            local_id="p1:O1",
            round_id=ROUND_ID,
            derived_from=(user,),
            rationale="数据量大",
        ),
        now=NOW,
    )
    return ledger


def test_seat_conflict_pairs_carry_the_ledger_handles():
    """冲突对子要带每条判断的 `p{趟}:{条目}` 句柄——盖章时靠它反查 EID。"""

    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O2", "导出性能", "导出不应在 60 秒内完成")]),
    )

    conflict = assembly.seat_conflict_pairs(reviews)[0]

    assert {conflict.handle_a, conflict.handle_b} == {"p1:O1", "p1:O2"}


def test_seat_conflict_eid_pairs_maps_handles_to_ledger_eids():
    """句柄 → 账本里的 E 编号（`assemble` 盖章要的就是这一对）。"""

    ledger = _ledger_with_two_seat_judgments()
    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O1", "导出性能", "导出不应在 60 秒内完成")]),
    )
    conflicts = assembly.seat_conflict_pairs(reviews)

    pairs = assembly.seat_conflict_eid_pairs(conflicts, ledger=ledger, round_id=ROUND_ID)

    left = ledger.find_by_local(seat_id="seat.a", local_id="p1:O1", round_id=ROUND_ID)
    right = ledger.find_by_local(seat_id="seat.b", local_id="p1:O1", round_id=ROUND_ID)
    assert pairs == frozenset({frozenset({left.eid, right.eid})})


def test_seat_conflict_eid_pairs_skips_unresolved_judgments():
    """账本里查不到（还没落编号）⇒ 跳过，不判死。"""

    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O1", "导出性能", "导出不应在 60 秒内完成")]),
    )
    conflicts = assembly.seat_conflict_pairs(reviews)

    pairs = assembly.seat_conflict_eid_pairs(
        conflicts, ledger=EvidenceLedger.empty(PROJECT), round_id=ROUND_ID
    )

    assert pairs == frozenset()


def test_a_question_covering_both_seat_judgments_is_stamped_as_conflict():
    """席位冲突走同一台盖章机器：题目覆盖两条判断 ⇒ 系统盖章 conflict。"""

    ledger = _ledger_with_two_seat_judgments()
    reviews = (
        _review("seat.a", [_judgment("O1", "导出性能", "导出应在 60 秒内完成")]),
        _review("seat.b", [_judgment("O1", "导出性能", "导出不应在 60 秒内完成")]),
    )
    pairs = assembly.seat_conflict_eid_pairs(
        assembly.seat_conflict_pairs(reviews), ledger=ledger, round_id=ROUND_ID
    )
    (pair,) = pairs

    result = assembly.assemble(
        packet(questions=[question_draft(evidence_refs=[str(e) for e in pair])]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger,
        contradictions=pairs,
    )

    assert result.questions[0].kind is QuestionKind.CONFLICT


def test_a_multi_select_question_keeps_its_type():
    """真跑撞到（2026-10-09）：模型按 schema 出 `type="multi"`，领域枚举却没有 MULTI →
    `QuestionType("multi")` 抛 ValueError（不是 DomainError，`except` 兜不住）→ 整轮崩。
    补回 MULTI（439327e 误删；schema 与前端契约一直留着它）。"""

    result = assembly.assemble(
        packet(questions=[question_draft(type="multi")]),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert result.questions[0].type is QuestionType.MULTI


def test_cited_eids_unions_proposals_and_questions():
    """引用口径的唯一实现：提案引用 ∪ 题目引用。

    （`_accounted` 与 `_unreferenced_materials_for` 原来各抄了一遍，现在共用一个。）"""

    proposals = [
        Proposal(
            proposal_id="p1",
            entry_id="en1",
            op=WriteOp.ADD,
            target=Target.body("s5"),
            text="测试提案",
            origin=Origin(type=OriginType.USER, refs=(EvidenceId("E1"),)),
        )
    ]
    questions = [
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s5"),
            type=QuestionType.SINGLE,
            title="测试题",
            why="为什么问",
            consequence_if_skipped="跳过的后果",
            options=(Option("a", "A"), Option("b", "B")),
            evidence_refs=(EvidenceId("E2"),),
        )
    ]

    assert assembly.cited_eids(proposals, questions) == frozenset(
        {EvidenceId("E1"), EvidenceId("E2")}
    )

