"""C17 Conversation（会话、消息与滚动摘要）。

M15 的家。消息是"面向界面说过的话"，滚动摘要是"历史太长之后压出来的那段"。
**不保存内部思考过程**（I12）。摘要不是删除：原文还在消息表里。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.models.base import ContractModel


class Message(ContractModel):
    """一条面向界面的消息。引用一律认 `round_id`（C15 的 `user_input` 档写的就是它）。"""

    message_id: str
    project_id: str
    round_id: str
    author: str
    text: str
    packet_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Message":
        if not self.message_id:
            raise ContractViolation("message_id 不能为空")
        if not self.project_id:
            raise ContractViolation("消息必须记住属于哪个项目")
        if not self.round_id:
            raise ContractViolation("消息必须记住是哪一轮说的")
        if not self.author:
            raise ContractViolation("消息必须记住是谁说的")
        if not self.text.strip():
            raise ContractViolation("消息正文不能为空")
        return self


class Summary(ContractModel):
    """压出来的一段历史。`covers_round_ids` 盖住的原文仍然在消息表里。"""

    summary_id: str
    project_id: str
    covers_round_ids: tuple[str, ...]
    text: str

    @model_validator(mode="after")
    def _check(self) -> "Summary":
        if not self.summary_id:
            raise ContractViolation("summary_id 不能为空")
        if not self.project_id:
            raise ContractViolation("摘要必须记住属于哪个项目")
        if not self.covers_round_ids:
            raise ContractViolation("摘要必须说清盖住了哪些轮次")
        if not self.text.strip():
            raise ContractViolation("摘要正文不能为空")
        return self
