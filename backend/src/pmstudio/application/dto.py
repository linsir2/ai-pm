"""只读投影：界面只认这些结构，不认识聚合内部。

这里不做任何业务判断——"要不要出问卷""能不能写"全由领域决定，这里只是把结论摆出来。
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from pmstudio.application.ports import SeatCatalog
from pmstudio.domain.authoring.views import DocumentView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Proposal
from pmstudio.domain.round.opinion import OpinionForm, SeatReview
from pmstudio.domain.round.round import Round
from pmstudio.domain.services.scope_guard import ScopeGuard

SEAT_COMPLETED = "completed"
SEAT_PENDING = "pending"


@dataclass(frozen=True, slots=True)
class SeatSuggestionView:
    """开场卡上的一个席位提名：为什么请它 = 它的 focus。"""

    seat_id: str
    title: str
    focus: str


@dataclass(frozen=True, slots=True)
class ClaimView:
    claim_id: str
    eid: str
    text: str
    label: str
    target_label: str
    raw_span: str
    assumption_note: str | None = None


@dataclass(frozen=True, slots=True)
class OpinionView:
    local_id: str
    form: str
    about: str
    claim: str
    because: str
    eids: tuple[str, ...]
    basis_note: str | None = None
    eid: str | None = None  # 这条判断自己拿到的编号（账本按 seat_id+local_id 反查）


@dataclass(frozen=True, slots=True)
class SeatPacketView:
    seat_id: str
    title: str
    status: str
    pass_index: int
    basis: str
    opinions: tuple[OpinionView, ...]
    judgment_count: int
    question_count: int


@dataclass(frozen=True, slots=True)
class QuestionView:
    question_id: str
    kind: str
    type: str
    title: str
    why: str
    target_label: str
    options: tuple[tuple[str, str], ...]
    consequence_if_skipped: str
    eids: tuple[str, ...]
    asked_by: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposalView:
    proposal_id: str
    entry_id: str
    op: str
    target_label: str
    text: str
    origin_type: str
    eids: tuple[str, ...]
    rationale: str | None
    kind: str | None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class DraftView:
    draft_id: str
    groups: tuple[tuple[str, tuple[ProposalView, ...]], ...]
    proposals: tuple[ProposalView, ...]
    gaps: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RoundView:
    round_id: str
    project_id: str
    user_input: str
    step: str
    waiting_on: str
    seat_round_count: int
    seats: tuple[str, ...]
    nominated_seats: tuple[SeatSuggestionView, ...]
    claims: tuple[ClaimView, ...]
    seat_packets: tuple[SeatPacketView, ...]
    questions: tuple[QuestionView, ...]
    list_items: tuple[str, ...]
    draft: DraftView | None
    advisories: tuple[str, ...]
    failure_reason: str | None
    confirmed_version_no: int | None
    form_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectSummaryView:
    project_id: str
    title: str
    version_no: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    project: ProjectSummaryView
    document: DocumentView
    round: RoundView | None


def round_view(
    round_: Round,
    *,
    seat_titles: Mapping[str, str] = {},
    catalog: SeatCatalog | None = None,
    ledger: EvidenceLedger | None = None,
) -> RoundView:
    warnings: dict[str, str] = {}
    advisories: tuple[str, ...] = ()
    draft_view: DraftView | None = None
    if round_.draft is not None:
        violations = ScopeGuard().check(round_.draft.proposals, write_scope=round_.write_scope)
        warnings = {str(item.proposal_id): item.message for item in violations}
        advisories = tuple(f"{item.target.label()}：{item.message}" for item in violations)
        draft_view = _draft_view(round_.draft, warnings=warnings)
    questionnaire = round_.questionnaire
    return RoundView(
        round_id=str(round_.round_id),
        project_id=str(round_.project_id),
        user_input=round_.user_input,
        step=str(round_.step),
        waiting_on=str(round_.waiting_on),
        seat_round_count=round_.seat_round_count,
        seats=tuple(round_.seats),
        nominated_seats=_suggestions(round_, catalog=catalog, seat_titles=seat_titles),
        claims=tuple(_claim_view(claim) for claim in round_.claims),
        seat_packets=_seat_packets(
            round_, seat_titles=seat_titles, ledger=ledger, catalog=catalog
        ),
        questions=_question_views(round_),
        list_items=tuple(item.text for item in (questionnaire.list_items if questionnaire else ())),
        draft=draft_view,
        advisories=advisories,
        failure_reason=round_.failure.reason if round_.failure is not None else None,
        confirmed_version_no=round_.confirmed_version_no,
        form_id=None if questionnaire is None else str(questionnaire.form_id),
    )


def workspace_view(
    *,
    project: ProjectSummaryView,
    document: DocumentView,
    round_: Round | None,
    seat_titles: Mapping[str, str] = {},
    catalog: SeatCatalog | None = None,
    ledger: EvidenceLedger | None = None,
) -> WorkspaceView:
    return WorkspaceView(
        project=project,
        document=document,
        round=(
            None
            if round_ is None
            else round_view(round_, seat_titles=seat_titles, catalog=catalog, ledger=ledger)
        ),
    )


def _suggestions(
    round_: Round, *, catalog: SeatCatalog | None, seat_titles: Mapping[str, str]
) -> tuple[SeatSuggestionView, ...]:
    """开场卡要的就是"提名了谁 + 为什么请它"（F8）。"""

    if catalog is not None and not round_.seats:
        return tuple(
            SeatSuggestionView(seat_id=seat.seat_id, title=seat.title, focus=seat.focus)
            for seat in catalog.nominate(user_input=round_.user_input)
        )
    return tuple(
        SeatSuggestionView(
            seat_id=seat_id,
            title=seat_titles.get(seat_id, seat_id),
            focus=catalog.by_id(seat_id).focus if catalog is not None else "",
        )
        for seat_id in round_.seats
    )


def _claim_view(claim) -> ClaimView:  # type: ignore[no-untyped-def]
    note = None
    if claim.assumption is not None:
        note = (
            f"失败后果：{claim.assumption.failure_consequence}；"
            f"验证：{claim.assumption.verification}"
        )
    return ClaimView(
        claim_id=claim.claim_id,
        eid=str(claim.eid),
        text=claim.text,
        label=claim.label.value,
        target_label=claim.target.label(),
        raw_span=claim.raw_span,
        assumption_note=note,
    )


def _seat_packets(
    round_: Round,
    *,
    seat_titles: Mapping[str, str],
    ledger: EvidenceLedger | None = None,
    catalog: SeatCatalog | None = None,
) -> tuple[SeatPacketView, ...]:
    by_seat = {review.seat_id: review for review in round_.current_reviews}
    # 还没选席位时（开场卡阶段）显示被提名的席位，状态是"待启动"——
    # 界面在开场卡上就要看到"接下来谁会跑"。
    seat_ids = round_.seats or tuple(
        seat.seat_id
        for seat in (catalog.nominate(user_input=round_.user_input) if catalog else ())
    )
    packets: list[SeatPacketView] = []
    for seat_id in seat_ids:
        review = by_seat.get(seat_id)
        opinions = (
            tuple(
                OpinionView(
                    local_id=opinion.local_id,
                    form=opinion.form.value,
                    about=opinion.about,
                    claim=opinion.claim,
                    because=opinion.because,
                    eids=tuple(str(eid) for eid in opinion.evidence_refs),
                    basis_note=opinion.basis_note,
                    eid=_eid_for(review, opinion, ledger=ledger),
                )
                for opinion in review.opinions
            )
            if review is not None
            else ()
        )
        packets.append(
            SeatPacketView(
                seat_id=seat_id,
                title=seat_titles.get(seat_id, seat_id),
                status=SEAT_COMPLETED if review is not None else SEAT_PENDING,
                pass_index=review.pass_index if review is not None else round_.seat_round_count,
                basis=review.basis if review is not None else "",
                opinions=opinions,
                judgment_count=sum(
                    1 for opinion in opinions if opinion.form == OpinionForm.JUDGMENT.value
                ),
                question_count=sum(
                    1 for opinion in opinions if opinion.form == OpinionForm.QUESTION.value
                ),
            )
        )
    return tuple(packets)


def _eid_for(
    review: SeatReview | None, opinion, *, ledger: EvidenceLedger | None  # type: ignore[no-untyped-def]
) -> str | None:
    """判断自己拿到的编号：按 (seat_id, f"p{pass}:{local_id}") 从账本反查（计划 §3.1 ⑤）。"""

    if ledger is None or review is None:
        return None
    evidence = ledger.find_by_local(
        seat_id=review.seat_id, local_id=f"p{review.pass_index}:{opinion.local_id}"
    )
    return None if evidence is None else str(evidence.eid)


def _question_views(round_: Round) -> tuple[QuestionView, ...]:
    questionnaire = round_.questionnaire
    if questionnaire is None:
        return ()
    return tuple(
        QuestionView(
            question_id=str(question.question_id),
            kind=question.kind.value,
            type=question.type.value,
            title=question.title,
            why=question.why,
            target_label=question.target.label(),
            options=tuple((option.option_id, option.text) for option in question.options),
            consequence_if_skipped=question.consequence_if_skipped,
            eids=tuple(str(eid) for eid in question.evidence_refs),
            asked_by=tuple(question.asked_by),
        )
        for question in questionnaire.questions
    )


def _proposal_view(proposal: Proposal, *, warning: str | None) -> ProposalView:
    return ProposalView(
        proposal_id=str(proposal.proposal_id),
        entry_id=str(proposal.entry_id),
        op=proposal.op.value,
        target_label=proposal.target.label(),
        text=proposal.text,
        origin_type=proposal.origin.type.value,
        eids=tuple(str(eid) for eid in proposal.origin.refs),
        rationale=proposal.origin.rationale,
        kind=proposal.kind.value if proposal.kind is not None else None,
        warning=warning,
    )


def _draft_view(draft: Draft, *, warnings: Mapping[str, str]) -> DraftView:
    proposals = tuple(
        _proposal_view(proposal, warning=warnings.get(str(proposal.proposal_id)))
        for proposal in draft.proposals
    )
    by_id = {item.proposal_id: item for item in proposals}
    groups = tuple(
        (title, tuple(by_id[str(proposal.proposal_id)] for proposal in items))
        for title, items in draft.grouped()
    )
    return DraftView(
        draft_id=str(draft.draft_id),
        groups=groups,
        proposals=proposals,
        gaps=tuple(draft.gaps),
    )
