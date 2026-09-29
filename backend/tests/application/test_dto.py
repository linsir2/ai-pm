from pmstudio.application import dto
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import DroppedItem, DropSource, Round, SeatNomination
from pmstudio.domain.shared.ids import (
    DocumentId,
    DraftId,
    EntryId,
    EvidenceId,
    FeatureSlug,
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


def test_round_view_exposes_dropped_channel():
    """留痕投影要有地址、席位、趟数、原因、来源（06 §2.2：界面据此挂到席位包下面）。

    旧契约里这是一串裸字符串（`Questionnaire.dropped` → `dropped_attributions`）；
    P5b 合成一条通道后，原因与来源也要跟着上桌——旧字段由新通道派生。
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
                    title="单次导出最大行数？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                ),
            ),
        ),
        dropped=(
            DroppedItem(
                handle="seat.nope:O1",
                reason="归因对不上号：这一轮没有这条席位条目",
                source=DropSource.ASSEMBLY,
                seat_id=None,
                pass_index=1,
            ),
            DroppedItem(
                handle="seat.data_scale:O2",
                reason="与文档已有内容重复",
                source=DropSource.INTEGRATOR,
                seat_id="seat.data_scale",
                pass_index=1,
            ),
        ),
    )

    view = dto.round_view(round_)

    assert [
        (item.handle, item.seat_id, item.pass_index, item.source, item.reason)
        for item in view.dropped
    ] == [
        ("seat.nope:O1", None, 1, "assembly", "归因对不上号：这一轮没有这条席位条目"),
        ("seat.data_scale:O2", "seat.data_scale", 1, "integrator", "与文档已有内容重复"),
    ]
    assert dto.round_view(make_round()).dropped == ()


def test_round_view_surfaces_failure_reason_and_resume_point():
    round_ = make_round()
    round_.fail("席位 seat.permissions 返回的报文过不了 schema")
    view = dto.round_view(round_)
    assert view.step == "failed"
    assert "seat.permissions" in view.failure_reason


def test_round_view_says_where_retry_will_continue():
    """E3：光有"为什么死"不够——还要说清"重试会从哪继续、哪些活不用重跑"。"""

    round_ = make_round()  # `make_round()` 已经过完开场卡 → 停在 seats_running
    round_.fail("席位 seat.permissions 返回的报文过不了 schema")

    view = dto.round_view(round_)

    assert view.failure_at_step == "seats_running"
    assert view.retry_from == "从席位轮继续（已交回的不重跑）"


# —— C4：连续几轮一条"它提议的"都没采用（P14）——


def outcome(round_id: str, *, offered: int | None, accepted: int | None):
    return dto.RoundOutcomeView(
        round_id=round_id,
        step="done",
        proposed_offered=offered,
        proposed_accepted=accepted,
    )


def test_three_rounds_without_taking_any_proposal_make_a_streak():
    outcomes = (outcome("r3", offered=2, accepted=0), outcome("r2", offered=2, accepted=0),
                outcome("r1", offered=1, accepted=0))

    assert dto.proposal_ignore_streak(outcomes) == 3


def test_a_round_that_took_one_proposal_breaks_the_streak():
    outcomes = (outcome("r3", offered=2, accepted=1), outcome("r2", offered=2, accepted=0))

    assert dto.proposal_ignore_streak(outcomes) == 0


def test_a_round_that_never_confirmed_stops_the_count():
    """没走到确认的轮什么也说明不了（C1）：不跨过去数。"""

    outcomes = (outcome("r3", offered=None, accepted=None), outcome("r2", offered=2, accepted=0))

    assert dto.proposal_ignore_streak(outcomes) == 0


def test_a_round_with_nothing_offered_stops_the_count():
    outcomes = (outcome("r3", offered=0, accepted=0), outcome("r2", offered=2, accepted=0))

    assert dto.proposal_ignore_streak(outcomes) == 0


def test_the_draft_card_says_it_out_loud_when_the_streak_hits_three():
    round_ = round_for_coverage(
        claim_target=Target.body("s1"),           # 没点功能 → 不会有"零功能候选"那句抢位置
        proposal_target=Target.body("s1"),
    )
    round_.remember_proposal_ignore_streak(3)

    view = dto.round_view(round_)

    assert view.advisories[0].startswith("⚠ 最近连续 3 轮")
    assert "少请几个席位" in view.advisories[0]


def test_a_short_streak_keeps_quiet():
    round_ = round_for_coverage(
        claim_target=Target.body("s1"), proposal_target=Target.body("s1")
    )
    round_.remember_proposal_ignore_streak(2)

    assert dto.round_view(round_).advisories == ()


def test_retry_from_labels_follow_the_resume_routes():
    """这四句话对着 `RESUME_ROUTES`：认不出的 step 不猜（老库里的坏行）。"""

    assert dto.retry_from_label("started") == "从切分重新开始"
    assert dto.retry_from_label("claims_ready") == "等你确认复述卡（不用重跑模型）"
    assert dto.retry_from_label("seats_running") == "从席位轮继续（已交回的不重跑）"
    assert dto.retry_from_label("failed") is None
    assert dto.retry_from_label("") is None


def test_a_healthy_round_has_no_failure_point():
    view = dto.round_view(make_round())

    assert view.failure_at_step is None
    assert view.retry_from is None


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


def test_workspace_view_carries_readiness_items():
    """就绪度是文档投影的一部分：界面刷新一次就能拿到"差 N 项"。"""

    document = Document.new(
        document_id=DocumentId("doc1"), project_id=ProjectId("p1"), title="订单中台", now=NOW
    )
    project = dto.ProjectSummaryView(
        project_id="p1", title="订单中台", version_no=1, updated_at=NOW
    )
    items = ReadinessChecker().check(document.view())

    view = dto.workspace_view(
        project=project, document=document.view(), round_=None, readiness=items
    )

    assert {item.section_id for item in view.readiness} >= {"s1", "s5", "s8"}
    assert all(item.severity in {"blocker", "warn"} for item in view.readiness)
    assert [item.section_id for item in view.readiness] == [
        item.section_id for item in items
    ]


def test_workspace_view_without_readiness_is_empty():
    document = Document.new(
        document_id=DocumentId("doc1"), project_id=ProjectId("p1"), title="订单中台", now=NOW
    )
    view = dto.workspace_view(
        project=dto.ProjectSummaryView(
            project_id="p1", title="订单中台", version_no=1, updated_at=NOW
        ),
        document=document.view(),
        round_=None,
    )
    assert view.readiness == ()


class _TwoSeatCatalog:
    """两个席位：一个常驻、一个有触发词。用来对照"名单"与"目录匹配"的差别。"""

    def __init__(self) -> None:
        self._seats = (
            SeatDefinition(
                seat_id="seat.data_scale",
                title="数据规模与性能",
                focus="量级、耗时上限",
                always_on=True,
            ),
            SeatDefinition(
                seat_id="seat.export_import",
                title="导出与导入",
                focus="导出范围、格式、大文件",
                trigger_keywords=("导出",),
            ),
        )

    def all(self):
        return self._seats

    def nominate(self, *, user_input: str):
        return tuple(
            seat
            for seat in self._seats
            if seat.always_on or any(word in user_input for word in seat.trigger_keywords)
        )

    def by_id(self, seat_id: str):
        for seat in self._seats:
            if seat.seat_id == seat_id:
                return seat
        raise KeyError(seat_id)


def _opening_round(*, nominated: bool = True):
    round_ = Round.start(
        round_id=ROUND_ID,
        project_id=ProjectId("p1"),
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    if nominated:
        # 分析者只挑了导出这一个；常驻的数据规模它认为这轮不用来
        round_.nominate(
            [SeatNomination(seat_id="seat.export_import", why="这轮就是在做导出")]
        )
    return round_


def test_opening_card_uses_the_analyzer_nomination_not_the_catalog_match():
    """名单是判断的产物：目录匹配会给出两个，分析者只挑了一个 —— 开场卡显示一个。"""

    view = dto.round_view(_opening_round(), catalog=_TwoSeatCatalog())

    assert [seat.seat_id for seat in view.nominated_seats] == ["seat.export_import"]
    assert view.nominated_seats[0].why == "这轮就是在做导出"
    assert view.nominated_seats[0].source == "agent"
    # 常驻的席位并没有消失：它以"还能加的"身份留在旁边
    assert [seat.seat_id for seat in view.available_seats] == ["seat.data_scale"]


def test_catalog_fallback_is_marked_as_such_for_older_rounds():
    """老轮次（分析时还没席位名单）才回落目录匹配，并且必须标出来源。"""

    view = dto.round_view(_opening_round(nominated=False), catalog=_TwoSeatCatalog())

    assert [seat.seat_id for seat in view.nominated_seats] == [
        "seat.data_scale",
        "seat.export_import",
    ]
    assert {seat.source for seat in view.nominated_seats} == {"catalog"}
    assert view.nominated_seats[0].why == ""
    assert view.nominated_seats[1].matched_keywords == ("导出",)


def test_opening_stage_packets_list_the_nominated_seats():
    view = dto.round_view(_opening_round(), catalog=_TwoSeatCatalog())

    assert [packet.seat_id for packet in view.seat_packets] == ["seat.export_import"]
    assert view.seat_packets[0].status == "pending"


def test_selected_seats_win_over_the_nomination_once_the_round_started():
    """用户确认之后，展示的是他选的那批（可能加了名单外的席位）。"""

    round_ = _opening_round()
    round_.submit_opening(seats=("seat.export_import", "seat.data_scale"))

    view = dto.round_view(round_, catalog=_TwoSeatCatalog())

    assert [seat.seat_id for seat in view.nominated_seats] == [
        "seat.export_import",
        "seat.data_scale",
    ]


def round_for_coverage(*, claim_target: Target, proposal_target: Target) -> Round:
    """一个"有主张、有草案"的轮次：用来验零功能候选告警。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=ProjectId("p1"), user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1"), target=claim_target),))
    round_.submit_opening(seats=(SEAT,))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND_ID,
            proposals=(
                Proposal(
                    proposal_id=ProposalId("p01"),
                    entry_id=EntryId("r1#01"),
                    op=WriteOp.ADD,
                    target=proposal_target,
                    text="现状：导出靠手工复制",
                    origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
                    kind=BlockKind.TEXT,
                ),
            ),
        )
    )
    return round_


