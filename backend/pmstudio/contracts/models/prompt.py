"""提示词的形状（`complete(model_ref, messages)` 的入参项）。

它不是 C 编号契约，但是**跨层刚需**：编排层产、Harness 用。不定它，这道接缝就是无类型的
（CONTRACTS §5.3 那条"入参和返回值只能是契约对象"）。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import PromptRole
from pmstudio.contracts.models.base import ContractModel


class PromptMessage(ContractModel):
    """一条提示词消息。"""

    role: PromptRole
    text: str

    @model_validator(mode="after")
    def _check(self) -> "PromptMessage":
        if not self.text.strip():
            raise ContractViolation("提示词消息不能为空")
        return self
