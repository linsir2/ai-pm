"""一个用例一个入口。

两条铁律：**模型调用一律在事务外**（`uow` 里不许 `await`，由守卫强制）；
**用例不写领域规则**（规则在聚合与领域服务里，用例只负责编排与事务边界）。
"""

import asyncio
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.schemas import Overflow
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.assembly import (
    NEW_FEATURE_YES,
    Assembly,
    assemble,
    deferred_from_open_items,
    degraded_question_proposals,
    downgraded_question_proposals,
    draft_of,
    open_question_proposals,
    question_key,
    questionnaire_of,
    seat_questions,
    unaccounted_drops,
)
from pmstudio.application.context import (
    answer_eids,
    render_answers,
    render_claims,
    render_document_state,
    render_existing_entries,
    render_reviews,
    seat_entry_handle,
)
from pmstudio.application.deliverable import Deliverable, render_markdown
from pmstudio.application.dto import (
    DiffView,
    EvidenceLookupView,
    ProjectSummaryView,
    ReadinessItemView,
    RollbackResult,
    RoundView,
    VersionListView,
    WorkspaceView,
    diff_view,
    evidence_lookup_view,
    readiness_view,
    round_view,
    used_by_entries,
    version_summary_view,
    workspace_view,
)
from pmstudio.application.errors import (
    ApplicationError,
    ModelCallFailed,
    PacketParseError,
    UnknownEvidenceId,
)
from pmstudio.application.events import (
    DOC_CHANGED,
    GENERATION_FAILED,
    PACKET_EMITTED,
    ROUND_UPDATED,
    Event,
)
from pmstudio.application.narrowing import narrow_seats
from pmstudio.application.ports import (
    Clock,
    EventPublisher,
    IdGenerator,
    ProjectFactory,
    RoundCanceller,
    SeatCatalog,
    SeatDefinition,
    WorkspaceIndex,
)
from pmstudio.application.repair import repair_note
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.authoring.views import DocumentView
from pmstudio.domain.ports import (
    DocumentReader,
    EvidenceReader,
    EvidenceWriter,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.draft import Proposal
from pmstudio.domain.round.questionnaire import (
    MAX_QUESTIONNAIRES,
    MAX_QUESTIONS_PER_BATCH,
    Answer,
    AnswerSet,
    Question,
    QuestionKind,
)
from pmstudio.domain.round.round import DroppedItem, DropSource, Round
from pmstudio.domain.round.step import MAX_SEAT_ROUNDS, Step
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.services.committer import (
    CommitResult,
    ProposalCommitter,
    to_write_instruction,
)
from pmstudio.domain.shared.errors import DomainError, NotAwaitingAnswers, ShapeError
from pmstudio.domain.shared.ids import (
    PREFERENCE_AREA_ID,
    DraftId,
    EntryId,
    EvidenceId,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.sections import FEATURE_HOME, SECTION_IDS
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp


@dataclass(frozen=True, slots=True)
class ClaimEdit:
    """开场卡上的一处纠正：只改标签与去处（内容归文档，卡片不改内容）。"""

    claim_id: str
    label: str
    target: Target


@dataclass(frozen=True, slots=True)
class AnswerInput:
    question_id: str
    text: str = ""
    selected: tuple[str, ...] = ()


class StartRound:
    """建轮 → Analyzer 切分定性归位。

    分两步不是洁癖：模型调用必须能在"轮已经存在"之后失败，
    这样重试（`RetryRound`）才能从 started 或 claims_ready 接着跑。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        uow: UnitOfWork,
        analyzer: ClaimAnalyzer,
        assigner: EvidenceAssigner,
        checker: ReadinessChecker,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
    ) -> None:
        self._documents = documents
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._uow = uow
        self._analyzer = analyzer
        self._assigner = assigner
        self._checker = checker
        self._clock = clock
        self._ids = ids
        self._events = events

    async def create(self, *, project_id: ProjectId, user_input: str) -> RoundId:
        with self._uow:
            self._documents.get(project_id)  # 项目不存在就地失败
            round_id = RoundId(self._ids.new("r"))
            round_ = Round.start(
                round_id=round_id,
                project_id=project_id,
                user_input=user_input,
                now=self._clock.now(),
            )
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)
        return round_id

    async def analyze(self, *, round_id: RoundId) -> None:
        # 读取不开事务：模型调用不能包在事务里，也没必要读到同一快照。
        # 重装期间这一轮没有任何写入，所以文档现状与用户输入读一次就够。
        round_ = self._rounds.get(round_id)
        document = self._documents.get(round_.project_id)
        user_input = round_.user_input
        view = document.view()
        document_state = render_document_state(
            view, readiness=self._checker.check(view)
        )
        repair = ""
        for attempt in range(1, MAX_CLAIMS_ATTEMPTS + 1):
            try:
                analysis = await self._analyzer.analyze(
                    user_input=user_input,
                    document_state=document_state,
                    round_id=round_id,
                    validation_error=repair,
                )
                break
            except PacketParseError as error:
                if attempt == MAX_CLAIMS_ATTEMPTS:
                    # 两次都过不了：判这一轮失败。claims 是一轮的地基——没有主张就没有编号
                    # 与去处，硬编一份出来比失败更糟（GB1）。理由里写清重装过，便于复盘。
                    reason = f"主张两次都过不了校验：{error.message}"
                    fail_round(
                        rounds=self._rounds,
                        round_writer=self._round_writer,
                        uow=self._uow,
                        events=self._events,
                        clock=self._clock,
                        round_id=round_id,
                        reason=reason,
                    )
                    raise PacketParseError(reason, purpose="claims") from error
                # 重装要带上"上一次错在哪"——只给要求，不复制模型写错的那串
                repair = repair_note(error)
            except ApplicationError as error:
                # 网关那类（超时 / 供应商 5xx）：网关自己已经重试 3 次，重装帮不上忙
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=error.message,
                )
                raise
        else:
            raise AssertionError("不可能走到这里")
        with self._uow:
            round_ = self._rounds.get(round_id)
            document = self._documents.get(round_.project_id)
            ledger = self._evidence.get(round_.project_id)
            assigned = self._assigner.assign_claims(
                analysis.claims, ledger=ledger, round_id=round_id, now=self._clock.now()
            )
            round_.attach_claims(assigned)
            # 席位名单跟着主张一起落：它是这一次判断的产物，重试/刷新都该看得见
            round_.nominate(analysis.seats)
            # 被丢掉的提名（目录里没有的席位）留痕：丢人不丢轮，但要说清丢了谁
            round_.note_dropped(analysis.dropped)
            authorize_named_features(round_, assigned, document=document)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


class SubmitOpening:
    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        catalog: SeatCatalog,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._catalog = catalog
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(
        self,
        *,
        round_id: RoundId,
        seats: Sequence[str],
        claim_edits: Sequence[ClaimEdit] = (),
    ) -> None:
        with self._uow:
            round_ = self._rounds.get(round_id)
            # 席位必须在注册表里：不然它会一路走到 `catalog.by_id` 才炸成 500。
            # 这是"你给的 id 不对"（422），不是领域不变量被破坏（409），所以用 ShapeError。
            known = {seat.seat_id for seat in self._catalog.all()}
            unknown = [seat_id for seat_id in seats if seat_id not in known]
            if unknown:
                raise ShapeError(
                    f"这些席位不在注册表里：{'、'.join(sorted(set(unknown)))}",
                    seat_ids=sorted(set(unknown)),
                )
            if claim_edits:
                round_.revise_claims(_apply_claim_edits(round_, claim_edits))
            round_.submit_opening(seats=tuple(seats))
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


class RunSeatRound:
    """并行调各席位 → **每席位一个事务**（交回即落库）+ 汇合点幂等编号。

    模型调用各自独立（一个席位失败不影响别的席位被调用）；崩溃/关页面后重进只补
    `Round.pending_seats` 里缺的席位，已交回的不重跑。任一席位失败 → 整轮 failed，
    文档零写入（"失败必须是失败"），但已交回的意见留着。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        uow: UnitOfWork,
        reviewer: SeatReviewer,
        catalog: SeatCatalog,
        assigner: EvidenceAssigner,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._uow = uow
        self._reviewer = reviewer
        self._catalog = catalog
        self._assigner = assigner
        self._checker = checker
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        # 读取阶段不开事务：模型调用绝不能包在事务里。
        round_ = self._rounds.get(round_id)
        document = self._documents.get(round_.project_id)
        ledger = self._evidence.get(round_.project_id)
        pending = tuple(self._catalog.by_id(seat_id) for seat_id in round_.pending_seats)
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        view = document.view()
        document_state = render_document_state(
            view, readiness=self._checker.check(view)
        )
        claims_text = render_claims(round_.claims)
        answered_text = render_answers(round_, ledger=ledger)
        answer_refs = answer_eids(ledger=ledger)
        previous = render_reviews(round_.seat_reviews, titles=titles, ledger=ledger)
        known_eids = frozenset(evidence.eid for evidence in ledger.evidences())

        results = await asyncio.gather(
            *(
                self._review_one(
                    round_id=round_id,
                    seat=seat,
                    document_state=document_state,
                    claims=claims_text,
                    opinions_so_far=previous,
                    answered=answered_text,
                    known_eids=known_eids,
                    answer_eids=answer_refs,
                )
                for seat in pending
            ),
            return_exceptions=True,
        )
        failures = [
            (seat, result)
            for seat, result in zip(pending, results, strict=True)
            if isinstance(result, BaseException)
        ]
        if failures:
            reason = "；".join(
                f"{seat.seat_id}：{getattr(error, 'message', str(error))}"
                for seat, error in failures
            )
            fail_round(
                rounds=self._rounds,
                round_writer=self._round_writer,
                uow=self._uow,
                events=self._events,
                clock=self._clock,
                round_id=round_id,
                reason=f"席位失败：{reason}",
            )
            first_error = failures[0][1]
            if isinstance(first_error, DomainError):
                # 领域校验不通过也要报成"模型输出不可用"（502），别漏成 409——
                # 这条是真模型冒烟实测出来的：某条意见缺依据时曾以 409 漏出去。
                raise PacketParseError(
                    f"席位失败：{reason}", purpose="seat_review"
                ) from first_error
            raise first_error

        # 汇合点：席位齐了才编号（按席位顺序，且幂等——重进这里不会重复占号）
        round_ = self._rounds.get(round_id)
        if round_.pending_seats:
            _publish(self._events, self._clock, round_)
            return
        with self._uow:
            ledger = self._evidence.get(round_.project_id)
            self._assigner.assign_opinions(
                round_.current_reviews,
                ledger=ledger,
                round_id=round_id,
                now=self._clock.now(),
            )
            self._evidence_writer.save(ledger)
        _publish(self._events, self._clock, round_)

    async def _review_one(
        self,
        *,
        round_id: RoundId,
        seat: SeatDefinition,
        document_state: str,
        claims: str,
        opinions_so_far: str,
        answered: str,
        known_eids: frozenset[EvidenceId],
        answer_eids: dict[str, str] | None = None,
    ) -> None:
        """一个席位：调模型（事务外）→ 交回就落库。`with uow` 里没有 await（守卫会拦）。"""

        validation_error = ""
        for attempt in range(1, MAX_SEAT_ATTEMPTS + 1):
            try:
                outcome = await self._reviewer.review(
                    seat=seat,
                    document_state=document_state,
                    claims=claims,
                    opinions_so_far=opinions_so_far,
                    answered=answered,
                    known_eids=known_eids,
                    answer_eids=answer_eids or {},
                    round_id=round_id,
                    validation_error=validation_error,
                )
            except PacketParseError as error:
                # 解析错也是**可修复的错**：只重跑这个席位（其余席位不动）。
                # 真跑 P7 验证实测：某个席位把 `evidence_refs` 写成 `null`，没重装时整轮判死。
                if attempt == MAX_SEAT_ATTEMPTS:
                    raise
                validation_error = repair_note(error)
                continue
            if outcome.overflow and attempt < MAX_SEAT_ATTEMPTS:
                # **先重装、再截断**（报告 F4）：超限是可修复的错，先让它自己精简一次。
                # 判定点放在用例层：重试策略归这里，席位 agent 只负责"报文 → 领域对象"。
                first = outcome.overflow[0]
                validation_error = repair_note(
                    PacketParseError(
                        "模型返回的报文过不了 schema",
                        purpose="seat_review",
                        overflow=(
                            first.container,
                            first.kept + first.dropped,
                            first.kept,
                        ),
                    )
                )
                continue
            break
        review = outcome.review
        with self._uow:
            round_ = self._rounds.get(round_id)
            round_.attest(review)
            # 被截断掉的意见要留痕（"一条都不许默默丢掉"）——它没进卡片，但要说清为什么。
            round_.note_dropped(
                _truncation_items(
                    review.seat_id,
                    outcome.overflow,
                    pass_index=round_.seat_round_count,
                )
            )
            self._round_writer.save(round_)
            pass_index = round_.seat_round_count
            project_id = round_.project_id
        # 逐席位发事件：闭环区立刻能看到这个席位的发言（编号在汇合点才有，所以 eid 先空着）
        self._events.publish(
            Event(
                name=PACKET_EMITTED,
                project_id=str(project_id),
                round_id=str(round_id),
                payload={
                    "seat_id": review.seat_id,
                    "pass_index": pass_index,
                    "basis": review.basis,
                    "opinions": [
                        {
                            "local_id": opinion.local_id,
                            "form": opinion.form.value,
                            "about": opinion.about,
                            "claim": opinion.claim,
                            "because": opinion.because,
                            "eids": [str(eid) for eid in opinion.evidence_refs],
                            "eid": None,
                        }
                        for opinion in review.opinions
                    ],
                },
                occurred_at=self._clock.now(),
            )
        )


# —— 用例之间共用的小工具 ——


MAX_INTEGRATION_ATTEMPTS = 2

#: claims 也允许"重装一次"：真模型是抖的（同一份输入换个采样就过），让用户手动点重试
#: 是把成本推给人。与整合者同一个口径——**必须带上一次错在哪**（`repair_note`），
#: 否则只是拿同一句提示词再撞一堵墙（`learnings.md` L13）。
MAX_CLAIMS_ATTEMPTS = 2

#: 单个席位也允许"重装一次"：超限（意见太多）与别的报文错都是可修复的，
#: 而整轮重跑要付 4–6 个席位的代价（真跑实测：188 次席位调用里多出一倍是重跑）。
MAX_SEAT_ATTEMPTS = 2

#: 每批从文档 s11 里挑几条最老的未决项来问（跨轮队列的"消化速度"）。
#: 每批 5 题里留 2 个位置还老账；真跑里每轮新增未决 2–4 条，2×2 批 ≈ 打平。
MAX_OPEN_ITEMS_PER_BATCH = 2

def _open_item_defaults(
    round_: Round,
    questions: Sequence[Question],
    *,
    ledger: EvidenceLedger,
    now: datetime,
    reason: str,
) -> dict[QuestionId, EvidenceId]:
    """给"没排上的题"补 system_default 出处（编号在账本里，s11 提案才有东西可引用）。"""

    defaults: dict[QuestionId, EvidenceId] = {}
    for item in questions:
        defaults[item.question_id] = ledger.record(
            kind=EvidenceKind.SYSTEM_DEFAULT,
            text=f"{item.title}（{reason}，未决定）",
            source=EvidenceSource(round_id=round_.round_id, rationale=reason),
            now=now,
        )
    return defaults


def _truncation_items(
    seat_id: str,
    overflow: tuple[Overflow, ...],
    *,
    pass_index: int,
) -> list[DroppedItem]:
    """席位交回超上限被截断的那几条：留痕说清"第几条起没进卡片"。"""

    return [
        DroppedItem(
            handle=f"{seat_id}:O{item.kept + 1}..O{item.kept + item.dropped}",
            reason=f"超出单席位上限 {item.kept} 条，第 {item.kept + 1} 条起未进卡片",
            source=DropSource.ASSEMBLY,
            seat_id=seat_id,
            pass_index=pass_index,
        )
        for item in overflow
    ]


def _overflow_items(
    overflow: tuple[Overflow, ...],
    *,
    pass_index: int,
) -> tuple[DroppedItem, ...]:
    """整合这一趟被截断掉的那几条：留痕说清是"超出单轮上限"。"""

    return tuple(
        DroppedItem(
            handle=f"提案 #{item.kept + 1} 起（共 {item.dropped} 条）",
            reason=f"超出单轮上限 {item.kept} 条，被截断未进卡片（{item.container}）",
            source=DropSource.ASSEMBLY,
            seat_id=None,
            pass_index=pass_index,
        )
        for item in overflow
    )


def _render_authorized_features(round_: Round, *, document: Document) -> str:
    """给整合者看的"本轮已授权的功能"清单（报告 F1 的直接解药）。

    主张点名过的功能在切分阶段就已经授权（`authorize_named_features`），但提示词里原来只说
    "不许新建功能子树"——模型于是整轮都不产功能候选，s5 就空着。这里把已授权的点名列出来：
    **这些功能视为已存在，可以直接写它的功能字段**。
    """

    lines = [
        f"- {slug}（被 {eid} 点名，已授权）：直接写它的功能字段"
        for slug, eid in sorted(round_.authorized_feature_basis.items())
    ]
    existing = sorted(
        str(block.feature_slug)
        for block in document.features()
        if block.feature_slug is not None
    )
    if existing:
        lines.append("- 文档里已有的功能：" + "、".join(existing))
    return "\n".join(lines) or "（这一轮没有点名任何功能）"


def _next_proposal_index(proposals: Sequence[Proposal]) -> int:
    """续编提案编号：**现有最大编号 + 1**，不是 `len + 1`。

    装配层会把去处不合法 / 与既有内容重复的提案**丢掉**，编号因此出现空洞
    （p01、p03…）。真跑实测（P7 验证）：按条数续编会撞上后面那条的编号，
    `DuplicateProposalId` 把整轮打成 500（`p15` 重复）。
    """

    numbers = [
        int(text)
        for text in (str(item.proposal_id)[1:] for item in proposals)
        if text.isdigit()
    ]
    return max(numbers, default=0) + 1


def _stop_asking_reason(round_: Round) -> str | None:
    """该不该停止追问？两个闸门，谁先到算谁，理由写进证据与缺口行里。

    - **问卷已两批**（`04-未决.md` C6）：这是用户最先感受到的那条——别再没完没了地问；
    - **席位轮到上限**（`02` §7）：兜底，正常路径下问卷闸门会先响。
    """

    if len(round_.answer_sets) >= MAX_QUESTIONNAIRES:
        return f"问卷已满 {MAX_QUESTIONNAIRES} 批"
    if round_.seat_round_count >= MAX_SEAT_ROUNDS:
        return "已到席位轮上限"
    return None


class IntegrateRound:
    """整合 → 干跑 →（失败就重装一次）→ 一个事务里出问卷或草案。"""

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        uow: UnitOfWork,
        integrator: Integrator,
        catalog: SeatCatalog,
        checker: ReadinessChecker,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._uow = uow
        self._integrator = integrator
        self._catalog = catalog
        self._checker = checker
        self._clock = clock
        self._ids = ids
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        assembled, gaps, carried = await self._integrate_with_repair(round_id=round_id)

        with self._uow:
            round_ = self._rounds.get(round_id)
            ledger = self._evidence.get(round_.project_id)
            now = self._clock.now()
            # 只有**真被问出去**的那几条才从轮内队列出队（carried 里还混着 s11 来的题）
            asked = {question_key(item) for item in assembled.questions}
            round_.take_deferred(
                sum(1 for item in carried if question_key(item) in asked)
            )
            round_.defer(assembled.deferred)
            proposals = list(assembled.proposals)
            degraded = 0
            if assembled.degrade_reason is not None:
                degraded_proposals = degraded_question_proposals(
                    round_.current_reviews,
                    ledger=ledger,
                    round_id=round_id,
                    now=now,
                    start_index=_next_proposal_index(proposals),
                    reason=assembled.degrade_reason,
                )
                degraded = len(degraded_proposals)
                proposals += list(degraded_proposals)
            system_defaults = _system_defaults(round_, ledger=ledger, now=now)
            proposals += list(
                open_question_proposals(
                    round_,
                    system_defaults=system_defaults,
                    start_index=_next_proposal_index(proposals),
                )
            )
            questions = assembled.questions
            gaps = list(gaps)
            if assembled.degrade_reason is not None:
                gaps.append(
                    f"{assembled.degrade_reason}：{degraded} 条席位疑问未归并，"
                    "已原样落到 s11（未改一个字）"
                )
            stop_reason = _stop_asking_reason(round_)
            if questions and stop_reason is not None:
                # 不再追问：题目降级成 s11 开放问题，直接出草案（02 §7 / 04-未决 C6）
                for question in questions:
                    system_defaults[question.question_id] = ledger.record(
                        kind=EvidenceKind.SYSTEM_DEFAULT,
                        text=f"{question.title}（{stop_reason}，未决定）",
                        source=EvidenceSource(
                            round_id=round_id,
                            rationale=f"{stop_reason}，不再追问，转为开放性记录",
                        ),
                        now=now,
                    )
                proposals += list(
                    downgraded_question_proposals(
                        questions,
                        system_defaults=system_defaults,
                        start_index=_next_proposal_index(proposals),
                        round_id=round_id,
                        reason=stop_reason,
                    )
                )
                gaps += [
                    f"未决（{stop_reason}，不再追问）：{item.title}" for item in questions
                ]
                questions = ()
            if not questions:
                # 出草案这一支：轮内队列**一律清空落 s11**（队列只在轮内有活路）
                leftover = list(round_.clear_deferred())
                if leftover:
                    defaults = _open_item_defaults(
                        round_,
                        leftover,
                        ledger=ledger,
                        now=now,
                        reason="本批没排上",
                    )
                    proposals += list(
                        downgraded_question_proposals(
                            leftover,
                            system_defaults=defaults,
                            start_index=_next_proposal_index(proposals),
                            round_id=round_id,
                            reason="本批没排上",
                        )
                    )
                    gaps += [f"未决（本批没排上）：{item.title}" for item in leftover]
            dropped = _dropped_items_for(
                round_,
                assembled=assembled,
                proposals=proposals,
                produced_questions=assembled.questions,
                ledger=ledger,
            )
            questionnaire = questionnaire_of(
                round_id,
                FormId(self._ids.new("f")),
                questions,
                assembled.list_items,
            )
            if questionnaire is not None:
                round_.integrate(questionnaire=questionnaire, dropped=dropped)
            else:
                draft = draft_of(round_id, DraftId(self._ids.new("d")), proposals, gaps)
                if draft is None:
                    raise ShapeError("整合既没有给出题目也没有给出提案")
                round_.integrate(draft=draft, dropped=dropped)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)

    async def _integrate_with_repair(self, *, round_id: RoundId):  # type: ignore[no-untyped-def]
        validation_error = ""
        carried: tuple[Question, ...] = ()
        carried_from_queue: tuple[Question, ...] = ()
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        for attempt in range(1, MAX_INTEGRATION_ATTEMPTS + 1):
            round_ = self._rounds.get(round_id)
            document = self._documents.get(round_.project_id)
            ledger = self._evidence.get(round_.project_id)
            # carried 只算一次：重装时模型看到的是同一份"要优先问的题"（队列在事务里才消费）
            if attempt == 1:
                # 跨轮队列 = 文档 s11 里最老的 N 条未决条目；轮内队列 = 本批没问上的题
                carried_from_queue = round_.peek_deferred(MAX_QUESTIONS_PER_BATCH)
                carried = (
                    deferred_from_open_items(
                        document, ledger=ledger, limit=MAX_OPEN_ITEMS_PER_BATCH
                    )
                    + carried_from_queue
                )
            answered, _, skipped = render_answers(
                round_, ledger=self._evidence.get(round_.project_id)
            ).partition("--")
            view = document.view()
            try:
                authorized_features = _render_authorized_features(
                    round_, document=document
                )
                outcome = await self._integrator.integrate(
                    document_state=render_document_state(
                        view, readiness=self._checker.check(view)
                    ),
                    claims=render_claims(round_.claims),
                    opinions=render_reviews(
                        round_.current_reviews, titles=titles, ledger=ledger
                    ),
                    answered=answered,
                    skipped=skipped,
                    entry_ids=render_existing_entries(document),
                    validation_error=validation_error,
                    round_id=round_id,
                    authorized_features=authorized_features,
                )
                if outcome.overflow and attempt < MAX_INTEGRATION_ATTEMPTS:
                    # 超限是可修复的错：先让它自己精简一次，第二次才截断（报告 F2/F11）。
                    first = outcome.overflow[0]
                    validation_error = repair_note(
                        PacketParseError(
                            "模型返回的报文过不了 schema",
                            purpose="integration",
                            overflow=(
                                first.container,
                                first.kept + first.dropped,
                                first.kept,
                            ),
                        )
                    )
                    continue
                assembled = assemble(
                    outcome.packet,
                    round_=round_,
                    document=document,
                    ledger=ledger,
                    carried=carried,
                )
                # 截断掉的那几条要留痕 + 在草案卡缺口行里说清（"不静默丢"）。
                assembled = replace(
                    assembled,
                    dropped=assembled.dropped
                    + _overflow_items(outcome.overflow, pass_index=round_.seat_round_count),
                )
                gaps = dry_run(
                    document, assembled.proposals, round_=round_, now=self._clock.now()
                )
                if outcome.overflow:
                    count = sum(item.dropped for item in outcome.overflow)
                    gaps = gaps + (
                        f"本轮有 {count} 条内容因单轮上限未进卡片（可在下一轮继续提）",
                    )
            except ModelCallFailed as error:
                # 网关类失败：网关自己已经重试 3 次 + 退避，报文重装解决不了它 → 立刻判死
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=error.message,
                )
                raise
            except (PacketParseError, DomainError) as error:
                # 形状错 / 结构错 / 内容错**都是可修复的错**：给第二次机会。
                # 以前 `except ApplicationError` 把解析错和网关错一起判死，于是
                # `repair_note` 对整合从来没生效过（真跑留样 15 与 16 差 55 秒就是证据）。
                message = getattr(error, "message", str(error))
                if attempt == MAX_INTEGRATION_ATTEMPTS:
                    reason = f"整合两次都过不了校验：{message}"
                    if seat_questions(round_.current_reviews):
                        # 有可救的东西就别判死：席位的疑问原样落成 s11 开放问题，
                        # 走草案卡让用户自己决定（04-未决 C9；与 C6/C7 的降级同一套语义）。
                        return (
                            Assembly(
                                questions=(),
                                list_items=(),
                                proposals=(),
                                degrade_reason=reason,
                            ),
                            (),
                            carried_from_queue,
                        )
                    fail_round(
                        rounds=self._rounds,
                        round_writer=self._round_writer,
                        uow=self._uow,
                        events=self._events,
                        clock=self._clock,
                        round_id=round_id,
                        reason=reason,
                    )
                    raise PacketParseError(reason, purpose="integration") from error
                validation_error = (
                    # 解析错：`repair_note` 有结构化细节（形状 / 超限 / 字段路径）要说
                    repair_note(error)
                    if isinstance(error, PacketParseError)
                    # 领域错：它自己的话术更具体（如"选择题至少两个选项"），别用通用话盖掉
                    else message
                )
                continue
            except ApplicationError as error:
                # 兜底：认不出的应用层错误照旧"失败必须是失败"（与改动前的行为一致）
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=error.message,
                )
                raise
            return assembled, gaps, carried_from_queue
        raise AssertionError("不可能走到这里")


