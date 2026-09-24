from pmstudio.application import dto
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DocumentId,
    DraftId,
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
    make_claim,
    make_round,
    make_seat,
)


def make_draft(*, out_of_scope: bool = False) -> Draft:
    proposals = [
        Proposal(
            proposal_id=ProposalId("p01"),
            entry_id=EntryId("r1#01"),
            op=WriteOp.ADD,
            target=Target.body("s5"),
            text="登录页提供微信扫码入口",
            origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
            kind=BlockKind.TEXT,
        ),
        Proposal(
            proposal_id=ProposalId("p02"),
            entry_id=EntryId("r1#02"),
            op=WriteOp.ADD,
            target=Target.body("s8") if out_of_scope else Target.body("s5"),
            text="单次导出 60 秒内完成",
            origin=Origin(OriginType.DERIVED, (EvidenceId("E9"),), "同类导出的常见上限"),
            kind=BlockKind.NFR,
        ),
        Proposal(
            proposal_id=ProposalId("p03"),
            entry_id=EntryId("r1#03"),
            op=WriteOp.ADD,
            target=Target.body("s5"),
            text="导出记录保留 30 天",
            origin=Origin(OriginType.PROPOSED, (EvidenceId("E10"),)),
            kind=BlockKind.TEXT,
        ),
    ]
    return Draft(draft_id=DraftId("d1"), round_id=ROUND_ID, proposals=tuple(proposals))


def test_round_view_reports_step_waiting_claims_and_seats():
    view = dto.round_view(make_round(), seat_titles={SEAT: "数据规模与性能"})
    assert view.step == "seats_running"
    assert view.waiting_on == "none"
    assert view.claims[0].eid == "E1"
    assert view.claims[0].label == "need"
    assert view.claims[0].target_label == "s5"
    assert view.seats == (SEAT,)


def test_round_view_suggests_seats_for_the_opening_card():
    """F8：开局要按输入提名席位，并写清为什么请它（理由用席位的 focus）。"""

    def make_triggered_seat():
        return SeatDefinition(
            seat_id="seat.export_import",
            title="导出与导入",
            focus="导出范围、格式、大文件",
            trigger_keywords=("导出", "导入"),
        )

    class _Catalog:
        def all(self):
            return (make_seat(),)

        def nominate(self, *, user_input: str):
            return (make_seat(),) if "导出" in user_input else ()

        def by_id(self, seat_id: str):
            return make_seat()

    round_ = Round.start(
        round_id=ROUND_ID,
        project_id=ProjectId("p1"),
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))

    view = dto.round_view(round_, catalog=_Catalog())

    assert view.nominated_seats[0].seat_id == SEAT
    assert view.nominated_seats[0].title == "数据规模与性能"
    assert view.nominated_seats[0].focus  # 提名理由


def test_round_view_tells_why_a_seat_was_nominated():
    """提名来源要能说明白：常驻席位 / 你这轮的话命中了它的关键词。

    这是**机械判定**（目录里的 always_on 与 trigger_keywords），不是模型判断——
    界面照实说，用户才好决定删谁。
    """

    def export_seat(*, always_on: bool) -> SeatDefinition:
        return SeatDefinition(
            seat_id="seat.export_import",
            title="导出与导入",
            focus="导出范围、格式、大文件",
            trigger_keywords=("导出", "导入"),
            always_on=always_on,
        )

    class _AlwaysOnCatalog:
        def all(self):
            return (export_seat(always_on=True),)

        def nominate(self, *, user_input: str):
            return (export_seat(always_on=True),)

        def by_id(self, seat_id: str):
            return export_seat(always_on=True)

    always_on = Round.start(
        round_id=ROUND_ID,
        project_id=ProjectId("p1"),
        user_input="把零散想法整理成一份能交付的文档",
        now=NOW,
    )
    on_view = dto.round_view(always_on, catalog=_AlwaysOnCatalog()).nominated_seats[0]
    assert on_view.always_on is True
    assert on_view.matched_keywords == ()

    triggered = Round.start(
        round_id=ROUND_ID,
        project_id=ProjectId("p1"),
        user_input="想做个订单导出",
        now=NOW,
    )
    class _TriggeredCatalog(_AlwaysOnCatalog):
        def nominate(self, *, user_input: str):
            return (export_seat(always_on=False),)

        def by_id(self, seat_id: str):
            return export_seat(always_on=False)

    triggered_view = dto.round_view(triggered, catalog=_TriggeredCatalog()).nominated_seats[0]
    assert triggered_view.always_on is False
    assert triggered_view.matched_keywords == ("导出",)


