"""唯一写入路径。界面只传 proposal_id，提案内容以 Round 里存的那份为准。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.version import Version
from pmstudio.domain.ports import (
    DocumentReader,
    DocumentWriter,
    EvidenceReader,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.services.scope_guard import ScopeGuard, ScopeViolation
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.errors import (
    IllegalStepTransition,
    NothingToCommit,
    UnknownProposal,
    UntraceableContent,
)
from pmstudio.domain.shared.ids import BlockId, ProposalId, RoundId

TRACEABLE_ORIGINS = (OriginType.USER, OriginType.DERIVED)


@dataclass(frozen=True, slots=True)
class CommitResult:
    version: Version
    written_block_ids: tuple[BlockId, ...]
    advisories: tuple[ScopeViolation, ...]


class ProposalCommitter:
    def __init__(
        self,
        *,
        documents: DocumentReader,
        writer: DocumentWriter,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        scope: ScopeGuard,
        traceability: TraceabilityChecker,
        uow: UnitOfWork,
    ) -> None:
        self._documents = documents
        self._writer = writer
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._scope = scope
        self._traceability = traceability
        self._uow = uow

    def commit(
        self, *, round_id: RoundId, accepted: Sequence[ProposalId], now: datetime
    ) -> CommitResult:
        with self._uow:
            round_ = self._rounds.get(round_id)
            # 写入端先自查前置条件：不要在写完文档之后才发现状态不对
            if round_.waiting_on is not WaitingOn.DRAFT:
                raise IllegalStepTransition(current=round_.step, target=Step.AWAITING_CONFIRM)
            proposals = self._resolve(round_, accepted)
            ledger = self._evidence.get(round_.project_id)
            for proposal in proposals:
                self._assert_traceable(proposal, ledger=ledger)
            advisories = self._scope.check(proposals, write_scope=round_.write_scope)
            document: Document = self._documents.get(round_.project_id)
            version = document.apply(
                tuple(self._to_instruction(proposal) for proposal in proposals),
                now=now,
                summary=f"第 {round_id} 轮确认写入 {len(proposals)} 条",
                authorized_new_features=round_.authorized_new_features,
            )
            self._writer.save(document)
            round_.confirm(version.version_no)
            self._round_writer.save(round_)
        return CommitResult(
            version=version,
            written_block_ids=version.changed_block_ids,
            advisories=advisories,
        )

    def _resolve(self, round_: Round, accepted: Sequence[ProposalId]) -> tuple[Proposal, ...]:
        draft = round_.draft
        if draft is None:
            raise NothingToCommit()
        accepted = tuple(accepted)
        if not accepted:
            raise NothingToCommit()
        by_id = {proposal.proposal_id: proposal for proposal in draft.proposals}
        for proposal_id in accepted:
            if proposal_id not in by_id:
                raise UnknownProposal(proposal_id=proposal_id)
        return tuple(by_id[proposal_id] for proposal_id in accepted)

    def _assert_traceable(self, proposal: Proposal, *, ledger: EvidenceLedger) -> None:
        if proposal.origin.type not in TRACEABLE_ORIGINS:
            return  # proposed / system_default 已在草案卡上显式标注，勾选即为授权
        result = self._traceability.check(proposal.origin.refs, ledger=ledger)
        if not result.traceable:
            raise UntraceableContent(proposal_id=proposal.proposal_id, reason=result.reason)

    def _to_instruction(self, proposal: Proposal) -> WriteInstruction:
        return to_write_instruction(proposal)


def to_write_instruction(proposal: Proposal) -> WriteInstruction:
    """Proposal（round 上下文）→ WriteInstruction（authoring 上下文）。

    两个上下文因此不必互相 import；这也是"草案落卡前干跑"与"确认写入"共用的唯一翻译
    （计划 P2 §3.1 ④）。
    """

    return WriteInstruction(
        instruction_id=str(proposal.proposal_id),
        entry_id=proposal.entry_id,
        op=proposal.op,
        target=proposal.target,
        text=proposal.text,
        refs=proposal.origin.refs,
        kind=proposal.kind,
    )