def _dropped_items_for(
    round_: Round,
    *,
    assembled: Assembly,
    proposals: Sequence[Proposal],
    produced_questions: Sequence[Question],
    ledger: EvidenceLedger,
) -> tuple[DroppedItem, ...]:
    """这一趟的留痕：三个来源合成一条通道（`04-未决.md` C5 后半）。

    装配层不假设"模型会老实交代"——它自己核一遍：哪些意见真的被用到了、
    哪些只是被申报为丢弃、剩下的就是"没人说过它去哪了"（`unaccounted`）。
    问卷路、草案路、降级路都走这一个出口，所以留痕不会只在某一条路上活着。
    """

    accounted_handles = frozenset(assembled.accounted_handles)
    if assembled.degrade_reason is not None:
        # 整合失败时，这一趟的疑问会被原样落进 s11：算交代过；没落地的判断仍会报
        accounted_handles |= frozenset(
            seat_entry_handle(seat_id, opinion.local_id)
            for seat_id, opinion in seat_questions(round_.current_reviews)
        )
    accounted_eids = frozenset(
        eid for proposal in proposals for eid in proposal.origin.refs
    ) | frozenset(
        eid for question in produced_questions for eid in question.evidence_refs
    )
    return assembled.dropped + unaccounted_drops(
        round_.current_reviews,
        ledger=ledger,
        accounted_handles=accounted_handles,
        accounted_eids=accounted_eids,
        declared=frozenset(
            item.handle
            for item in assembled.dropped
            if item.source is DropSource.INTEGRATOR
        ),
    )


