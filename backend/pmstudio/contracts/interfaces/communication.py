"""L3 通信层（M11 黑板 / M12 事件流）对外暴露什么。

两半：**状态**（黑板）与**通知**（事件）。黑板自己不派发——通知归事件管。

读写分开两个 Protocol，是 I19（区块单写者）的类型表达：`Board` 对所有层开放，
`BoardWriter` 只发给编排层。
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from pmstudio.contracts.enums import EventType, ProducerIdentity, RegionName
from pmstudio.contracts.skeleton.board import RegionValue
from pmstudio.contracts.skeleton.events import Event

EventHandler = Callable[[Event], Awaitable[None]]


@runtime_checkable
class Board(Protocol):
    """黑板只读句柄。所有层都能调。"""

    async def read(self, region: RegionName) -> RegionValue | None:
        """读当前轮的一个区块。**还没写过就是 `None`**——开轮时只有 `round` 有值。"""
        ...


@runtime_checkable
class BoardWriter(Protocol):
    """黑板写句柄。**只有编排层拿得到它**（I19）。"""

    async def open_round(self, round_id: str) -> None:
        """把这一轮设为黑板当前的轮，并清掉其它轮残留的区块（C12：用户开新一轮时再删）。"""
        ...

    async def write(self, region: RegionName, value: RegionValue) -> None: ...

    async def drop_round(self) -> None:
        """轮末清理：删掉当前轮的区块；`rounds` 那一行长期保留。"""
        ...


@runtime_checkable
class EventBus(Protocol):
    """唯一的一条总线。发布要带身份，白名单在 `events.PRODUCERS`。"""

    async def publish(self, event: Event, *, producer: ProducerIdentity) -> None: ...

    async def subscribe(self, event_type: EventType, handler: EventHandler) -> None: ...
