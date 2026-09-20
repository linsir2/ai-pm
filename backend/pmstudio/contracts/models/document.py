"""C2 Block / Document 与 C3 Document Version（文档与版本）。

用户手动编辑和 AI 写入共用同一条版本时间线。改动与回退的原子单位是一个文档版本；
回退产生新版本，不删不改历史（I8）。
"""

from collections.abc import Sequence

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import BlockOpKind, VersionTrigger
from pmstudio.contracts.models.base import ContractModel


class Block(ContractModel):
    """文档树上的一个块。"""

    block_id: str
    parent_id: str | None = None
    schema_label: str
    content: str = ""
    version: int = Field(default=1, ge=1)
    source_card_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Block":
        if not self.block_id:
            raise ContractViolation("block_id 不能为空")
        if not self.schema_label:
            raise ContractViolation("schema_label 不能为空——M20 靠它定位、M28 靠它落位")
        if self.parent_id == self.block_id:
            raise ContractViolation("块不能把自己当父块——文档树不是环")
        return self

    @property
    def is_ai_written(self) -> bool:
        """AI 写的块都带 `source_card_id`，手写的留空（I3）。"""
        return self.source_card_id is not None


class Document(ContractModel):
    """一个项目一个文档。"""

    doc_id: str
    project_id: str
    template_id: str

    @model_validator(mode="after")
    def _check(self) -> "Document":
        if not self.doc_id:
            raise ContractViolation("doc_id 不能为空")
        if not self.project_id:
            raise ContractViolation("project_id 不能为空——记忆与检索按项目隔离")
        if not self.template_id:
            raise ContractViolation("template_id 不能为空——决定文档的字段分区")
        return self


class BlockOp(ContractModel):
    """一次写入里对某个块的操作（CONTRACTS §5.3 `writeDocument` 的 payload 项）。

    `source_card_id`：这次写入的出处——AI 写入带填充卡的 `card_id`（I3 审计留痕），
    manual 留空（I3：手写的块没有来源卡）。
    """

    block_id: str
    op: BlockOpKind
    content: str
    expected_version: int = Field(ge=1)
    source_card_id: str | None = None


class DocumentSnapshot(ContractModel):
    """某一版文档的完整内容。"""

    blocks: tuple[Block, ...]

    @model_validator(mode="after")
    def _check(self) -> "DocumentSnapshot":
        seen: set[str] = set()
        for block in self.blocks:
            if block.block_id in seen:
                raise ContractViolation(f"同一份快照里出现了重复的 block_id：{block.block_id}")
            seen.add(block.block_id)
        assert_unique_top_level_labels(self.blocks)
        return self


def assert_unique_top_level_labels(blocks: Sequence[Block]) -> None:
    """同一个文档里，**顶层**块的 `schema_label` 必须唯一（I22）。

    M20 靠 label 定位块——C1 的写范围（`selected_fields`）与 C7 提案的 `target_label` 都是字段名；
    两个同名的顶层块会让"写到哪一块"没有唯一答案。**只约束顶层**：定位只发生在顶层，
    嵌套块的重名规则等 R2 引入"加块"时再定。

    放在模块级而不是只写在 `DocumentSnapshot` 里，是因为写入路径（`Ledger.create_document`）
    也要用同一条规则；而 `invariants.py` 会 import 本模块，把规则写在那边会绕成环。
    """
    seen: set[str] = set()
    for block in blocks:
        if block.parent_id is not None:
            continue
        if block.schema_label in seen:
            raise ContractViolation(
                f"同一个文档里有重复的顶层字段：{block.schema_label}——M20 靠它定位，必须唯一"
            )
        seen.add(block.schema_label)


class DocumentVersion(ContractModel):
    """文档版本。只增不改，回退产生新版本（I8）。"""

    version_id: str
    doc_id: str
    seq: int = Field(ge=1)
    snapshot: DocumentSnapshot
    trigger: VersionTrigger
    group_id: str | None = None
    target_version_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "DocumentVersion":
        if self.trigger is VersionTrigger.SUBMIT:
            if self.group_id is None:
                raise ContractViolation("submit 版本必须带 group_id——'撤销某次 AI 写入'靠它定位")
        elif self.group_id is not None:
            raise ContractViolation("group_id 只有 submit 版本才有")

        if self.trigger is VersionTrigger.ROLLBACK:
            if self.target_version_id is None:
                raise ContractViolation("回退必须记下从哪一版退回来（AC8：回退本身也留痕）")
        elif self.target_version_id is not None:
            raise ContractViolation("target_version_id 只有 rollback 版本才有")
        return self


class SubmitPayload(ContractModel):
    """`writeDocument(trigger = submit, ...)` 的入参：M20 自己去黑板读这一组。"""

    group_id: str

    @model_validator(mode="after")
    def _check(self) -> "SubmitPayload":
        if not self.group_id:
            raise ContractViolation("提交写入必须指名是哪一组卡片")
        return self


class ManualEditPayload(ContractModel):
    """`writeDocument(trigger = manual, ...)` 的入参：一次显式保存 = 一版（AC8）。"""

    block_ops: tuple[BlockOp, ...]

    @model_validator(mode="after")
    def _check(self) -> "ManualEditPayload":
        if not self.block_ops:
            raise ContractViolation("一次保存至少要改一处")
        return self


class RollbackPayload(ContractModel):
    """`writeDocument(trigger = rollback, ...)` 的入参：回退产生新版本，不删不改历史（I8）。"""

    target_version_id: str

    @model_validator(mode="after")
    def _check(self) -> "RollbackPayload":
        if not self.target_version_id:
            raise ContractViolation("回退必须指名退回哪一版")
        return self


WritePayload = SubmitPayload | ManualEditPayload | RollbackPayload
"""三种 trigger 各自的 payload。配错由 `invariants.check_write_payload` 拒。"""