class SubmitAnswers:
    """答案落账本（card_answer + question_id）→ 回到席位轮。

    答案**不是**被消费掉就完：它进账本，图接着回到席位轮重新判断（`02` §2 要点 2）。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        catalog: SeatCatalog,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._catalog = catalog
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(
        self,
        *,
        round_id: RoundId,
        form_id: str,
        answers: Sequence[AnswerInput],
        skipped: Sequence[str] = (),
    ) -> None:
        with self._uow:
            round_ = self._rounds.get(round_id)
            ledger = self._evidence.get(round_.project_id)
            questionnaire = round_.questionnaire
            if questionnaire is None:
                raise NotAwaitingAnswers(step=round_.step)
            by_id = {str(question.question_id): question for question in questionnaire.questions}
            now = self._clock.now()
            answer_set = AnswerSet(
                form_id=FormId(form_id),
                answers=tuple(
                    Answer(
                        question_id=QuestionId(item.question_id),
                        text=item.text,
                        selected=tuple(item.selected),
                    )
                    for item in answers
                ),
                answered_at=now,
                skipped=tuple(QuestionId(item) for item in skipped),
            )
            # C8 缩编：只请"这批题跟它有关"的席位。名单要基于**用户编制**算，
            # 结果也一定在编制之内（领域还会再校验一次）。
            touched = tuple(item.question_id for item in answer_set.answers) + tuple(
                answer_set.skipped
            )
            active_seats = narrow_seats(
                seats=round_.seats,
                catalog=self._catalog,
                questionnaire=questionnaire,
                touched=touched,
            )
            round_.submit_answers(answer_set, active_seats=active_seats)  # 领域把关（R6）
            for answer in answer_set.answers:
                question = by_id[str(answer.question_id)]
                eid = ledger.record(
                    kind=EvidenceKind.CARD_ANSWER,
                    text=_answer_text(question, answer),
                    source=EvidenceSource(round_id=round_id, question_id=answer.question_id),
                    now=now,
                )
                slug = question.target.feature_slug
                if (
                    question.kind is QuestionKind.DECISION
                    and slug is not None
                    and NEW_FEATURE_YES in answer.selected
                    and slug not in round_.authorized_new_features
                ):
                    round_.authorize_new_feature(slug, basis=eid)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


class ConfirmDraft:
    """唯一写入路径的入口。编排层、席位、整合者都拿不到 `DocumentWriter`。"""

    def __init__(
        self,
        *,
        committer: ProposalCommitter,
        rounds: RoundReader,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._committer = committer
        self._rounds = rounds
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId, accepted: Sequence[str]) -> CommitResult:
        result = self._committer.commit(
            round_id=round_id,
            accepted=tuple(ProposalId(item) for item in accepted),
            now=self._clock.now(),
        )
        round_ = self._rounds.get(round_id)
        self._events.publish(
            Event(
                name=DOC_CHANGED,
                project_id=str(round_.project_id),
                round_id=str(round_id),
                payload={
                    "version_no": result.version.version_no,
                    "written_block_ids": [
                        str(block_id) for block_id in result.written_block_ids
                    ],
                    "summary": result.version.summary,
                },
                occurred_at=self._clock.now(),
            )
        )
        _publish(self._events, self._clock, round_)
        return result


class RetryRound:
    """**继续这一轮**：失败就先 `resume`；没失败就直接接着跑。

    为什么允许对"没失败"的轮调用：关页面 / 进程崩了以后，轮停在 `seats_running`，
    它不是 failed 状态，但同样需要接着跑（计划 §3.7）。两种场面收在一个动作里，
    界面就只有一个"继续"按钮。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        with self._uow:
            round_ = self._rounds.get(round_id)
            if round_.step is Step.FAILED:
                round_.resume()
                self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


