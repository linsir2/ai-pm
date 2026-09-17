"""轮次状态机：把一轮从用户输入跑到等待用户。

11 步流程：
open_round → write round(assembling) → M13 组装 → 回填 scope.base_versions
→ M24 裁剪 → write context → phase=restating → M8 产理解卡
→ M9 打包 → write card_group → phase=awaiting_user
"""

from collections.abc import Sequence

from pmstudio.common.clock import Clock
from pmstudio.common.errors import ContractViolation
from pmstudio.common.ids import IdGenerator
from pmstudio.communication.board import BoardEditor, BoardReader
from pmstudio.contracts.enums import RegionName, RoundEntry, RoundPhase
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextRegion, RoundRegion
from pmstudio.orchestration.cards import CardAssembler
from pmstudio.orchestration.consensus import ConsensusGenerator


class RoundDriver:
    """轮次状态机：实现 OrchestrationPort.start_round（11 步）。"""

    def __init__(
        self,
        writer: BoardEditor,
        board: BoardReader,
        consensus: ConsensusGenerator,
        cards: CardAssembler,
        clock: Clock,
        ids: IdGenerator,
        context_assembler: object | None = None,
        harness: object | None = None,
    ) -> None:
        self._writer = writer
        self._board = board
        self._consensus = consensus
        self._cards = cards
        self._clock = clock
        self._ids = ids
        self._context_assembler = context_assembler
        self._harness = harness

    async def start_round(
        self,
        project_id: str,
        entry: RoundEntry,
        user_input: str,
        scope: Scope,
    ) -> str:
        """开一轮，返回 round_id。"""
        round_id = self._ids.new_id("rnd")  # type: ignore[attr-defined]

        # ① open_round
        await self._writer.open_round(round_id)

        # ② 写 round(assembling)
        round_region = RoundRegion(
            round_id=round_id,
            project_id=project_id,
            entry=entry,
            user_input=user_input,
            scope=scope,
            phase=RoundPhase.ASSEMBLING,
        )
        await self._writer.write(RegionName.ROUND, round_region)

        # ③ M13 组装上下文（如果注入了 assembler）
        base_versions: dict[str, int] = {}
        blocks: Sequence = ()
        if self._context_assembler is not None:
            assembled = await self._context_assembler.assemble_context(round_region)  # type: ignore[attr-defined]
            base_versions = assembled.base_versions
            blocks = assembled.blocks

        # ④ 回填 scope.base_versions
        if base_versions:
            round_region = round_region.model_copy(
                update={"scope": scope.model_copy(update={"base_versions": base_versions})}
            )
            await self._writer.write(RegionName.ROUND, round_region)

        # ⑤ M24 裁剪（如果注入了 harness）
        trimmed_blocks = blocks
        dropped: Sequence = ()
        if self._harness is not None and blocks:
            result = await self._harness.trim(blocks, project_id, "reg_model_default")  # type: ignore[attr-defined]
            trimmed_blocks = result.blocks
            dropped = result.dropped

        # ⑥ 写 context（不广播）
        await self._writer.write(
            RegionName.CONTEXT,
            ContextRegion(blocks=trimmed_blocks, dropped=dropped, assembled_at=self._clock.now()),
        )

        # ⑦ phase=restating
        round_region = round_region.model_copy(update={"phase": RoundPhase.RESTATING})
        await self._writer.write(RegionName.ROUND, round_region)

        # ⑧ M8 产理解卡
        card = await self._consensus.produce_understanding_card(round_region, list(trimmed_blocks))

        # ⑨ M9 打包
        group = self._cards.assemble((card,), round_id=round_id)

        # ⑩ 写 card_group（写即广播）
        await self._writer.write(RegionName.CARD_GROUP, group)

        # ⑪ phase=awaiting_user
        round_region = round_region.model_copy(update={"phase": RoundPhase.AWAITING_USER})
        await self._writer.write(RegionName.ROUND, round_region)

        return round_id
