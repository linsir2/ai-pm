from dataclasses import replace

import pytest

from pmstudio.application import assembly
from pmstudio.application.agents import schemas
from pmstudio.application.errors import PacketParseError
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
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import (
    NOW,
    ROUND_ID,
    SEAT,
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


def test_the_same_place_asked_differently_is_not_a_duplicate():
    """挪一个问法就不是同一道题：同一去处上的另一件事照问（别把去重做过头）。"""

    result = assembly.assemble(
        packet(questions=[question_draft(title="单次导出最多能有多少行？")]),
        round_=round_that_already_asked("单次导出的最大行数是多少？"),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )

    assert [str(item.question_id) for item in result.questions] == ["q2"]


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
        target=Target.feature_field("order-export", "acceptance"),
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


def test_assemble_replace_requires_existing_entry_id():
    bad = proposal_draft()
    bad["op"] = "replace"
    bad["entry_id"] = "不存在的条目"
    with pytest.raises(PacketParseError):
        assembly.assemble(
            packet(proposals=[bad]),
            round_=make_round(),
            document=document_with_feature(),
            ledger=ledger_with_two_entries(),
        )


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
