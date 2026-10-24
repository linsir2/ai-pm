from types import SimpleNamespace

from pmstudio.application import dto
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.retrieval import (
    ManualChunk,
    RetrievalHit,
    RetrievalStage,
    RetrievalTrace,
)
from pmstudio.domain.round.round import (
    DroppedItem,
    DropSource,
    MaterialRef,
    Round,
    SeatNomination,
)
from pmstudio.domain.round.web_query import WebQuery
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
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import (
    NOW,
    PROJECT,
    ROUND_ID,
    SEAT,
    STD,
    document_with_feature,
    make_claim,
    make_document,
    make_material_packet,
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
        project_id="p1",
        title="订单中台",
        version_no=1,
        updated_at=NOW,
        template_id="standard",
        template_title="标准版",
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
        project_id="p1",
        title="订单中台",
        version_no=1,
        updated_at=NOW,
        template_id="standard",
        template_title="标准版",
    )
    items = ReadinessChecker().check(document.view(), adapter=STD)

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
            project_id="p1",
            title="订单中台",
            version_no=1,
            updated_at=NOW,
            template_id="standard",
            template_title="标准版",
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


def round_with_queries() -> Round:
    """复述卡上的一轮：分析者提名两条，用户勾了第二条。"""

    round_ = _opening_round()
    round_.attach_web_queries(
        (
            WebQuery(query="RBAC 权限模型", why="要查行业通行做法"),
            WebQuery(query="订单导出的行业规范", why="要查行业标准"),
        )
    )
    round_.submit_opening(seats=(SEAT,), web_queries_chosen=("订单导出的行业规范",))
    return round_


def test_round_view_carries_the_nominated_queries_and_what_the_user_picked():
    """复述卡要能说清"它建议查什么、为什么、你勾了哪条"。"""

    view = dto.round_view(round_with_queries(), web_search_available=True)

    assert [(item.query, item.why, item.chosen) for item in view.web_queries] == [
        ("RBAC 权限模型", "要查行业通行做法", False),
        ("订单导出的行业规范", "要查行业标准", True),
    ]
    assert view.web_search_available is True


def test_web_search_is_unavailable_unless_somebody_says_otherwise():
    """拿不到"配没配工具"这个事实时，默认是**不可用**：宁可少显示一栏，也不许骗人能查。"""

    view = dto.round_view(make_round())

    assert view.web_queries == ()
    assert view.web_search_available is False


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
        claim_target=Target.feature_field(
                         FeatureSlug("order-export"), "one_liner",
                         home=STD.feature_home
                     ),
        proposal_target=Target.body("s1"),
    )

    advisory = dto.feature_coverage_advisory(round_)

    assert advisory is not None
    assert "order-export" in advisory
    assert "s5" in advisory


def test_no_advisory_when_the_draft_has_feature_content():
    round_ = round_for_coverage(
        claim_target=Target.feature_field(
                         FeatureSlug("order-export"), "one_liner",
                         home=STD.feature_home
                     ),
        proposal_target=Target.feature_field(
                            FeatureSlug("order-export"), "goal",
                            home=STD.feature_home
                        ),
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
        claim_target=Target.feature_field(
                         FeatureSlug("order-export"), "one_liner",
                         home=STD.feature_home
                     ),
        proposal_target=Target.body("s1"),
    )

    view = dto.round_view(round_)

    assert view.advisories[0].startswith("⚠")


def test_schema_view_freezes_the_shape():
    """`GET /schema` 的形状：13 章（含说明与槽）、12 个功能字段、三个上限常量。"""

    view = dto.schema_view(discussion_roles=_three_roles())

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
        max_seat_rounds=3,
        max_questionnaires=2,
        max_open_items_per_batch=3,
        max_sim_review_personas=3,
        max_sim_review_features=2,
        max_discussion_turns=30,
    )


def _three_roles():
    """就地造三个角色（与 `roles.toml` 同 id）：`schema_view` 只按鸭子类型读三个字段。"""

    return (
        SimpleNamespace(role_id="dlg.challenger", title="反方", focus="找漏洞、要反例、泼冷水"),
        SimpleNamespace(role_id="dlg.user_voice", title="用户之声", focus="替真实用户说话"),
        SimpleNamespace(role_id="dlg.analogist", title="类比者", focus="别人家怎么做过"),
    )