def test_advisory_when_claims_name_features_but_the_draft_has_none():
    """真跑实测（报告 F1）：主张点了 5 个功能，草案 s5 一条没有，卡片上什么都不说。"""

    round_ = round_for_coverage(
        claim_target=Target.feature_field(FeatureSlug("order-export"), "one_liner"),
        proposal_target=Target.body("s1"),
    )

    advisory = dto.feature_coverage_advisory(round_)

    assert advisory is not None
    assert "order-export" in advisory
    assert "s5" in advisory


def test_no_advisory_when_the_draft_has_feature_content():
    round_ = round_for_coverage(
        claim_target=Target.feature_field(FeatureSlug("order-export"), "one_liner"),
        proposal_target=Target.feature_field(FeatureSlug("order-export"), "goal"),
    )

    assert dto.feature_coverage_advisory(round_) is None


def test_no_advisory_when_the_input_never_mentioned_a_feature():
    round_ = round_for_coverage(
        claim_target=Target.body("s8"), proposal_target=Target.body("s1")
    )

    assert dto.feature_coverage_advisory(round_) is None


def test_no_advisory_before_there_is_a_draft():
    round_ = make_round()

    assert dto.feature_coverage_advisory(round_) is None


def test_round_view_puts_the_coverage_advisory_first():
    round_ = round_for_coverage(
        claim_target=Target.feature_field(FeatureSlug("order-export"), "one_liner"),
        proposal_target=Target.body("s1"),
    )

    view = dto.round_view(round_)

    assert view.advisories[0].startswith("⚠")


def test_schema_view_freezes_the_shape():
    """`GET /schema` 的形状：13 章（含说明与槽）、12 个功能字段、三个上限常量。"""

    view = dto.schema_view()

    assert [item.section_id for item in view.sections] == [f"s{i}" for i in range(1, 14)]
    assert all(item.purpose.strip() for item in view.sections)
    assert all(item.slots == () for item in view.sections if item.section_id != "s8")

    s5 = next(item for item in view.sections if item.section_id == "s5")
    assert s5.allows_features is True
    s8 = next(item for item in view.sections if item.section_id == "s8")
    assert [slot.name for slot in s8.slots] == [
        "performance",
        "availability",
        "security",
        "compatibility",
        "observability",
        "cost",
    ]

    assert len(view.feature_fields) == 12
    assert [item.name for item in view.feature_fields][:2] == ["name", "one_liner"]
    assert [item.name for item in view.feature_fields if item.required] == [
        "name",
        "one_liner",
        "goal",
        "audience",
        "exceptions",
        "acceptance",
    ]

    assert view.limits == dto.LimitsView(
        max_seat_rounds=3, max_questionnaires=2, max_open_items_per_batch=3
    )