def test_round_view_lists_every_seat_with_status_pending_or_completed():
    round_ = make_round(seats=(SEAT, "seat.permissions"))
    round_.attest(
        SeatReview(
            seat_id=SEAT,
            basis="读了 s5 与 E1",
            opinions=(
                Opinion(
                    local_id="O1",
                    form=OpinionForm.QUESTION,
                    about="量级",
                    claim="量级必须问用户",
                    because="1 万是同步与异步的分界",
                    evidence_refs=(EvidenceId("E1"),),
                ),
                Opinion(
                    local_id="O2",
                    form=OpinionForm.JUDGMENT,
                    about="性能上限",
                    claim="60 秒内完成",
                    because="同类做法",
                    evidence_refs=(EvidenceId("E1"),),
                ),
            ),
        )
    )
    view = dto.round_view(round_, seat_titles={SEAT: "数据规模与性能"})

    by_id = {packet.seat_id: packet for packet in view.seat_packets}
    assert by_id[SEAT].status == "completed"
    assert by_id[SEAT].judgment_count == 1
    assert by_id[SEAT].question_count == 1
    assert by_id["seat.permissions"].status == "pending"
    assert by_id["seat.permissions"].title == "seat.permissions"  # 没有标题时退回 id


def test_round_view_groups_draft_proposals_in_three_groups():
    round_ = make_round()
    round_.integrate(draft=make_draft())
    view = dto.round_view(round_)

    assert view.draft is not None
    titles = [title for title, _ in view.draft.groups]
    assert titles == ["你说过的", "它替你判断的", "它提议的"]
    derived = dict(view.draft.groups)["它替你判断的"]
    assert derived[0].rationale == "同类导出的常见上限"
    assert derived[0].eids == ("E9",)


def test_round_view_marks_out_of_scope_proposals_and_top_advisories():
    round_ = make_round()
    round_.integrate(draft=make_draft(out_of_scope=True))
    view = dto.round_view(round_)

    assert view.advisories  # 顶部提示
    assert "s8" in view.advisories[0]
    warnings = {proposal.proposal_id: proposal.warning for proposal in view.draft.proposals}
    assert warnings["p02"] is not None
    assert warnings["p01"] is None


def test_round_view_exposes_question_kind_options_and_consequence():
    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.SINGLE,
                    title="单次导出最大行数？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                    options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
                    evidence_refs=(EvidenceId("E1"),),
                    asked_by=(SEAT,),
                ),
            ),
        )
    )
    view = dto.round_view(round_)

    assert view.waiting_on == "questionnaire"
    assert view.form_id == "f1"
    assert view.questions[0].kind == "gap"
    assert view.questions[0].options == (("a", "少于 1 万"), ("b", "1 万–10 万"))
    assert view.questions[0].asked_by == (SEAT,)
    assert view.questions[0].consequence_if_skipped == "按同步实现"


def test_round_view_surfaces_failure_reason_and_resume_point():
    round_ = make_round()
    round_.fail("席位 seat.permissions 返回的报文过不了 schema")
    view = dto.round_view(round_)
    assert view.step == "failed"
    assert "seat.permissions" in view.failure_reason


def test_workspace_view_carries_project_document_and_round():
    document = Document.new(
        document_id=DocumentId("doc1"), project_id=ProjectId("p1"), title="订单中台", now=NOW
    )
    project = dto.ProjectSummaryView(
        project_id="p1", title="订单中台", version_no=1, updated_at=NOW
    )
    view = dto.workspace_view(project=project, document=document.view(), round_=make_round())
    assert view.project.title == "订单中台"
    assert view.document.version_no == 1
    assert view.round is not None