def test_schema_view_carries_the_discussion_roles():
    """讨论角色进 `/schema`：三行、字段齐全（灵感区不再自己抄一份）。"""

    view = dto.schema_view(discussion_roles=_three_roles())

    assert [(item.role_id, item.title, item.focus) for item in view.discussion_roles] == [
        ("dlg.challenger", "反方", "找漏洞、要反例、泼冷水"),
        ("dlg.user_voice", "用户之声", "替真实用户说话"),
        ("dlg.analogist", "类比者", "别人家怎么做过"),
    ]


def question(question_id: str, title: str, *, options: bool = False) -> Question:
    return Question(
        question_id=QuestionId(question_id),
        target=Target.body("s8"),
        type=QuestionType.SINGLE if options else QuestionType.TEXT,
        title=title,
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现，超时风险由 PM 承担",
        options=(Option("a", "自建机房"), Option("b", "云服务")) if options else (),
    )


def round_with_two_answer_batches() -> Round:
    """一批：答 1 题（选了选项）+ 答 1 题（自由文本）+ 跳过 1 题；另一批：整批跳过。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=ProjectId("p1"), user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f-1"),
            round_id=ROUND_ID,
            questions=(
                question("q1", "存储选哪家", options=True),
                question("q2", "同步还是异步"),
                question("q3", "记录保存几年"),
            ),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f-1"),
            answers=(
                Answer(question_id=QuestionId("q1"), text="", selected=("a",)),
                Answer(question_id=QuestionId("q2"), text="先同步，后面再说"),
            ),
            skipped=(QuestionId("q3"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f-2"),
            round_id=ROUND_ID,
            questions=(question("q4", "要不要做多语言"),),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f-2"),
            answers=(),
            skipped=(QuestionId("q4"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )
    return round_


def test_answer_sets_projection_counts_each_batch_and_keeps_the_text():
    """两批各自计数；跳过单独算；文本是「题目 → 选项；备注」这种人话，不是选项 id。"""

    view = dto.round_view(round_with_two_answer_batches())

    assert [item.form_id for item in view.answer_sets] == ["f-1", "f-2"]
    first = view.answer_sets[0]
    assert (first.answered, first.skipped) == (2, 1)
    assert first.answers == ("存储选哪家 → 自建机房", "同步还是异步 → 先同步，后面再说")
    second = view.answer_sets[1]
    assert (second.answered, second.skipped) == (0, 1)
    assert second.answers == ()


def test_asked_questions_span_every_batch_while_questions_stay_current():
    """`questions` 仍是当前批（界面用），`asked_questions` 是整轮（判据用）——两份语义钉死。

    真跑教训（P30 完整 eval 的 `edge-contradiction`，轮 `r-8889fcf0`）：
    `q4 kind=conflict` 在第 1 批，用户跳过之后当前批只剩两道 gap 题；
    只看 `questions` 会把"问过互斥题"读成"没问过"。
    """

    view = dto.round_view(round_with_two_answer_batches())

    assert [item.question_id for item in view.questions] == ["q4"]
    assert [item.question_id for item in view.asked_questions] == ["q1", "q2", "q3", "q4"]
    # 当前批是整轮的最后一批：同一道题在两份投影里是同一个视图对象
    assert view.asked_questions[-1] == view.questions[0]


def test_a_round_that_never_asked_has_no_asked_questions():
    assert dto.round_view(make_round()).asked_questions == ()


def test_a_round_that_never_asked_has_no_answer_sets():
    assert dto.round_view(make_round()).answer_sets == ()


def test_round_view_shows_the_material_chunks_that_went_in():
    """界面要知道"这一轮带了哪份材料的哪几块、它们的编号与锚点"（工具层 C-2）。"""

    round_ = make_round()
    round_.add_materials(
        (
            MaterialRef(material_id="m1", chunk_id="m1#c00", eid=EvidenceId("E9")),
            MaterialRef(material_id="m1", chunk_id="m1#c01", eid=EvidenceId("E10")),
        )
    )
    packet = make_material_packet("目标：导出", "边界：只给管理员", title="需求.md")

    view = dto.round_view(round_, materials=(packet,))

    assert len(view.materials) == 1
    card = view.materials[0]
    assert (card.title, card.source_ref, card.parser_id) == (
        "需求.md",
        "attachment:需求.md",
        "local-text",
    )
    assert card.chunk_count == 2
    assert [chunk.eid for chunk in card.chunks] == ["E9", "E10"]
    assert [chunk.anchor for chunk in card.chunks] == ["第 1 行", "第 2 行"]
    assert card.chunks[0].text == "目标：导出"


def test_a_round_without_materials_shows_none_even_if_packets_are_passed():
    """材料是"这一轮带进来的"，不是"项目里有的"——没引用就不显示。"""

    view = dto.round_view(make_round(), materials=(make_material_packet("别的材料"),))

    assert view.materials == ()


def test_search_finds_entries_features_ids_and_opinions():
    """F6：一次搜四类东西——文档条目 / 功能 / 出处编号 / 席位发言。"""

    results = dto.search_hits(
        "导出",
        document=document_with_feature(one_liner="把订单导出成表格").view(),
        evidence=(("E7", "用户说：订单要能导出"),),
        opinions=(
            dto.OpinionSourceView(
                round_id="r1", seat_id="seat.data_scale", claim="导出的量级要先定"
            ),
        ),
    )

    kinds = [hit.kind for hit in results.hits]
    assert "entry" in kinds          # 功能字段里的那句话
    assert "feature" in kinds        # 功能自己的 slug
    assert "evidence" in kinds       # 出处原话
    assert "opinion" in kinds        # 席位发言
    assert results.truncated is False
    assert results.query == "导出"

    feature_hit = next(hit for hit in results.hits if hit.kind == "feature")
    assert feature_hit.ref.startswith("feat-")
    opinion_hit = next(hit for hit in results.hits if hit.kind == "opinion")
    assert opinion_hit.ref == "r1"


def test_blank_query_returns_nothing_instead_of_everything():
    results = dto.search_hits(
        "   ",
        document=document_with_feature().view(),
        evidence=(("E7", "什么都搜得到"),),
        opinions=(),
    )

    assert results.hits == ()


def test_material_hits_land_last_with_their_title_and_anchor():
    """F6 的第五类来源：材料块（`P18`）。排在最后——人最常找的是"文档里哪句写了"。"""

    results = dto.search_hits(
        "导出",
        document=document_with_feature(one_liner="把订单导出成表格").view(),
        evidence=(("E7", "用户说：订单要能导出"),),
        opinions=(),
        materials=(
            dto.MaterialHit(
                material_id="m1",
                chunk_id="m1#c03",
                score=3.0,
                excerpt="…结论：导出要走异步队列…",
                anchor="第 3 页 §2.1",
                source_ref="attachment:诊断报告.pdf",
                title="诊断报告.pdf",
                origin="upload",
            ),
        ),
    )

    assert [hit.kind for hit in results.hits][-1] == "material"
    material = results.hits[-1]
    assert material.ref == "m1#c03"
    assert material.label == "诊断报告.pdf：…结论：导出要走异步队列…"
    assert material.where == "材料 · 第 3 页 §2.1"
    assert material.origin == "upload"


def test_search_stops_at_the_limit_and_says_it_truncated():
    evidence = tuple((f"E{number}", "导出") for number in range(1, 80))

    results = dto.search_hits(
        "导出", document=make_document().view(), evidence=evidence, opinions=(), limit=10
    )

    assert len(results.hits) == 10
    assert results.truncated is True


def test_round_head_keeps_the_first_three_claim_headlines():
    """跨轮可见的"上一轮你提过什么"：头 3 条，各自压一行；没有就是空。"""

    head = dto.round_head_view(
        round_id="r1",
        step="done",
        created_at=NOW,
        payload={
            "user_input": "想做个订单导出",
            "claims": [
                {"text": "  要能按时间筛选  "},
                {"text": "导出要有权限"},
                {"text": "月底前上线"},
                {"text": "第四条不该出现"},
            ],
        },
    )

    assert head.claim_headlines == ("要能按时间筛选", "导出要有权限", "月底前上线")

    nothing = dto.round_head_view(
        round_id="r2", step="started", created_at=NOW, payload={"user_input": "x", "claims": []}
    )
    assert nothing.claim_headlines == ()


def test_round_head_view_reads_only_the_head_and_survives_old_rows():
    """时间线的一行：输入压一行、数主张与题数；老行缺键就回落，不抛。"""

    head = dto.round_head_view(
        round_id="r1",
        step="done",
        created_at=NOW,
        payload={
            "user_input": "  想做订单导出  " + "很长" * 40,
            "claims": [{"claim_id": "c1"}, {"claim_id": "c2"}],
            "questionnaire_history": [
                {"questions": [{"question_id": "q1"}]},
                {"questions": [{"question_id": "q2"}, {"question_id": "q3"}]},
            ],
            "confirmed_version_no": 3,
        },
    )

    assert head.round_id == "r1"
    assert head.step == "done"
    assert head.created_at == NOW
    assert head.claims_count == 2
    assert head.questions_count == 3
    assert head.confirmed_version_no == 3
    assert len(head.user_input) <= 61        # 压过一行（带一个省略号）
    assert "\n" not in head.user_input

    old = dto.round_head_view(
        round_id="r2",
        step="seats_running",
        created_at=NOW,
        payload={"user_input": "x", "claims": [], "questionnaire": {"questions": []}},
    )
    assert old.questions_count == 0
    assert old.confirmed_version_no is None


def test_round_view_projects_packet_id_from_attested_review():
    """REST 投影必须带 `packet_id`（真跑抓到的漂移：SSE 事件有、REST 没有，
    前端类型却声明必填——留痕精确匹配在 REST 路径下静默降级）。"""

    round_ = make_round(seats=(SEAT,))
    round_.attest(
        SeatReview(seat_id=SEAT, basis="读了 s5 与 E1", opinions=()),
        packet_id="pkt-r1-seat_review-seat.data_scale-p1a1",
    )
    view = dto.round_view(round_, seat_titles={SEAT: "数据规模与性能"})
    assert view.seat_packets[0].packet_id == "pkt-r1-seat_review-seat.data_scale-p1a1"


def test_round_view_packet_id_is_none_for_reviews_attested_without_one():
    """老数据（修之前落库的轮次）没有包 id：投影给 None，不编一个。"""

    round_ = make_round(seats=(SEAT,))
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 s5 与 E1", opinions=()))
    view = dto.round_view(round_, seat_titles={SEAT: "数据规模与性能"})
    assert view.seat_packets[0].packet_id is None


def test_attest_preserves_packet_id_already_on_review():
    """review 自带的 id + 空 attest → 保留，不被默认 None 洗掉。

    覆盖 `Round.attest` 里那行 `stamped` 三元：给了参数才盖戳，
    没给必须原样保留。这是"给了才盖戳"语义的另一半（上一条只测了"没给→None"）。"""

    round_ = make_round(seats=(SEAT,))
    round_.attest(
        SeatReview(seat_id=SEAT, basis="读了 s5 与 E1", opinions=(), packet_id="pkt-keep")
    )
    view = dto.round_view(round_, seat_titles={SEAT: "数据规模与性能"})
    assert view.seat_packets[0].packet_id == "pkt-keep"


def test_round_view_carries_sim_inquiry_artifact() -> None:
    """RoundView.inquiry：没跑过是 None；跑过是 artifact + 次数 + 勾选（eval 与抽屉读它）。"""

    from tests.support.builders import make_round

    assert dto.round_view(make_round()).sim_inquiry is None

    round_ = make_round()
    round_.record_sim_inquiry(
        slices=(
            {
                "local_id": "S1",
                "situation": "运营月底要对账",
                "job": "按时间筛出订单",
                "trigger": "月底结账",
                "workaround": "导出全部再手工筛",
                "anchors": ["E1"],
            },
        ),
        hypotheses=(
            {
                "local_id": "H1",
                "statement": "运营宁愿等一分钟也不愿分两次导",
                "grounding": "document",
                "based_on": ["E1"],
                "falsification": "观察到运营主动分多次小批量导出",
                "evidence_needed": "跟两场月底结账",
            },
        ),
        unknowns=(),
    )
    round_.choose_sim_inquiry(("H1",))

    view = dto.round_view(round_).sim_inquiry
    assert view is not None
    assert view.runs == 1
    assert view.chosen == ("H1",)
    assert view.slices[0].anchors == ("E1",)
    assert view.hypotheses[0].falsification.startswith("观察到运营")
    assert view.unknowns == ()


# ── M11 模拟评测：投影与 /schema ─────────────────────────────────────────────


def _review_payload(evaluated_version: int = 3) -> dict:
    def feature(slug: str, verdict: str) -> dict:
        return {
            "feature_slug": slug,
            "started": True,
            "walkthrough": [
                {
                    "step_index": 1,
                    "step_text": "进入订单列表",
                    "from_doc": "en-flow",
                    "action": "打开订单列表页",
                    "outcome": "stuck",
                    "need": "文档没写导出按钮在哪",
                }
            ],
            "frictions": [
                {
                    "local_id": "F1",
                    "kind": "dead_end",
                    "entry_id": "en-flow",
                    "field": "",
                    "quote": "",
                    "not_documented": True,
                    "blocks_flow": True,
                    "note": "点了导出没有反应",
                }
            ],
            "acceptance_checks": [
                {"local_id": "A1", "entry_id": "en-acc1", "status": "unverified", "note": ""}
            ],
            "verdict": verdict,
            "verdict_because": ["F1"],
        }

    return {
        "evaluated_version": evaluated_version,
        "personas": [
            {
                "persona_id": "persona.first_touch",
                "title": "首次上手者",
                "features": [feature("order-export", "blocked")],
            },
            {
                "persona_id": "persona.edge_maker",
                "title": "异常状态制造者",
                "features": [feature("order-export", "friction")],
            },
        ],
        "advisories": ["功能 order-export：全体模拟使用者未能走通"],
    }


def test_round_view_carries_sim_review_artifact() -> None:
    """RoundView.sim_review：没跑过是 None；跑过是逐 persona 报告 + 计数 + advisories。"""

    from pmstudio.application.sim_review import MAX_SIM_REVIEWS_PER_DRAFT

    assert dto.round_view(make_round()).sim_review is None

    round_ = make_round()
    round_.record_sim_review(_review_payload())

    view = dto.round_view(round_).sim_review
    assert view is not None
    assert view.runs == 1
    assert view.max_runs == MAX_SIM_REVIEWS_PER_DRAFT
    assert view.evaluated_version == 3
    assert [item.persona_id for item in view.personas] == [
        "persona.first_touch",
        "persona.edge_maker",
    ]
    assert [item.title for item in view.personas] == ["首次上手者", "异常状态制造者"]
    feature = view.personas[0].features[0]
    assert feature.feature_slug == "order-export"
    assert feature.verdict == "blocked"
    assert feature.walkthrough[0].outcome == "stuck"
    assert feature.frictions[0].kind == "dead_end"
    assert feature.frictions[0].not_documented is True
    assert feature.acceptance_checks[0].status == "unverified"
    assert feature.verdict_because == ("F1",)
    assert (view.summary.smooth, view.summary.friction, view.summary.blocked) == (0, 1, 1)
    assert view.advisories == ("功能 order-export：全体模拟使用者未能走通",)


def test_proposal_view_marks_simulation_based_from_ledger() -> None:
    """`simulation_based` = 提案编号里有账本上的 SIMULATED——界面 tag 与 eval 断言都读它。"""

    from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
    from pmstudio.domain.provenance.ledger import EvidenceLedger

    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId(ROUND_ID), raw_span="想做个订单导出"),
        now=NOW,
    )
    sim_eid = ledger.record(
        kind=EvidenceKind.SIMULATED,
        text="点了导出没有反应",
        source=EvidenceSource(
            round_id=RoundId(ROUND_ID),
            external_ref="simulation:review:r1#persona.first_touch@order-export#F1",
        ),
        now=NOW,
    )

    round_ = make_round()
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=RoundId(ROUND_ID),
            proposals=(
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
                    proposal_id=ProposalId("sim#1"),
                    entry_id=EntryId("r1#02"),
                    op=WriteOp.REMOVE,
                    target=Target.body("s5", feature_slug="order-export"),
                    text="进入订单列表",
                    origin=Origin(OriginType.PROPOSED, (sim_eid,), rationale="模拟评测"),
                    kind=BlockKind.TEXT,
                ),
            ),
        )
    )

    view = dto.round_view(round_, ledger=ledger)
    flags = {item.proposal_id: item.simulation_based for item in view.draft.proposals}
    assert flags == {"p01": False, "sim#1": True}

    no_ledger = dto.round_view(round_)
    assert all(not item.simulation_based for item in no_ledger.draft.proposals), (
        "没带账本时不瞎标"
    )


def test_schema_view_exposes_personas_and_review_limits() -> None:
    from pmstudio.application.sim_review import (
        MAX_SIM_REVIEW_FEATURES,
        MAX_SIM_REVIEW_PERSONAS,
    )

    personas = (
        SimpleNamespace(persona_id="persona.p1", title="P1", situation="S1"),
        SimpleNamespace(persona_id="persona.p2", title="P2", situation="S2"),
    )

    view = dto.schema_view(personas=personas)

    assert [(item.persona_id, item.title, item.situation) for item in view.personas] == [
        ("persona.p1", "P1", "S1"),
        ("persona.p2", "P2", "S2"),
    ]
    assert view.limits.max_sim_review_personas == MAX_SIM_REVIEW_PERSONAS
    assert view.limits.max_sim_review_features == MAX_SIM_REVIEW_FEATURES
    assert dto.schema_view().personas == ()


def test_schema_view_exposes_discussion_turn_limit() -> None:
    """讨论轮上限进 schema.limits（M13）：界面不再离线抄一份（M9 的有意留白收口）。"""

    from pmstudio.domain.discussion.thread import MAX_DISCUSSION_TURNS

    assert dto.schema_view().limits.max_discussion_turns == MAX_DISCUSSION_TURNS


# —— P0.3：检索投影与材料命中身份 ——


def test_round_view_projects_retrievals_and_pinned_materials():
    round_ = make_round()
    round_.pin_materials(["m1", "m2"])
    round_.note_retrieval(
        RetrievalTrace(
            stage=RetrievalStage.SEAT,
            pass_index=1,
            query="导出权限",
            hits=(RetrievalHit("m1", "m1#c00", 3.0), RetrievalHit("m1", "m1#c01", 2.0)),
            selected=(RetrievalHit("m1", "m1#c01", 2.0),),
            ok=True,
            error=None,
            created_at=NOW,
        )
    )

    view = dto.round_view(round_)

    assert view.pinned_materials == ("m1", "m2")
    assert len(view.retrievals) == 1
    one = view.retrievals[0]
    assert (one.stage, one.pass_index, one.query) == ("seat", 1, "导出权限")
    assert (one.hit_count, one.selected_count) == (2, 1)
    assert one.chunk_ids == ("m1#c01",)
    assert one.ok is True and one.error is None
    assert one.created_at == NOW


def test_an_old_round_projects_empty_retrievals():
    view = dto.round_view(make_round())

    assert view.retrievals == ()
    assert view.pinned_materials == ()


def test_round_view_projects_manual_chunks_with_material_identity():
    round_ = make_round()
    round_.note_manual_chunks([ManualChunk("m1", "m1#c03")])

    view = dto.round_view(round_)

    assert [(item.material_id, item.chunk_id) for item in view.manual_chunks] == [
        ("m1", "m1#c03")
    ]


def test_a_material_hit_carries_material_and_chunk_identity():
    results = dto.search_hits(
        "导出",
        document=make_document().view(),
        evidence=(),
        opinions=(),
        materials=(
            dto.MaterialHit(
                material_id="m1",
                chunk_id="m1#c03",
                score=3.0,
                excerpt="…导出要走异步队列…",
                anchor="第 3 页",
                source_ref="attachment:诊断报告.pdf",
                title="诊断报告.pdf",
            ),
        ),
    )

    hit = next(one for one in results.hits if one.kind == "material")
    assert hit.material_id == "m1"
    assert hit.chunk_id == "m1#c03"
    assert hit.ref == "m1#c03"
