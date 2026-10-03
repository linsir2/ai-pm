"""Memory Curator：每轮结束时问一句"这一轮的收获里，哪几条值得影响以后每一轮"。

它**只出提议**（`02` §11.0.44）：`write_document: false` 的精神不变——连记忆也不能自己改。
用户在草案卡上勾了哪几条，`ProposalCommitter` 才在同一个事务里把记忆落库。

失败处理与工具层同一条纪律：**它挂了不打崩这一轮**——草案照出，留一行痕。
记忆是旁路能力，不是交付的一部分。
"""

import json
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.ports import ModelGateway, ModelMessage, ModelRequest, PromptLibrary
from pmstudio.domain.shared.ids import RoundId
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp

PURPOSE = "memory_curation"


@dataclass(frozen=True, slots=True)
class CuratorOutcome:
    """这一趟交回的变更指令 + 报文里被截断 / 被摘掉的那些（走同一条留痕通道）。

    `changes` **还没经过领域校验**：缺出处、点名不存在、装不下都留给
    `domain/memory/memory.py::screen_changes`（一处实现，别写第二份判据）。
    """

    changes: tuple[MemoryChange, ...]
    overflow: tuple[schemas.Overflow, ...] = ()
    discarded: tuple[schemas.Discarded, ...] = ()


class MemoryCurator:
    PURPOSE = PURPOSE
    PROMPT = "memory_curator"
    #: 槽位顺序就是提示词里的节顺序；少给一个都会炸（`FilePromptLibrary` 的既定行为）
    PROMPT_SLOTS = ("memory_state", "round_digest", "schema_json", "validation_error")

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def propose(
        self,
        *,
        memory_state: str,
        round_digest: str,
        round_id: RoundId | None = None,
        validation_error: str = "",
    ) -> CuratorOutcome:
        schema = schemas.MemoryPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            memory_state=memory_state,
            round_digest=round_digest,
            schema_json=json.dumps(schema, ensure_ascii=False),
            validation_error=validation_error,
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="MemoryPacket",
                response_schema=schema,
                metadata={} if round_id is None else {"round_id": str(round_id)},
            )
        )
        packet, overflow, discarded = schemas.parse_packet_lenient(
            reply.text, schemas.MemoryPacket, purpose=self.PURPOSE
        )
        return CuratorOutcome(
            changes=tuple(_change_of(item) for item in packet.changes),
            overflow=overflow,
            discarded=discarded,
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
