"""唯一写入路径。界面只传 proposal_id，提案内容以 Round 里存的那份为准。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.version import Version, VersionTrigger
from pmstudio.domain.ports import (
    DocumentReader,
    DocumentWriter,
    EvidenceReader,
    IdSource,
    MemoryReader,
    MemoryWriter,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import OriginType, Proposal, merge_duplicate_removes
from pmstudio.domain.round.round import MemoryProposal, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.services.scope_guard import ScopeGuard, ScopeViolation
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.errors import (
    IllegalStepTransition,
    NothingToCommit,
    UnknownMemoryProposal,
    UnknownProposal,
    UntraceableContent,
)
from pmstudio.domain.shared.ids import BlockId, ProjectId, ProposalId, RoundId
from pmstudio.domain.shared.memory import MemoryChange

TRACEABLE_ORIGINS = (OriginType.USER, OriginType.DERIVED)


@dataclass(frozen=True, slots=True)
class CommitResult:
    version: Version
    written_block_ids: tuple[BlockId, ...]
    advisories: tuple[ScopeViolation, ...]
    #: 同一批里被合并掉的重复 `remove`（同一 `entry_id` 只该撤一次）。
    #: 它们**不是内容**——条目照样被撤下，所以不进 `unaccounted` / `dropped` 那两条通道；
    #: 留在这里是为了可断言（真跑里就是它把整批确认打成 422 的，P30 · F28）。
    merged_removes: tuple[ProposalId, ...] = ()


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
        memory: MemoryReader | None = None,
        memory_writer: MemoryWriter | None = None,
        ids: IdSource | None = None,
    ) -> None:
        self._documents = documents
        self._writer = writer
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._scope = scope
        self._traceability = traceability
        self._uow = uow
        #: 跨轮记忆（M6）：与文档**同一个事务**写。三个都可以缺省（老测试只测文档那一支），
        #: 缺了就是"这一轮没有记忆要写"。
        self._memory = memory
        self._memory_writer = memory_writer
        self._ids = ids

    def commit(
        self,
        *,
        round_id: RoundId,
        accepted: Sequence[ProposalId],
        now: datetime,
        accepted_memory: Sequence[str] = (),
    ) -> CommitResult:
        with self._uow:
            round_ = self._rounds.get(round_id)
            # 写入端先自查前置条件：不要在写完文档之后才发现状态不对
            if round_.waiting_on is not WaitingOn.DRAFT:
                raise IllegalStepTransition(current=round_.step, target=Step.AWAITING_CONFIRM)
            proposals, merged = self._resolve(round_, accepted)
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
            # 记忆与文档**同一个事务**（M6）：要么一起成，要么一起回滚。
            # 顺序上先写文档再写记忆只是可读性——`MemoryWriter` 不可用/`ids` 缺失时
            # 直接跳过（老测试只测文档那一支，不给记忆注入）。
            accepted_memory = tuple(accepted_memory)
            if accepted_memory:
                self._save_memory(round_, accepted_memory, ledger=ledger, now=now)
            # 记下"它提议的"给了几条、用户收了几条（C4）：给的是**整个草案**里的 proposed，
            # 收的是**勾了的那部分**里的 proposed——两个数都能从这一轮自己推出来，不用建表。
            offered = [
                item
                for item in round_.draft.proposals
                if item.origin.type is OriginType.PROPOSED
            ]
            # **用用户接受的 id**，不是合并后的提案列表：被合并掉的那条也是用户勾的，
            # 从 `proposals` 算会把 `proposed_accepted` 少算一条（C4 那条提示因此被带偏）。
            taken = set(accepted)
            round_.confirm(
                version.version_no,
                proposed_offered=len(offered),
                proposed_accepted=sum(1 for item in offered if item.proposal_id in taken),
                accepted_memory_ids=accepted_memory,
            )
            self._round_writer.save(round_)
        return CommitResult(
            version=version,
            written_block_ids=version.changed_block_ids,
            advisories=advisories,
            merged_removes=merged,
        )

    def apply_manual(
        self,
        *,
        project_id: ProjectId,
        instruction: WriteInstruction,
        now: datetime,
        summary: str,
    ) -> CommitResult:
        """用户手改文档：一次改动一版，`trigger=manual`。

        它与 `commit()` 共用同一个 `DocumentWriter`——写入能力仍然只有这一个文件持有
        （守卫 `test_write_path.py` 钉着）。所以手改不是第二条写入路径，
        只是同一个写入口的另一种来源。
        """

        with self._uow:
            document: Document = self._documents.get(project_id)
            version = document.apply(
                [instruction],
                now=now,
                summary=summary,
                trigger=VersionTrigger.MANUAL,
            )
            self._writer.save(document)
        return CommitResult(
            version=version,
            written_block_ids=version.changed_block_ids,
            advisories=(),
        )

    def rollback_to(
        self, *, project_id: ProjectId, version_no: int, now: datetime, summary: str
    ) -> CommitResult:
        """回到某一版：产生**新**一版（`trigger=rollback`），旧版本一个都不删（D6）。

        与手改一样，落盘仍然只经过这一个文件里的 `DocumentWriter`
        （守卫 `test_write_path.py` 盯着白名单）；编排层、AI 都拿不到这条路径。
        """

        with self._uow:
            document: Document = self._documents.get(project_id)
            version = document.rollback_to(version_no, now=now, summary=summary)
            self._writer.save(document)
        return CommitResult(
            version=version,
            written_block_ids=version.changed_block_ids,
            advisories=(),
        )

    def _resolve(
        self, round_: Round, accepted: Sequence[ProposalId]
    ) -> tuple[tuple[Proposal, ...], tuple[ProposalId, ...]]:
        """用户勾的那些提案 → 真正要执行的（+ 被合并掉的重复 `remove`）。

        合并这一步是**保底**：装配层已经不让重复的 `remove` 进卡片了，但**修好之前存下来的
        老草案**（`serialization.py` 原样读回来的那种）里还带着两条——不在这里合，
        用户点"确认写入"就会撞 `UnknownEntry`（422），而且**一条都写不进去**（P30 · F28）。
        """

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
        return merge_duplicate_removes(tuple(by_id[proposal_id] for proposal_id in accepted))

    def _assert_traceable(self, proposal: Proposal, *, ledger: EvidenceLedger) -> None:
        if proposal.origin.type not in TRACEABLE_ORIGINS:
            return  # proposed / system_default 已在草案卡上显式标注，勾选即为授权
        result = self._traceability.check(proposal.origin.refs, ledger=ledger)
        if not result.traceable:
            raise UntraceableContent(proposal_id=proposal.proposal_id, reason=result.reason)

    def _to_instruction(self, proposal: Proposal) -> WriteInstruction:
        return to_write_instruction(proposal)

    def _save_memory(
        self,
        round_: Round,
        accepted: Sequence[str],
        *,
        ledger: EvidenceLedger,
        now: datetime,
    ) -> None:
        """把用户勾的那些记忆提议落库（M6）——与文档写入**同一个事务**。

        三件事：

        ① 勾的 id 必须是这一轮真的出过的那几条（点名不存在 → `UnknownMemoryProposal`，
           整批回滚：文档也不写。宁可一条都不写，也不写半份）；
        ② 指令的判据仍然只有**一处**（`ProjectMemory.apply`）：缺出处 / 点名不存在 /
           超容量——任何一条不合法都抛，事务照样回滚；
        ③ 记忆没接（`memory`/`memory_writer`/`ids` 缺省）时**什么都不做**：
           老调用方式（只写文档）行为逐字节不变。
        """

        if self._memory is None or self._memory_writer is None or self._ids is None:
            return
        by_id: dict[str, MemoryProposal] = {
            item.proposal_id: item for item in round_.memory_proposals
        }
        changes: list[MemoryChange] = []
        for proposal_id in accepted:
            item = by_id.get(proposal_id)
            if item is None:
                raise UnknownMemoryProposal(proposal_id=str(proposal_id))
            changes.append(item.change)
        memory = self._memory.get(round_.project_id)
        updated = memory.apply(
            changes,
            round_id=str(round_.round_id),
            known_refs=frozenset(str(item.eid) for item in ledger.evidences()),
            now=now,
            ids=self._ids,
        )
        self._memory_writer.save(updated)


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
        allow_manual_override=proposal.allow_manual_override,
    )
