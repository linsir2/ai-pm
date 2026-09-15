"""L4 记忆层对外暴露什么。"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.conversation import Message
from pmstudio.contracts.models.material import MaterialPacket
from pmstudio.contracts.models.memory import Memory
from pmstudio.contracts.models.results import AssembleResult
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion


@runtime_checkable
class MemoryPort(Protocol):
    """M13–M17 的对外面。裁剪不在这一层（那是 M24）。"""

    async def assemble_context(self, round_region: RoundRegion) -> AssembleResult:
        """M13 组装这一轮的上下文，并给出 `base_versions` 快照。"""
        ...

    async def retrieve(self, project_id: str, query: str) -> tuple[MaterialPacket, ...]:
        """M16 按需检索。**当前没有跨层调用方**：检索在 M13 组装内部发生。"""
        ...

    async def read_brief(self, project_id: str) -> str:
        """M14 简报全文。"""
        ...

    async def update_brief(self, project_id: str, lines: Sequence[str]) -> str:
        """改动简报，返回改完之后的全文。"""
        ...

    async def append_message(self, message: Message) -> None:
        """落一条消息（C17）。"""
        ...

    async def history_context(self, project_id: str, round_id: str) -> tuple[ContextBlock, ...]:
        """M15 决定带全量历史还是摘要 + 最近若干轮。"""
        ...

    async def record_candidates(self, memories: Sequence[Memory]) -> None:
        """M17 写候选记忆。区块由编排层写（I19）。"""
        ...

    async def invalidate(self, citations: Sequence[Citation]) -> None:
        """按引用判失效。**当前没有跨层调用方**：它由 `doc.changed` 事件驱动。"""
        ...
