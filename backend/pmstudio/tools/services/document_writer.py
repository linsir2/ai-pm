"""M20 文档写入：AI 写入、手改、回退三种来源共用这一个入口（I1）。

**v2（DDD 重构）**：`write_document(trigger, payload)` 严格两参（端口签名锁钉死）。
M20 不去调用方要东西——按 CONTRACTS §5.3，`submit` 的 payload 只有 `group_id`，
**卡片组自己去黑板读**（C12 `card_group` 区块），scope / round / 版本快照从
`round` 区块读，`doc_id` 按 project 查账本。这就是「D1 修正」：
协议、实现、契约文档三方对齐。

R1.4 只实现 submit 路径；manual / rollback 路径留 R2。
"""


from pmstudio.common.clock import Clock
from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    CardKind,
    EventType,
    ProducerIdentity,
    RegionName,
    VersionTrigger,
)
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.document import WritePayload
from pmstudio.contracts.models.results import WriteDocumentResult
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.contracts.skeleton.events import DocChangedPayload, Event
from pmstudio.storage.ledger import Ledger


class DocumentWriter:
    """M20：唯一写文档入口（I1）。

    v2 注入 `board`（黑板只读句柄）：submit 路径从这里读卡片组与轮次信息，
    不再由调用方传参——这是 D1 修正的核心。
    """

    def __init__(self, ledger: Ledger, bus: object, clock: Clock, board: object) -> None:
        self._ledger = ledger
        self._bus = bus
        self._clock = clock
        self._board = board

    async def write_document(
        self,
        trigger: VersionTrigger,
        payload: WritePayload,
    ) -> WriteDocumentResult:
        """写入文档，产生新版本。严格两参（端口签名锁）。"""
        if trigger is not VersionTrigger.SUBMIT:
            raise ContractViolation(
                f"R1.4 只实现 submit 路径，{trigger.value} 留 R2"
            )

        # M20 自己去黑板读这一组（CONTRACTS §5.3）
        group = await self._read_group(payload.group_id)
        round_region = await self._read_round()
        doc_id = self._resolve_doc_id(round_region.project_id)

        # 取 kept proposals（用户裁决已合并进卡片组的 proposal.state）
        kept_proposals = group.kept_proposals()
        if not kept_proposals:
            raise ContractViolation("没有 state = kept 的提案——全删了 = 这次不写")

        # 按 target_label → block_id 映射，构造 BlockOp（source_card_id 透传卡片留痕，I3）
        ops = self._build_block_ops(
            doc_id=doc_id,
            group=group,
            base_versions=round_region.scope.base_versions,
            scope=round_region.scope,
        )

        # 写入（ledger 内含版本校验 I10 + 事务 I9）
        version = self._ledger.write_blocks(
            doc_id, ops, VersionTrigger.SUBMIT, group_id=group.group_id,
        )

        # 发 doc.changed
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
                    round_id=round_region.round_id,
                ),
                at=self._clock.now(),
            ),
            producer=ProducerIdentity.DOCUMENT_WRITER,
        )

        blocks = tuple(self._ledger.read_blocks(doc_id))
        return WriteDocumentResult(blocks=blocks, version=version)

    async def _read_group(self, group_id: str) -> CardGroup:
        """从黑板读当前卡片组，校验与 payload 指名的组一致。"""
        value = await self._board.read(RegionName.CARD_GROUP)
        if not isinstance(value, CardGroup):
            raise ContractViolation("黑板 card_group 区块没有可写入的卡片组")
        if value.group_id != group_id:
            raise ContractViolation(
                f"payload 指名的卡片组 {group_id} 与黑板当前组 {value.group_id} 不一致"
            )
        return value

    async def _read_round(self) -> RoundRegion:
        """从黑板读当前轮（scope / round_id / project_id 的出处）。"""
        value = await self._board.read(RegionName.ROUND)
        if not isinstance(value, RoundRegion):
            raise ContractViolation("黑板 round 区块没有进行中的轮次")
        return value

    def _resolve_doc_id(self, project_id: str) -> str:
        doc = self._ledger.read_document_by_project(project_id)
        if doc is None:
            raise ContractViolation(f"项目 {project_id} 还没有文档")
        return doc.doc_id

    def _build_block_ops(
        self,
        doc_id: str,
        group: CardGroup,
        base_versions: dict[str, int],
        scope: object,
    ) -> list:
        """按 target_label → block_id 映射，构造 BlockOp。

        `source_card_id` 取填充卡的 card_id（I16：填充卡单独成批，所以正好一张）——
        I3 审计留痕：AI 写的块都要能追到一次用户确认。
        """
        kept_proposals = group.kept_proposals()
        fill_cards = [c for c in group.cards if c.kind is CardKind.FILL]
        source_card_id = fill_cards[0].card_id if fill_cards else None

        blocks = {b.schema_label: b for b in self._ledger.read_blocks(doc_id) if b.parent_id is None}

        ops = []
        for proposal in kept_proposals:
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
            from pmstudio.contracts.models.document import BlockOp

            ops.append(BlockOp(
                block_id=block.block_id,
                op=proposal.op,
                content=proposal.content,
                expected_version=expected,
                source_card_id=source_card_id,
            ))
        return ops
