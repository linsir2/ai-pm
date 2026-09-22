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
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.assembly import (
    NEW_FEATURE_YES,
    assemble,
    draft_of,
    open_question_proposals,
    questionnaire_of,
)
from pmstudio.application.context import (
    render_answers,
    render_claims,
    render_document,
    render_existing_entries,
    render_reviews,
)
from pmstudio.application.dto import (
    ProjectSummaryView,
    RoundView,
    WorkspaceView,
    round_view,
    workspace_view,
)
from pmstudio.application.errors import ApplicationError, PacketParseError
from pmstudio.application.events import (
    DOC_CHANGED,
    GENERATION_FAILED,
    PACKET_EMITTED,
    ROUND_UPDATED,
    Event,
)
from pmstudio.application.ports import (
    Clock,
    EventPublisher,
    IdGenerator,
    ProjectFactory,
    SeatCatalog,
    SeatDefinition,
    WorkspaceIndex,
)
from pmstudio.domain.authoring.document import Document
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
    Answer,
    AnswerSet,
    Question,
    QuestionKind,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.services.committer import (
    CommitResult,
    ProposalCommitter,
    to_write_instruction,
)
from pmstudio.domain.shared.errors import DomainError, NotAwaitingAnswers, ShapeError
from pmstudio.domain.shared.ids import (
    DraftId,
    EvidenceId,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target


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
        # 读取不开事务：模型调用不能包在事务里，也没必要读到同一快照
        round_ = self._rounds.get(round_id)
        document = self._documents.get(round_.project_id)
        user_input = round_.user_input
        document_state = render_document(document.view())
        try:
            claims = await self._analyzer.analyze(
                user_input=user_input, document_state=document_state
            )
        except ApplicationError as error:
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
        with self._uow:
            round_ = self._rounds.get(round_id)
            document = self._documents.get(round_.project_id)
            ledger = self._evidence.get(round_.project_id)
            assigned = self._assigner.assign_claims(
                claims, ledger=ledger, round_id=round_id, now=self._clock.now()
            )
            round_.attach_claims(assigned)
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
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
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
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        # 读取阶段不开事务：模型调用绝不能包在事务里。
        round_ = self._rounds.get(round_id)
        document = self._documents.get(round_.project_id)
        ledger = self._evidence.get(round_.project_id)
        pending = tuple(self._catalog.by_id(seat_id) for seat_id in round_.pending_seats)
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        document_state = render_document(document.view())
        claims_text = render_claims(round_.claims)
        answered_text = render_answers(round_)
        previous = render_reviews(round_.seat_reviews, titles=titles)
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
    ) -> None:
        """一个席位：调模型（事务外）→ 交回就落库。`with uow` 里没有 await（守卫会拦）。"""

        review = await self._reviewer.review(
            seat=seat,
            document_state=document_state,
            claims=claims,
            opinions_so_far=opinions_so_far,
            answered=answered,
            known_eids=known_eids,
        )
        with self._uow:
            round_ = self._rounds.get(round_id)
            round_.attest(review)
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
        self._clock = clock
        self._ids = ids
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        assembled, gaps = await self._integrate_with_repair(round_id=round_id)

        with self._uow:
            round_ = self._rounds.get(round_id)
            ledger = self._evidence.get(round_.project_id)
            now = self._clock.now()
            proposals = list(assembled.proposals)
            system_defaults = _system_defaults(round_, ledger=ledger, now=now)
            proposals += list(
                open_question_proposals(
                    round_, system_defaults=system_defaults, start_index=len(proposals) + 1
                )
            )
            questionnaire = questionnaire_of(
                round_id,
                FormId(self._ids.new("f")),
                assembled.questions,
                assembled.list_items,
            )
            if questionnaire is not None:
                round_.integrate(questionnaire=questionnaire)
            else:
                draft = draft_of(round_id, DraftId(self._ids.new("d")), proposals, gaps)
                if draft is None:
                    raise ShapeError("整合既没有给出题目也没有给出提案")
                round_.integrate(draft=draft)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)

    async def _integrate_with_repair(self, *, round_id: RoundId):  # type: ignore[no-untyped-def]
        validation_error = ""
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        for attempt in range(1, MAX_INTEGRATION_ATTEMPTS + 1):
            round_ = self._rounds.get(round_id)
            document = self._documents.get(round_.project_id)
            ledger = self._evidence.get(round_.project_id)
            answered, _, skipped = render_answers(round_).partition("--")
            try:
                packet = await self._integrator.integrate(
                    document_state=render_document(document.view()),
                    claims=render_claims(round_.claims),
                    opinions=render_reviews(round_.current_reviews, titles=titles),
                    answered=answered,
                    skipped=skipped,
                    entry_ids=render_existing_entries(document),
                    validation_error=validation_error,
                )
            except ApplicationError as error:
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
            try:
                assembled = assemble(packet, round_=round_, document=document, ledger=ledger)
                gaps = dry_run(
                    document, assembled.proposals, round_=round_, now=self._clock.now()
                )
            except (PacketParseError, DomainError) as error:
                message = getattr(error, "message", str(error))
                if attempt == MAX_INTEGRATION_ATTEMPTS:
                    reason = f"整合两次都过不了校验：{message}"
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
                validation_error = message
                continue
            return assembled, gaps
        raise AssertionError("不可能走到这里")


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
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
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
            round_.submit_answers(answer_set)  # 先让领域把关（R6）
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
    ) -> None:
        self._documents = documents
        self._rounds = rounds
        self._evidence = evidence
        self._index = index
        self._catalog = catalog

    async def __call__(self, *, project_id: ProjectId) -> WorkspaceView:
        document = self._documents.get(project_id)
        round_id = self._index.latest_round_id(project_id)
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        round_ = None if round_id is None else self._rounds.get(round_id)
        ledger = None if round_ is None else self._evidence.get(project_id)
        return workspace_view(
            project=self._index.summary(project_id),
            document=document.view(),
            round_=round_,
            seat_titles=titles,
            catalog=self._catalog,
            ledger=ledger,
        )


class ListProjects:
    def __init__(self, *, index: WorkspaceIndex) -> None:
        self._index = index

    async def __call__(self) -> tuple[ProjectSummaryView, ...]:
        return self._index.list_summaries()


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
