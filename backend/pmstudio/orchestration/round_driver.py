"""轮次状态机：把一轮从用户输入跑到写入完成。

R1.3（11 步）：start_round → open_round → 组装 → 裁剪 → 复述 → 理解卡 → awaiting_user
R1.4（submit_cards）：确认理解卡 → M28 生成填充卡 → 确认填充卡 → M20 写入 → done
"""

from collections.abc import Sequence

from pmstudio.common.clock import Clock
from pmstudio.common.errors import ContractViolation
from pmstudio.common.ids import IdGenerator
from pmstudio.communication.board import BoardEditor, BoardReader
from pmstudio.contracts.enums import RegionName, RoundEntry, RoundPhase
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
        drafter: object | None = None,
        document_writer: object | None = None,
        ledger: object | None = None,
    ) -> None:
        self._writer = writer
        self._board = board
        self._consensus = consensus
        self._cards = cards
        self._clock = clock
        self._ids = ids
        self._context_assembler = context_assembler
        self._harness = harness
        self._drafter = drafter
        self._document_writer = document_writer
        self._ledger = ledger

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

    async def submit_cards(
        self,
        group_id: str,
        answers: Sequence,  # Sequence[CardAnswer]
    ) -> object:  # CardGroup
        """用户提交一组卡片。确认后整组冻结，card_group 区块写即广播。"""
        from pmstudio.contracts.models.card import CardKind

        # 1. 读当前轮 + card_group
        round_region = await self._board.read(RegionName.ROUND)
        if round_region is None:
            raise ContractViolation("没有正在进行的轮次")

        group = await self._board.read(RegionName.CARD_GROUP)
        if group is None:
            raise ContractViolation(f"找不到卡片组 {group_id}")

        # 2. 更新每张卡的 answer + status
        updated_cards = list(group.cards)
        for answer in answers:
            for idx, card in enumerate(updated_cards):
                if card.card_id == answer.card_id:
                    updated_cards[idx] = card.model_copy(update={
                        "answer": answer.answer,
                        "status": answer.status,
                    })
                    break

        # 3. 判断卡片类型
        card = updated_cards[0]
        if card.kind is CardKind.UNDERSTANDING:
            return await self._handle_understanding_card(
                round_region, group, updated_cards, answers[0],
            )
        elif card.kind is CardKind.FILL:
            return await self._handle_fill_card(
                round_region, group, updated_cards, answers[0],
            )
        else:
            raise ContractViolation(f"R1.4 不支持 {card.kind.value} 卡的提交")

    async def _handle_understanding_card(
        self,
        round_region: RoundRegion,
        group: object,
        updated_cards: list,
        answer,  # CardAnswer
    ) -> object:  # CardGroup
        """确认理解卡 → M28 生成填充卡。"""
        if answer.verdict == "correct":
            raise ContractViolation(
                "R1.4 不实现纠正路径（verdict = correct → 重新组装 → 复述）。留 R2"
            )

        # 确认 → M28 生成填充卡
        if self._drafter is None:
            raise ContractViolation("Drafter 未注入")

        fill_card = await self._drafter.draft_fill_card(round_region, [])  # type: ignore[attr-defined]

        # 打包新的 card_group（填充卡单独成批，I16）
        new_group = self._cards.assemble((fill_card,), round_id=round_region.round_id)
        await self._writer.write(RegionName.CARD_GROUP, new_group)

        # phase=drafting（填充卡已生成，等用户确认）
        round_region = round_region.model_copy(update={"phase": RoundPhase.AWAITING_USER})
        await self._writer.write(RegionName.ROUND, round_region)

        return new_group

    async def _handle_fill_card(
        self,
        round_region: RoundRegion,
        group: object,
        updated_cards: list,
        answer,  # CardAnswer
    ) -> object:  # CardGroup
        """确认填充卡 → M20 写入文档。"""
        if self._document_writer is None or self._ledger is None:
            raise ContractViolation("DocumentWriter / Ledger 未注入")

        # 冻结整组
        from pmstudio.contracts.enums import CardGroupState
        confirmed = group.model_copy(update={"state": CardGroupState.CONFIRMED})  # type: ignore[attr-defined]
        await self._writer.write(RegionName.CARD_GROUP, confirmed)

        # M20 写入
        from pmstudio.contracts.enums import VersionTrigger
        from pmstudio.contracts.models.document import SubmitPayload

        doc_id = self._get_doc_id(round_region.project_id)
        await self._document_writer.write_document(  # type: ignore[attr-defined]
            trigger=VersionTrigger.SUBMIT,
            payload=SubmitPayload(group_id=group.group_id),
            group=confirmed,
            base_versions=round_region.scope.base_versions,
            scope=round_region.scope,
            round_id=round_region.round_id,
            doc_id=doc_id,
            answers=[answer],
        )

        # phase=writing → done
        from pmstudio.contracts.enums import RoundEndReason
        round_region = round_region.model_copy(update={"phase": RoundPhase.WRITING})
        await self._writer.write(RegionName.ROUND, round_region)
        round_region = round_region.model_copy(update={
            "phase": RoundPhase.DONE,
            "ended_at": self._clock.now(),
            "end_reason": RoundEndReason.COMPLETED,
        })
        await self._writer.write(RegionName.ROUND, round_region)

        return confirmed

    def _get_doc_id(self, project_id: str) -> str:
        """按 project_id 找 doc_id。"""
        if self._ledger is None:
            raise ContractViolation("Ledger 未注入")
        doc = self._ledger.read_document_by_project(project_id)  # type: ignore[attr-defined]
        if doc is None:
            raise ContractViolation(f"项目 {project_id} 还没有文档")
        return doc.doc_id
