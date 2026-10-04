"""事件信封与两个投递协议（搬自 `application/events.py` 与 `application/ports.py`）。

**为什么不泛型化**：总线与 `Event` 现在是同层的兄弟模块，直接 import 就够了，
不需要 `TypeVar`——搬进 harness 的最大收益就是不必再绕开"Event 住在 application"这件事。
"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Event:
    """一条"发生过的事"。它只承诺自洽（序号非负），**不承诺这种通知该不该存在**。"""

    name: str
    project_id: str
    occurred_at: datetime
    seq: int = 0  # 由事件总线在 publish 时盖真序号；业务侧一律用默认值
    round_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.seq < 0:
            raise ValueError(f"事件序号不能是负数：{self.seq}")


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, event: Event) -> None: ...


@runtime_checkable
class EventStream(Protocol):
    def subscribe(self) -> AsyncIterator[Event]: ...
