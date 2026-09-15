"""L6 Harness 对外暴露什么：模型、权限、预算与裁剪、观测。"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pmstudio.contracts.enums import Permission, RoundEntry
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.results import TrimResult
from pmstudio.contracts.models.trace import Trace
from pmstudio.contracts.skeleton.board import ContextBlock


@runtime_checkable
class HarnessPort(Protocol):
    """模型接入（M22）、权限（M23）、预算与裁剪（M24）、观测（M25）的对外面。"""

    async def complete(self, model_ref: str, messages: Sequence[PromptMessage]) -> str:
        """一次模型调用；重试与降级在里面（M26）。"""
        ...

    async def can_use(self, role_id: str, tool_id: str) -> Permission:
        """执行 C10 的 `allowed_tools` 白名单。声明在 L0，执行在这里。"""
        ...

    async def can_enter(self, role_id: str, entry: RoundEntry) -> Permission:
        """执行 C10 的 `allowed_entries` 白名单。"""
        ...

    async def trim(self, blocks: Sequence[ContextBlock], project_id: str, model_ref: str) -> TrimResult:
        """按预算裁剪。**预算由 L6 自己算**（模型窗口 − 输出预留 − 系统开销，叠 C16 的 config），
        调用方不传；**只按 `priority` 裁**，不做语义判断。"""
        ...

    async def record_trace(self, trace: Trace) -> None:
        """M25 落一轮的 trace（含主张留痕）。"""
        ...