MANUAL_PREFERENCE_BLOCK = str(PREFERENCE_AREA_ID)

EDIT_SUMMARY = {
    WriteOp.ADD: "加一条",
    WriteOp.REPLACE: "改一条",
    WriteOp.REMOVE: "删一条",
}


@dataclass(frozen=True, slots=True)
class EditResult:
    version_no: int
    written_block_ids: tuple[str, ...]
    entry_id: str
    document: DocumentView


class EditEntry:
    """用户直接编辑文档（F17）：改 / 加 / 删条目，一次改动一版（`trigger=manual`）。

    它和 AI 写入只差两点：

    1. **来源是 `manual`**：手改内容也进账本，有自己的编号（`04-未决.md` D1 的建议版），
       所以文档里每条内容都查得到"这句话是谁说的"。
    2. **改过的块打上 `manually_edited`**：之后 AI 的改写与删除会被 `BlockManuallyEdited` 拦下来，
       不会把你亲手写的东西悄悄覆盖掉——这就是"卡片改的与文档改的谁优先"的答案。

    写入仍然只有一条路：这里只负责准备指令与编号，落盘交给 `ProposalCommitter.apply_manual`。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        committer: ProposalCommitter,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
    ) -> None:
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._committer = committer
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._events = events

    async def __call__(
        self,
        *,
        project_id: ProjectId,
        op: WriteOp,
        entry_id: str | None = None,
        block_id: str | None = None,
        text: str = "",
    ) -> EditResult:
        with self._uow:
            document: Document = self._documents.get(project_id)
            ledger = self._evidence.get(project_id)
            now = self._clock.now()
            target, existing = _resolve_manual_target(
                document, op=op, entry_id=entry_id, block_id=block_id
            )
            # 手改也要有出处：编号先发，内容才落得进去（GB1）。
            manual_eid = ledger.record(
                kind=EvidenceKind.MANUAL,
                text=_manual_note(
                    op,
                    existing_text=None if existing is None else existing.text,
                    text=text,
                ),
                source=EvidenceSource(),
                now=now,
            )
            if op is WriteOp.ADD:
                final_entry_id = EntryId(self._ids.new("entry"))
                refs = (manual_eid,)
                instruction_text = text
            else:
                assert existing is not None  # _resolve_manual_target 保证
                final_entry_id = existing.entry_id
                # 保留原来的出处，再加上"你自己改的"这一条：新旧都能查。
                refs = tuple(existing.refs) + (manual_eid,)
                # 删除也要有一条非空文本（写入指令的硬要求）；记录被删掉的是什么。
                instruction_text = text if text.strip() else existing.text

            instruction = WriteInstruction(
                instruction_id=f"manual-{manual_eid}",
                entry_id=final_entry_id,
                op=op,
                target=target,
                text=instruction_text,
                refs=refs,
                kind=_manual_kind(document, block_id=block_id) if op is WriteOp.ADD else None,
                manual=True,
            )
            result: CommitResult = self._committer.apply_manual(
                project_id=project_id,
                instruction=instruction,
                now=now,
                summary=f"手工{EDIT_SUMMARY[op]}：{text.strip() or '（删除）'}"[:120],
            )
            self._evidence_writer.save(ledger)
            view = self._documents.get(project_id).view()
        self._events.publish(
            Event(
                name=DOC_CHANGED,
                project_id=str(project_id),
                payload={
                    "version_no": result.version.version_no,
                    "written_block_ids": [str(item) for item in result.written_block_ids],
                    "summary": result.version.summary,
                },
                occurred_at=now,
            )
        )
        return EditResult(
            version_no=result.version.version_no,
            written_block_ids=tuple(str(item) for item in result.written_block_ids),
            entry_id=str(final_entry_id),
            document=view,
        )


def _resolve_manual_target(
    document: Document, *, op: WriteOp, entry_id: str | None, block_id: str | None
) -> tuple[Target, object | None]:
    """手改要落到哪个 target、动的是哪一条。

    ADD 的 `block_id` 有三种给法：`preference-area`（偏好区）、某个块的 id（往这块加）、
    某个章节 id（`s1`…`s13`：这一章还没有块，那就新开一段）。
    """

    if op is WriteOp.ADD:
        if block_id is None:
            raise ShapeError("加一条要先说加到哪一块")
        if block_id == MANUAL_PREFERENCE_BLOCK:
            return Target.preference(), None
        if block_id in SECTION_IDS:
            return Target.body(block_id), None
        block = next((item for item in document.blocks if str(item.block_id) == block_id), None)
        if block is None:
            raise ShapeError(f"找不到块 {block_id}", block_id=block_id)
        return _target_of_block(block), None

    if entry_id is None:
        raise ShapeError("改或删要先说改哪一条")
    for entry in document.preference_entries:
        if str(entry.entry_id) == entry_id:
            return Target.preference(), entry
    for block in document.blocks:
        entry = block.entry(EntryId(entry_id))
        if entry is not None:
            return _target_of_block(block), entry
    raise ShapeError(f"找不到条目 {entry_id}", entry_id=entry_id)


def _target_of_block(block) -> Target:  # type: ignore[no-untyped-def]
    if block.field is not None and block.feature_slug is not None:
        return Target.feature_field(block.feature_slug, block.field)
    if block.field is not None and block.parent_id is not None:
        # 章节槽（s8 的六块）：手改要落回那个槽，别退化成"整章"
        return Target.body(str(block.parent_id), field=block.field)
    if block.kind is BlockKind.FEATURE and block.feature_slug is not None:
        return Target.body(FEATURE_HOME, feature_slug=block.feature_slug)
    section_id = str(block.parent_id or block.block_id)
    return Target.body(section_id)


def _manual_kind(document: Document, *, block_id: str | None) -> BlockKind | None:
    """往章节级块里加内容时，跟着被点的块保持同一种 kind（清单还是清单）。"""

    # 偏好区不是块；章节 id 也不能照抄——章节根块的 kind 是 section，
    # 抄下来会造出第二个章节根（D1：13 章必须不多不少，这条真踩过）。
    if block_id is None or block_id == MANUAL_PREFERENCE_BLOCK or block_id in SECTION_IDS:
        return None
    block = next((item for item in document.blocks if str(item.block_id) == block_id), None)
    return None if block is None else block.kind


def _manual_note(op: WriteOp, *, existing_text: str | None, text: str) -> str:
    if op is WriteOp.REMOVE:
        return f"手工删除：{existing_text or ''}".strip()
    if op is WriteOp.REPLACE:
        return f"手工改写：{text.strip()}"
    return f"手工新增：{text.strip()}"


class CreateProject:
    """建项目 = 建一份空骨架文档（计划 §3.5：v1 不建 Project 聚合）。"""

    def __init__(
        self,
        *,
        factory: ProjectFactory,
        index: WorkspaceIndex,
        clock: Clock,
        ids: IdGenerator,
    ) -> None:
        self._factory = factory
        self._index = index
        self._clock = clock
        self._ids = ids

    async def __call__(self, *, title: str) -> ProjectSummaryView:
        project_id = ProjectId(self._ids.new("p"))
        self._factory.create(project_id=project_id, title=title, now=self._clock.now())
        return self._index.summary(project_id)


class GetWorkspace:
    def __init__(
        self,
        *,
        documents: DocumentReader,
        rounds: RoundReader,
        evidence: EvidenceReader,
        index: WorkspaceIndex,
        catalog: SeatCatalog,
        checker: ReadinessChecker,
    ) -> None:
        self._documents = documents
        self._rounds = rounds
        self._evidence = evidence
        self._index = index
        self._catalog = catalog
        self._checker = checker

    async def __call__(self, *, project_id: ProjectId) -> WorkspaceView:
        document = self._documents.get(project_id)
        round_id = self._index.latest_round_id(project_id)
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        round_ = None if round_id is None else self._rounds.get(round_id)
        ledger = None if round_ is None else self._evidence.get(project_id)
        view = document.view()
        return workspace_view(
            project=self._index.summary(project_id),
            document=view,
            round_=round_,
            seat_titles=titles,
            catalog=self._catalog,
            ledger=ledger,
            readiness=self._checker.check(view),
        )


class GetReadiness:
    """只读：这份文档按合格标准还差什么（`01-规格.md` §4/§7）。"""

    def __init__(self, *, documents: DocumentReader, checker: ReadinessChecker) -> None:
        self._documents = documents
        self._checker = checker

    async def __call__(self, *, project_id: ProjectId) -> tuple[ReadinessItemView, ...]:
        view = self._documents.get(project_id).view()
        return tuple(readiness_view(item) for item in self._checker.check(view))


class ExportMarkdown:
    """交付物：纯读 + 纯渲染。它是"能不能交出去"这句话的落地形态（`01` §7.3）。"""

    def __init__(
        self,
        *,
        documents: DocumentReader,
        evidence: EvidenceReader,
        checker: ReadinessChecker,
        clock: Clock,
    ) -> None:
        self._documents = documents
        self._evidence = evidence
        self._checker = checker
        self._clock = clock

    async def __call__(self, *, project_id: ProjectId) -> Deliverable:
        document = self._documents.get(project_id)
        view = document.view()
        return render_markdown(
            view=view,
            readiness=self._checker.check(view),
            versions=document.versions,
            ledger=self._evidence.get(project_id),
            exported_at=self._clock.now(),
        )


class ListProjects:
    def __init__(self, *, index: WorkspaceIndex) -> None:
        self._index = index

    async def __call__(self) -> tuple[ProjectSummaryView, ...]:
        return self._index.list_summaries()


class GetVersions:
    """列版本。读聚合里的 `versions`（快照也读出来；单项目几百版以内没问题）。"""

    def __init__(self, *, documents: DocumentReader) -> None:
        self._documents = documents

    async def __call__(self, *, project_id: ProjectId) -> VersionListView:
        document: Document = self._documents.get(project_id)
        return VersionListView(
            versions=tuple(version_summary_view(item) for item in document.versions),
            current=document.current_version.version_no,
        )


class GetVersionDiff:
    """两个版本的差异：纯函数算（`domain/authoring/diff.py`），这一层只负责取两个版本。"""

    def __init__(self, *, documents: DocumentReader) -> None:
        self._documents = documents

    async def __call__(
        self, *, project_id: ProjectId, from_version_no: int, to_version_no: int
    ) -> DiffView:
        document: Document = self._documents.get(project_id)
        # 版本号不存在 → UnknownVersion → 404（api/errors.py 的映射）
        before = document.version(from_version_no)
        after = document.version(to_version_no)
        return diff_view(before, after)


class RollbackVersion:
    """回到某一版：写入只经过 `ProposalCommitter`，写完推 `doc.changed` 让界面重读。"""

    def __init__(
        self,
        *,
        committer: ProposalCommitter,
        documents: DocumentReader,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._committer = committer
        self._documents = documents
        self._clock = clock
        self._events = events

    async def __call__(self, *, project_id: ProjectId, version_no: int) -> RollbackResult:
        result: CommitResult = self._committer.rollback_to(
            project_id=project_id,
            version_no=version_no,
            now=self._clock.now(),
            summary=f"回到 v{version_no}",
        )
        view = self._documents.get(project_id).view()
        self._events.publish(
            Event(
                name=DOC_CHANGED,
                project_id=str(project_id),
                payload={
                    "version_no": result.version.version_no,
                    "written_block_ids": [str(item) for item in result.written_block_ids],
                    "summary": result.version.summary,
                },
                occurred_at=self._clock.now(),
            )
        )
        return RollbackResult(version_no=result.version.version_no, document=view)


class GetEvidence:
    """按编号查处方：原话、来路（哪一轮 / 哪个席位 / 哪道题）、链条、以及"现在被谁用着"。

    只读：账本 + 文档投影。单项目几千条以内是 O(n) 扫描，不建索引（索引层是另一件事）。
    """

    def __init__(self, *, documents: DocumentReader, evidence: EvidenceReader) -> None:
        self._documents = documents
        self._evidence = evidence

    async def __call__(self, *, project_id: ProjectId, eid: str) -> EvidenceLookupView:
        ledger = self._evidence.get(project_id)
        evidence = next((item for item in ledger.evidences() if str(item.eid) == eid), None)
        if evidence is None:
            raise UnknownEvidenceId(eid)
        view = self._documents.get(project_id).view()
        return evidence_lookup_view(
            evidence,
            chain=ledger.chain(evidence.eid),
            used_by=used_by_entries(view, evidence.eid),
        )


class AbandonRound:
    """放弃这一轮（`01` F15）：先写状态，再取消正在跑的任务。

    顺序不能反：先取消的话，取消回来的那一刻状态还是"在跑"，界面会闪一下旧状态；
    而且**写法只有这一处**——取消本身不写任何业务状态，它只让模型调用停下来。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
        canceller: RoundCanceller,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._uow = uow
        self._clock = clock
        self._events = events
        self._canceller = canceller

    async def __call__(
        self, *, round_id: RoundId, reason: str = "用户放弃了这一轮"
    ) -> RoundView:
        with self._uow:
            round_ = self._rounds.get(round_id)
            round_.abandon(reason)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)

        # 写完了再取消：取消会把那个任务炸在 await 点，而它不会碰业务状态。
        await self._canceller.cancel(round_id)
        return round_view(self._rounds.get(round_id))


