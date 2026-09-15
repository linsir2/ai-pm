"""C2 Block / Document 与 C3 Document Version（文档与版本）。

用户手动编辑和 AI 写入共用同一条版本时间线。改动与回退的原子单位是一个文档版本；
回退产生新版本，不删不改历史（I8）。
"""

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


class BlockOp(ContractModel):
    """一次写入里对某个块的操作（CONTRACTS §5.3 `writeDocument` 的 payload 项）。"""

    block_id: str
    kind: BlockOpKind
    content: str
    expected_version: int = Field(ge=1)


class DocumentSnapshot(ContractModel):
    """某一版文档的完整内容。"""

    blocks: tuple[Block, ...]


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
