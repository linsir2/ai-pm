"""L2 编排层对外暴露什么——界面对 AI 的唯一入口。"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pmstudio.contracts.enums import RoundEntry
from pmstudio.contracts.models.card import CardAnswer
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.discussion import DiscussionRound
from pmstudio.contracts.models.idea import IdeaCandidate
from pmstudio.contracts.models.scope import Scope


@runtime_checkable
class OrchestrationPort(Protocol):
    """用户动作进 AI 流程的六个入口。"""

    async def start_round(self, project_id: str, entry: RoundEntry, user_input: str, scope: Scope) -> str:
        """开一轮，返回 `round_id`。写 `round` 区块，写即广播。"""
        ...

    async def submit_cards(self, group_id: str, answers: Sequence[CardAnswer]) -> CardGroup:
        """用户提交一组卡片。确认后整组冻结，`card_group` 区块写即广播。"""
        ...

    async def run_discussion(self, project_id: str, user_input: str) -> DiscussionRound:
        """开一轮讨论。讨论区只产素材，不产卡片（I11）。"""
        ...

    async def summarize_discussion(self, discussion_round_id: str) -> tuple[IdeaCandidate, ...]:
        """用户点「整理成提案」：M29 把发言整理成候选。"""
        ...

    async def select_ideas(self, idea_ids: Sequence[str], discarded_ids: Sequence[str]) -> None:
        """用户勾选结果。此刻**还没有消费**——消费发生在下一轮组装上下文时。"""
        ...

    async def stop_round(self, round_id: str) -> None:
        """用户叫停。主闭环与讨论区共用。"""
        ...