class GetRound:
    def __init__(
        self, *, rounds: RoundReader, evidence: EvidenceReader, catalog: SeatCatalog
    ) -> None:
        self._rounds = rounds
        self._evidence = evidence
        self._catalog = catalog

    async def __call__(self, *, round_id: RoundId) -> RoundView:
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        round_ = self._rounds.get(round_id)
        return round_view(
            round_,
            seat_titles=titles,
            catalog=self._catalog,
            ledger=self._evidence.get(round_.project_id),
        )


class GetDocument:
    def __init__(self, *, documents: DocumentReader) -> None:
        self._documents = documents

    async def __call__(self, *, project_id: ProjectId) -> DocumentView:
        return self._documents.get(project_id).view()


def dry_run(
    document: Document,
    proposals: Sequence[Proposal],
    *,
    round_: Round,
    now: datetime,
) -> tuple[str, ...]:
    """在内存深拷贝上跑一次 apply：预校验 + 算出"写入后还剩哪些必填缺口"。

    真是唯一真相源：干跑用的是 `Document.apply` 本尊，不是第二套规则。
    """

    probe = deepcopy(document)
    if proposals:
        probe.apply(
            tuple(to_write_instruction(proposal) for proposal in proposals),
            now=now,
            summary="干跑：不落库",
            authorized_new_features=round_.authorized_new_features,
        )
    return tuple(f"{gap.feature_slug}·{gap.title} 仍然为空" for gap in probe.gaps())


