"""层间接口的返回形状。

都不是 C 编号契约，但都是跨层的刚需返回——不定它们，接口的返回就只能是一句"上下文块数组"。
"""

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.document import Block, DocumentVersion
from pmstudio.contracts.skeleton.board import ContextBlock


class AssembleResult(ContractModel):
    """M13 `assembleContext` 的返回：上下文块 + 这一轮的版本快照。"""

    blocks: tuple[ContextBlock, ...] = Field(default_factory=tuple)
    base_versions: dict[str, int] = Field(default_factory=dict)


class TrimResult(ContractModel):
    """M24 `trim` 的返回：留下的与被裁掉的。"""

    blocks: tuple[ContextBlock, ...] = Field(default_factory=tuple)
    dropped: tuple[ContextBlock, ...] = Field(default_factory=tuple)


class CreateProjectResult(ContractModel):
    """建项目（应用函数）的返回。"""

    project_id: str
    doc_id: str

    @model_validator(mode="after")
    def _check(self) -> "CreateProjectResult":
        if not self.project_id or not self.doc_id:
            raise ContractViolation("建项目必须同时给出项目与文档")
        return self


class WriteDocumentResult(ContractModel):
    """`writeDocument` 的返回：更新后的块 + 新产生的那一版。"""

    blocks: tuple[Block, ...] = Field(default_factory=tuple)
    version: DocumentVersion
