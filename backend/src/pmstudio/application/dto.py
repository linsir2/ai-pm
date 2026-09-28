"""只读投影：界面只认这些结构，不认识聚合内部。

这里不做任何业务判断——"要不要出问卷""能不能写"全由领域决定，这里只是把结论摆出来。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.application.deliverable import KIND_LABELS, TRIGGER_LABELS, summarize_line
from pmstudio.application.ports import SeatCatalog, SeatDefinition
from pmstudio.domain.authoring.diff import diff_snapshots
from pmstudio.domain.authoring.readiness import ReadinessItem
from pmstudio.domain.authoring.version import Version
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Proposal
from pmstudio.domain.round.opinion import OpinionForm, SeatReview
from pmstudio.domain.round.round import Round
from pmstudio.domain.services.scope_guard import ScopeGuard
from pmstudio.domain.shared.ids import EvidenceId

SEAT_COMPLETED = "completed"
SEAT_PENDING = "pending"


@dataclass(frozen=True, slots=True)
class SeatSuggestionView:
    """开场卡上的一个席位提名。

    "为什么请它"分三层说清：
    `why`（**分析者读完这段输入之后给的理由**，这是主口径）、
    `focus`（它管什么）、以及机械事实 `always_on` / `matched_keywords`（目录里的配置）。
    `source` 标出这条是分析者挑的（`agent`）还是老轮次回落目录匹配来的（`catalog`）——
    界面照实说，用户才好做减法。
    """

    seat_id: str
    title: str
    focus: str
    why: str = ""
    source: str = "agent"
    always_on: bool = False
    matched_keywords: tuple[str, ...] = ()


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
class DroppedView:
    """一条"没进卡片"的留痕：地址、席位、第几趟、原因、来源（`04` C5 后半）。"""

    handle: str
    seat_id: str | None
    pass_index: int
    reason: str
    source: str


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
    blocking: bool


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
    active_seats: tuple[str, ...]
    nominated_seats: tuple[SeatSuggestionView, ...]
    available_seats: tuple[SeatSuggestionView, ...]
    claims: tuple[ClaimView, ...]
    seat_packets: tuple[SeatPacketView, ...]
    dropped: tuple[DroppedView, ...]
    questions: tuple[QuestionView, ...]
    list_items: tuple[str, ...]
    draft: DraftView | None
    advisories: tuple[str, ...]
    failure_reason: str | None
    abandon_reason: str | None
    confirmed_version_no: int | None
    form_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectSummaryView:
    project_id: str
    title: str
    version_no: int
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReadinessItemView:
    section_id: str
    code: str
    title: str
    detail: str
    severity: str


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    project: ProjectSummaryView
    document: DocumentView
    round: RoundView | None
    readiness: tuple[ReadinessItemView, ...] = ()


@dataclass(frozen=True, slots=True)
class VersionSummaryView:
    """版本列表里的一行（底栏版本历史抽屉读它）。"""

    version_no: int
    trigger: str
    trigger_label: str
    summary: str
    created_at: datetime
    changed_block_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionListView:
    versions: tuple[VersionSummaryView, ...]
    current: int


@dataclass(frozen=True, slots=True)
class EntrySnapshotView:
    entry_id: str
    text: str
    refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ChangedEntryView:
    before: EntrySnapshotView
    after: EntrySnapshotView


@dataclass(frozen=True, slots=True)
class DiffGroupView:
    target_label: str
    added: tuple[EntrySnapshotView, ...]
    removed: tuple[EntrySnapshotView, ...]
    changed: tuple[ChangedEntryView, ...]


@dataclass(frozen=True, slots=True)
class DiffView:
    from_version_no: int
    to_version_no: int
    groups: tuple[DiffGroupView, ...]


@dataclass(frozen=True, slots=True)
class RollbackResult:
    version_no: int
    document: DocumentView


@dataclass(frozen=True, slots=True)
class ChainItemView:
    eid: str
    kind: str
    kind_label: str
    text: str
    summary: str


@dataclass(frozen=True, slots=True)
class EvidenceLookupView:
    """按编号查处方：原话、来路、链条、以及"现在被谁用着"（`04` F5）。"""

    eid: str
    kind: str
    kind_label: str
    text: str
    summary: str
    raw_span: str | None
    rationale: str | None
    derived_from: tuple[str, ...]
    round_id: str | None
    seat_id: str | None
    question_id: str | None
    chain: tuple[ChainItemView, ...]
    used_by: tuple[str, ...]


def feature_coverage_advisory(round_: Round) -> str | None:
    """这一轮"点到了功能、却一条功能内容都没有"的置顶告警（报告 F1）。

    纯投影：只读 claims 与 draft，不写任何东西、不进文档——所以它只出现在**这一轮**的卡片上
    （草案卡的最上面，前端 `DraftDrawer` 已经按这个顺序渲染 `advisories`）。

    判据刻意保守（宁可漏报，不许骚扰）：
    - 只有在"主张点名过功能"（claims 里有 `feature_slug`）时才可能报；
    - 草案里**一条** s5 功能提案都没有，才报；
    - 输入本来没提功能（如纯偏好那一轮）→ 不报。
    """

    if round_.draft is None:
        return None
    named = sorted(
        {
            str(claim.target.feature_slug)
            for claim in round_.claims
            if claim.target.feature_slug is not None
        }
    )
    if not named:
        return None
    if any(proposal.target.feature_slug is not None for proposal in round_.draft.proposals):
        return None
    return (
        f"⚠ 这一轮一条功能内容都没有：你点到了「{'、'.join(named)}」"
        f"{len(named)} 个功能，但草案里 s5 没有任何提案。"
        "要么这一轮补上功能内容（再提一轮，把每个功能一句话说清），"
        "要么明确「这轮不写功能」。"
    )


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
        # 置顶告警（报告 F1）："点到了功能、却一条功能内容都没有"比"哪条越界"更要紧
        coverage = feature_coverage_advisory(round_)
        advisories = ((coverage,) if coverage else ()) + tuple(
            f"{item.target.label()}：{item.message}" for item in violations
        )
        draft_view = _draft_view(round_.draft, warnings=warnings)
    questionnaire = round_.questionnaire
    nominated = _suggestions(round_, catalog=catalog, seat_titles=seat_titles)
    return RoundView(
        round_id=str(round_.round_id),
        project_id=str(round_.project_id),
        user_input=round_.user_input,
        step=str(round_.step),
        waiting_on=str(round_.waiting_on),
        seat_round_count=round_.seat_round_count,
        seats=tuple(round_.seats),
        active_seats=tuple(round_.active_seats),
        nominated_seats=nominated,
        available_seats=_available_seats(
            round_,
            catalog=catalog,
            already={seat.seat_id for seat in nominated},
        ),
        claims=tuple(_claim_view(claim) for claim in round_.claims),
        seat_packets=_seat_packets(
            round_, seat_titles=seat_titles, ledger=ledger, catalog=catalog
        ),
        dropped=tuple(
            DroppedView(
                handle=item.handle,
                seat_id=item.seat_id,
                pass_index=item.pass_index,
                reason=item.reason,
                source=item.source.value,
            )
            for item in round_.dropped
        ),
        questions=_question_views(round_),
        list_items=tuple(item.text for item in (questionnaire.list_items if questionnaire else ())),
        draft=draft_view,
        advisories=advisories,
        failure_reason=round_.failure.reason if round_.failure is not None else None,
        abandon_reason=round_.abandon_reason,
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
    readiness: Sequence[ReadinessItem] = (),
) -> WorkspaceView:
    return WorkspaceView(
        project=project,
        document=document,
        round=(
            None
            if round_ is None
            else round_view(round_, seat_titles=seat_titles, catalog=catalog, ledger=ledger)
        ),
        readiness=tuple(readiness_view(item) for item in readiness),
    )


def readiness_view(item: ReadinessItem) -> ReadinessItemView:
    return ReadinessItemView(
        section_id=item.section_id,
        code=item.code,
        title=item.title,
        detail=item.detail,
        severity=item.severity.value,
    )


def version_summary_view(version: Version) -> VersionSummaryView:
    return VersionSummaryView(
        version_no=version.version_no,
        trigger=version.trigger.value,
        trigger_label=TRIGGER_LABELS[version.trigger],
        summary=version.summary,
        created_at=version.created_at,
        changed_block_ids=tuple(str(item) for item in version.changed_block_ids),
    )


def _entry_snapshot_view(item) -> EntrySnapshotView:  # type: ignore[no-untyped-def]
    return EntrySnapshotView(
        entry_id=str(item.entry_id),
        text=item.text,
        refs=tuple(str(ref) for ref in item.refs),
    )


def diff_view(before: Version, after: Version) -> DiffView:
    """两个版本 → 界面要的形状（分组、前后对照、编号照抄）。"""

    return DiffView(
        from_version_no=before.version_no,
        to_version_no=after.version_no,
        groups=tuple(
            DiffGroupView(
                target_label=group.target_label,
                added=tuple(_entry_snapshot_view(item) for item in group.added),
                removed=tuple(_entry_snapshot_view(item) for item in group.removed),
                changed=tuple(
                    ChangedEntryView(
                        before=_entry_snapshot_view(item.before),
                        after=_entry_snapshot_view(item.after),
                    )
                    for item in group.changed
                ),
            )
            for group in diff_snapshots(before.snapshot, after.snapshot)
        ),
    )


def _chain_item_view(evidence) -> ChainItemView:  # type: ignore[no-untyped-def]
    return ChainItemView(
        eid=str(evidence.eid),
        kind=evidence.kind.value,
        kind_label=KIND_LABELS[evidence.kind],
        text=evidence.text,
        summary=summarize_line(evidence.text),
    )


def used_by_entries(view: DocumentView, eid: EvidenceId) -> tuple[str, ...]:
    """文档里哪些条目引用了这个编号（界面据此滚到那条内容并闪一下）。"""

    def walk(block: BlockView) -> list:
        found = [entry for entry in block.entries if eid in entry.refs]
        for child in block.children:
            found += walk(child)
        return found

    entries = [
        entry
        for section in view.sections
        for block in section.blocks
        for entry in walk(block)
    ]
    entries += [entry for entry in view.preference_entries if eid in entry.refs]
    return tuple(str(entry.entry_id) for entry in entries)


def evidence_lookup_view(
    evidence,  # type: ignore[no-untyped-def]
    *,
    chain: Sequence,  # type: ignore[type-arg]
    used_by: tuple[str, ...],
) -> EvidenceLookupView:
    source = evidence.source
    return EvidenceLookupView(
        eid=str(evidence.eid),
        kind=evidence.kind.value,
        kind_label=KIND_LABELS[evidence.kind],
        text=evidence.text,
        summary=summarize_line(evidence.text),
        # 原话只在界面上给（点编号能看），导出里没有（`04` D5）
        raw_span=source.raw_span,
        rationale=source.rationale,
        derived_from=tuple(str(ref) for ref in source.derived_from),
        round_id=None if source.round_id is None else str(source.round_id),
        seat_id=source.seat_id,
        question_id=None if source.question_id is None else str(source.question_id),
        chain=tuple(_chain_item_view(item) for item in chain),
        used_by=used_by,
    )


def _suggestions(
    round_: Round, *, catalog: SeatCatalog | None, seat_titles: Mapping[str, str]
) -> tuple[SeatSuggestionView, ...]:
    """开场卡要的就是"提名了谁 + 为什么请它"（F8）。三种情形，优先级从高到低：

    1. 用户已经确认过编制 → 展示他选的那批（可能加了名单外的席位），
       名单里有的仍带上分析者给的理由；
    2. 还没确认、但有分析者的名单 → 展示名单（`source="agent"`）；
    3. 都没有（老轮次、或分析失败停在 started）→ 回落目录匹配，并标 `source="catalog"`。
    """

    if catalog is None:
        return tuple(
            SeatSuggestionView(seat_id=seat_id, title=seat_titles.get(seat_id, seat_id), focus="")
            for seat_id in round_.seats
        )
    by_nomination = {item.seat_id: item for item in round_.nominated_seats}

    def for_seat(seat: SeatDefinition) -> SeatSuggestionView:
        suggestion = _suggestion(seat, user_input=round_.user_input)
        item = by_nomination.get(seat.seat_id)
        if item is None:
            return suggestion
        return replace(suggestion, why=item.why, source="agent")

    if round_.seats:
        return tuple(for_seat(catalog.by_id(seat_id)) for seat_id in round_.seats)
    if round_.nominated_seats:
        return tuple(
            for_seat(catalog.by_id(item.seat_id)) for item in round_.nominated_seats
        )
    # 老轮次：分析那会儿还没有"挑席位"这一步，只好按目录匹配，并如实标出来源
    return tuple(
        replace(_suggestion(seat, user_input=round_.user_input), source="catalog")
        for seat in catalog.nominate(user_input=round_.user_input)
    )


def _available_seats(
    round_: Round, *, catalog: SeatCatalog | None, already: set[str]
) -> tuple[SeatSuggestionView, ...]:
    """目录里还有哪些席位可以自己加（名单与已选之外的全部）。"""

    if catalog is None:
        return ()
    return tuple(
        replace(_suggestion(seat, user_input=round_.user_input), source="catalog")
        for seat in catalog.all()
        if seat.seat_id not in already
    )


def _suggestion(seat: SeatDefinition, *, user_input: str) -> SeatSuggestionView:
    return SeatSuggestionView(
        seat_id=seat.seat_id,
        title=seat.title,
        focus=seat.focus,
        always_on=seat.always_on,
        matched_keywords=tuple(
            keyword for keyword in seat.trigger_keywords if keyword in user_input
        ),
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
    seat_ids = (
        round_.seats
        or tuple(item.seat_id for item in round_.nominated_seats)
        or tuple(
            seat.seat_id
            for seat in (catalog.nominate(user_input=round_.user_input) if catalog else ())
        )
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
            blocking=question.blocking,
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
