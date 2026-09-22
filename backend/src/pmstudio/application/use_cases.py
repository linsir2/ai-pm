"""一个用例一个入口。

两条铁律：**模型调用一律在事务外**（`uow` 里不许 `await`，由守卫强制）；
**用例不写领域规则**（规则在聚合与领域服务里，用例只负责编排与事务边界）。
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, replace

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.context import (
    render_answers,
    render_claims,
    render_document,
    render_reviews,
)
from pmstudio.application.errors import ApplicationError
from pmstudio.application.events import (
    GENERATION_FAILED,
    PACKET_EMITTED,
    ROUND_UPDATED,
    Event,
)
from pmstudio.application.ports import (
    Clock,
    EventPublisher,
    IdGenerator,
    SeatCatalog,
    SeatDefinition,
)
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.ports import (
    DocumentReader,
    EvidenceReader,
    EvidenceWriter,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.round import Round
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import (
    EvidenceId,
    ProjectId,
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
            raise failures[0][1]

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
