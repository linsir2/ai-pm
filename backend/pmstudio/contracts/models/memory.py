"""C9 Memory（记忆）。

记忆分**候选**与**生效**两态——这是防止"一次错误反馈污染以后所有生成"的关键。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import MemoryStatus, MemoryType
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.citation import Citation


class Memory(ContractModel):
    """一条记忆。只有 `active` 能进常驻上下文。"""

    memory_id: str
    type: MemoryType
    content: str
    status: MemoryStatus
    citations: tuple[Citation, ...]

    @model_validator(mode="after")
    def _check(self) -> "Memory":
        if not self.memory_id:
            raise ContractViolation("memory_id 不能为空")
        if not self.content.strip():
            raise ContractViolation("记忆正文不能为空")
        if not self.citations:
            raise ContractViolation("每条记忆都必须有出处——无出处的记忆不许入库")
        return self