def _system_defaults(
    round_: Round, *, ledger: EvidenceLedger, now: datetime
) -> dict[QuestionId, EvidenceId]:
    """给跳过的题补一条 system_default 出处（编号在账本里，proposal 才有东西可引用）。"""

    questionnaire = round_.questionnaire
    if questionnaire is None:
        return {}
    by_id = {question.question_id: question for question in questionnaire.questions}
    defaults: dict[QuestionId, EvidenceId] = {}
    for answer_set in round_.answer_sets:
        for question_id in answer_set.skipped:
            question = by_id.get(question_id)
            if question is None or question_id in defaults:
                continue
            defaults[question_id] = ledger.record(
                kind=EvidenceKind.SYSTEM_DEFAULT,
                text=f"{question.title}（跳过，未决定）",
                source=EvidenceSource(
                    round_id=round_.round_id,
                    rationale="跳过的题按题面写明的后果落成开放问题",
                ),
                now=now,
            )
    return defaults


def _answer_text(question: Question, answer: Answer) -> str:
    chosen = "、".join(
        option.text for option in question.options if option.option_id in answer.selected
    )
    head = f"{question.title} → {chosen or answer.text.strip() or '（空答）'}"
    note = answer.text.strip()
    return f"{head}；备注：{note}" if chosen and note else head


