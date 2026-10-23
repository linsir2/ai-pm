"""工具留样协议（写侧）：`application` 只认它，实现由 `bootstrap` 注入。

**为什么在 `harness`**：`ModelCallRecorder` 就在 `harness/model/contracts.py`，同构。
工具调用契约也不放进 `application/ports.py`——那是**产品侧**能力，而"留样"是
控制平面的事（`application → infrastructure` 分层守卫也不许反向依赖）。

**没有数据类**：`ModelCallRecorder` 协议与实现**都是 kwargs**，工具协议照抄这个形状
——再加一个 `ToolCallRecord` 就是同一件事的第二套形状（冗余）。
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class ToolCallRecorder(Protocol):
    """留样的旁路出口：记下每一次工具调用，给排查用，不参与业务。

    实现是 `infrastructure/sqlite/tool_calls.py`；网关/用例默认不接
    （`recorder=None` 时什么都不记）。
    """

    def record(
        self,
        *,
        trace_id: str | None,
        tool_id: str,
        arguments: Mapping[str, object],
        result_note: str,
        duration_ms: int,
        created_at: datetime,
        ok: bool = True,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None: ...
