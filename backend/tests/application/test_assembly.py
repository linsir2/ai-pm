import pytest

from pmstudio.application import assembly
from pmstudio.application.agents import schemas
from pmstudio.application.errors import PacketParseError
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import OriginType
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
from pmstudio.domain.shared.ids import EvidenceId, FormId, ProjectId, QuestionId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import (
    NOW,
    ROUND_ID,
    SEAT,
    document_with_feature,
    make_document,
    make_round,
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


def test_assemble_keeps_first_five_questions_and_degrades_the_rest():
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
    assert len(result.list_items) == 2
    assert result.list_items[0].reason.startswith("本批只问 5 题")


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
