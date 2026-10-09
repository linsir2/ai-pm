"""Memory Curator：每轮结束时问一句"这一轮的收获里，哪几条值得影响以后每一轮"。

它**只出提议**（`02` §11.0.44）：`write_document: false` 的精神不变——连记忆也不能自己改。
用户在草案卡上勾了哪几条，`ProposalCommitter` 才在同一个事务里把记忆落库。

失败处理与工具层同一条纪律：**它挂了不打崩这一轮**——草案照出，留一行痕。
记忆是旁路能力，不是交付的一部分。
"""

from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.definitions import MEMORY_CURATOR
from pmstudio.domain.shared.ids import RoundId
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope


@dataclass(frozen=True, slots=True)
class CuratorOutcome:
    """这一趟交回的变更指令 + 报文里被截断 / 被摘掉的那些（走同一条留痕通道）。

    `changes` **还没经过领域校验**：缺出处、点名不存在、装不下都留给
    `domain/memory/memory.py::screen_changes`（一处实现，别写第二份判据）。
    """

    changes: tuple[MemoryChange, ...]
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        """转发到信封——只有信封一处存储。"""

        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


class MemoryCurator(Agent[CuratorOutcome, schemas.MemoryPacket]):
    definition = MEMORY_CURATOR

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        super().__init__(model=model, prompts=prompts)

    async def propose(
        self,
        *,
        memory_state: str,
        round_digest: str,
        round_id: RoundId | None = None,
        validation_error: str = "",
        packet_slot: str = "",
    ) -> CuratorOutcome:
        outcome = await self._call(
            slots={
                "memory_state": memory_state,
                "round_digest": round_digest,
                "validation_error": validation_error,
            },
            metadata={} if round_id is None else {"round_id": str(round_id)},
            packet_id=self._make_packet_id(
                str(round_id or "new"), "memory_curator", slot=packet_slot
            ),
            run_id=str(round_id or "new"),
            produced_by="memory_curator",
        )
        return CuratorOutcome(
            changes=tuple(_change_of(item) for item in outcome.packet.changes),
            envelope=outcome,
        )


def _change_of(draft: schemas.MemoryChangeDraft) -> MemoryChange:
    return MemoryChange(
        op=MemoryOp(draft.op),
        kind=MemoryKind(draft.kind),
        text=draft.text,
        refs=tuple(str(ref) for ref in draft.refs),
        target_id=draft.target_id,
        reason=draft.reason,
    )
