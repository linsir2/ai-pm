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
    """一条"发生过的事"。**不承诺这种通知该不该存在**——它只是"去重读"的信号。

    **`owner_id` 由总线在 publish 时盖，业务侧一律留空**：owner 是**投递判据**，
    与 seq 曾经同类。业务侧要填就得把 owner 透传进 37 个调用点，而分层守卫
    （`ALLOWED_LAYER_IMPORTS`）规定 application 根本 import 不到 `owner_scope`。
    留空**不等于**"谁都能看"——`api/sse.py` 的 `owner_visible` 会回落到查库判定。

    **曾经有过 `seq` 字段，2026-10-17（P4）删除**：它是逐进程自增的，而前端
    `workspace.ts` 拿它当**全局水位**——4 个 worker 下会把大约 3/4 的事件当成
    重复而丢掉。它防的"重复投递"在本架构里不可能发生（进程内队列与 Redis
    pub/sub 都是 at-most-once），而留着它的两条路各自引入更严重的新失效：
    Redis `INCR` 计数器遇 Redis 重启归零会把客户端的 `lastSeq` "饿死"；
    用服务端时钟派生则同微秒碰撞会丢一条。详见计划文档 P4 段。
    """

    name: str
    project_id: str
    occurred_at: datetime
    owner_id: str | None = None  # 总线盖；业务侧留空
    round_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, event: Event) -> None: ...


@runtime_checkable
class EventStream(Protocol):
    def subscribe(self) -> AsyncIterator[Event]: ...