def authorize_named_features(
    round_: Round, claims: Sequence[Claim], *, document: Document
) -> None:
    """用户原话点名的功能才算授权（D3 的第一条路径，见计划 §3.4）。"""

    existing = {block.feature_slug for block in document.features()}
    for claim in claims:
        slug = claim.target.feature_slug
        if slug is None or slug in existing or slug in round_.authorized_new_features:
            continue
        if claim.eid is None:
            raise ShapeError("主张还没有编号，谈不上授权", claim_id=claim.claim_id)
        round_.authorize_new_feature(slug, basis=claim.eid)


def _apply_claim_edits(round_: Round, edits: Sequence[ClaimEdit]) -> tuple[Claim, ...]:
    edits_by_id = {edit.claim_id: edit for edit in edits}
    claims: list[Claim] = []
    for claim in round_.claims:
        edit = edits_by_id.pop(claim.claim_id, None)
        if edit is None:
            claims.append(claim)
            continue
        try:
            label = ClaimLabel(edit.label)
        except ValueError as error:
            raise ShapeError(f"未知标签：{edit.label}", claim_id=claim.claim_id) from error
        claims.append(replace(claim, label=label, target=edit.target))
    if edits_by_id:
        raise ShapeError("纠正里出现了不存在的 claim_id", claim_ids=sorted(edits_by_id))
    return tuple(claims)


def _publish(
    events: EventPublisher,
    clock: Clock,
    round_: Round,
    *,
    name: str = ROUND_UPDATED,
    payload: dict[str, object] | None = None,
) -> None:
    events.publish(
        Event(
            name=name,
            project_id=str(round_.project_id),
            round_id=str(round_.round_id),
            payload=payload
            or {
                "step": str(round_.step),
                "waiting_on": str(round_.waiting_on),
                "seat_round_count": round_.seat_round_count,
            },
            occurred_at=clock.now(),
        )
    )


def fail_round(
    *,
    rounds: RoundReader,
    round_writer: RoundWriter,
    uow: UnitOfWork,
    events: EventPublisher,
    clock: Clock,
    round_id: RoundId,
    reason: str,
) -> None:
    """整轮失败：记原因、发 generation.failed。**不改文档、不落半截意见。**"""

    with uow:
        round_ = rounds.get(round_id)
        round_.fail(reason)
        round_writer.save(round_)
    events.publish(
        Event(
            name=GENERATION_FAILED,
            project_id=str(round_.project_id),
            round_id=str(round_id),
            payload={"reason": reason, "at_step": str(round_.failure.at_step)},
            occurred_at=clock.now(),
        )
    )
