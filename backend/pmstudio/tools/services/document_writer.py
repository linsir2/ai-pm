"""M20 文档写入：AI 写入、手改、回退三种来源共用这一个入口（I1）。

R1.4 只实现 submit 路径（确认填充卡 → 写入文档）。
manual / rollback 路径留 R2。
"""


from pmstudio.common.clock import Clock
from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import EventType, ProducerIdentity, VersionTrigger
from pmstudio.contracts.models.card import CardAnswer, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.document import BlockOp, WritePayload
from pmstudio.contracts.models.results import WriteDocumentResult
from pmstudio.contracts.skeleton.events import DocChangedPayload, Event
from pmstudio.storage.ledger import Ledger


class DocumentWriter:
    """M20：唯一写文档入口（I1）。"""

    def __init__(self, ledger: Ledger, bus: object, clock: Clock) -> None:
        self._ledger = ledger
        self._bus = bus
        self._clock = clock

    async def write_document(
        self,
        trigger: VersionTrigger,
        payload: WritePayload,
        *,
        group: CardGroup | None = None,
        base_versions: dict[str, int] | None = None,
        scope: object | None = None,
        round_id: str | None = None,
        doc_id: str | None = None,
        answers: list | None = None,
    ) -> WriteDocumentResult:
        """写入文档，产生新版本。"""
        if trigger is not VersionTrigger.SUBMIT:
            raise ContractViolation(
                f"R1.4 只实现 submit 路径，{trigger.value} 留 R2"
            )

        return await self._write_submit(
            group=group,
            base_versions=base_versions or {},
            scope=scope,
            round_id=round_id,
            doc_id=doc_id,
            answers=answers,
        )

    async def _write_submit(
        self,
        *,
        group: CardGroup | None,
        base_versions: dict[str, int],
        scope: object | None,
        round_id: str | None,
        doc_id: str | None,
        answers: list | None = None,
    ) -> WriteDocumentResult:
        """submit 路径：取 kept proposals → 版本校验 → 写入。"""
        if group is None or doc_id is None:
            raise ContractViolation("submit 路径需要 group 和 doc_id")

        # 1. 取 kept proposals
        kept_proposals = self._get_kept_proposals(group, answers or [])
        if not kept_proposals:
            raise ContractViolation("没有 state = kept 的提案——全删了 = 这次不写")

        # 2. 按 target_label → block_id 映射，构造 BlockOp
        ops = self._build_block_ops(
            doc_id=doc_id,
            proposals=kept_proposals,
            base_versions=base_versions,
            scope=scope,
        )

        # 3. 写入（ledger 内含版本校验 I10 + 事务 I9）
        version = self._ledger.write_blocks(
            doc_id, ops, VersionTrigger.SUBMIT, group_id=group.group_id,
        )

        # 4. 发 doc.changed
        block_ids = tuple(op.block_id for op in ops)
        await self._bus.publish(
            Event(
                type=EventType.DOC_CHANGED,
                payload=DocChangedPayload(
                    doc_id=doc_id,
                    version_id=version.version_id,
                    seq=version.seq,
                    trigger=VersionTrigger.SUBMIT,
                    block_ids=block_ids,
                    round_id=round_id,
                ),
                at=self._clock.now(),
            ),
            producer=ProducerIdentity.DOCUMENT_WRITER,
        )

        blocks = tuple(self._ledger.read_blocks(doc_id))
        return WriteDocumentResult(blocks=blocks, version=version)

    def _get_kept_proposals(
        self, group: CardGroup, answers: list,
    ) -> list[tuple[Proposal, CardAnswer | None]]:
        """从卡片组取 kept 的提案。"""
        kept: list[tuple[Proposal, CardAnswer | None]] = []
        for card in group.cards:
            if card.kind.value != "fill":
                continue
            # 找这张卡的 answer
            card_answer = None
            for answer in answers:
                if answer.card_id == card.card_id:
                    card_answer = answer
                    break

            if card_answer is not None:
                for proposal in card.proposals:
                    state = card_answer.proposal_states.get(proposal.proposal_id)
                    if state and state.value == "kept":
                        kept.append((proposal, card_answer))
            else:
                # 没有 answer → 默认全部 kept
                for proposal in card.proposals:
                    if proposal.state.value == "kept":
                        kept.append((proposal, None))
        return kept

    def _build_block_ops(
        self,
        doc_id: str,
        proposals: list[tuple[Proposal, CardAnswer | None]],
        base_versions: dict[str, int],
        scope: object | None,
    ) -> list[BlockOp]:
        """按 target_label → block_id 映射，构造 BlockOp。"""
        blocks = {b.schema_label: b for b in self._ledger.read_blocks(doc_id) if b.parent_id is None}

        ops: list[BlockOp] = []
        for proposal, _ in proposals:
            # I17 写范围校验
            if hasattr(scope, "covers") and not scope.covers(proposal.target_label):
                raise ContractViolation(
                    f"提案落在选区外：{proposal.target_label}（I17：AI 不能写选区外的字段）"
                )

            block = blocks.get(proposal.target_label)
            if block is None:
                raise ContractViolation(
                    f"找不到 target_label = {proposal.target_label} 对应的块（I22：顶层字段名必须存在）"
                )

            expected = base_versions.get(block.block_id, block.version)
            ops.append(BlockOp(
                block_id=block.block_id,
                op=proposal.op,
                content=proposal.content,
                expected_version=expected,
            ))
        return ops
