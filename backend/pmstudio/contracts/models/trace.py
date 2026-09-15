"""C11 Trace（观测）。

一轮用了哪些上下文（含被裁掉的）、调了哪些工具、最后写进了哪一版、说过哪些主张。
**主张留痕**是"这个分歧怎么来的"唯一的出口——没有它，C4 的主张在轮末就随黑板一起没了。

留痕的形状归 C11 所有，不是 C4 的形状：契约层不 import 编排层（directory.md §3.1）。
"""

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import ClaimKind
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.skeleton.board import ContextRegion


class ClaimDigest(ContractModel):
    """一条主张在复盘里留下的东西：谁说的、说了什么、依据什么。"""

    packet_id: str
    claim_id: str
    role_id: str
    statement: str
    kind: ClaimKind
    citations: tuple[Citation, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check(self) -> "ClaimDigest":
        if not self.packet_id or not self.claim_id or not self.role_id:
            raise ContractViolation("主张留痕必须记住是哪条主张、谁说的")
        if not self.statement.strip():
            raise ContractViolation("主张留痕不能是一句空话")
        return self


class Trace(ContractModel):
    """一轮的复盘材料。"""

    trace_id: str
    round_id: str
    context_used: ContextRegion
    tools_called: tuple[str, ...] = Field(default_factory=tuple)
    final_version_id: str | None = None
    claims: tuple[ClaimDigest, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check(self) -> "Trace":
        if not self.trace_id:
            raise ContractViolation("trace_id 不能为空")
        if not self.round_id:
            raise ContractViolation("trace 必须记住是哪一轮——复盘入口就是它")
        return self
