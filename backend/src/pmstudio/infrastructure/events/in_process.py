"""进程内事件总线：只做通知与投影。

- 事件在事务提交后发布（用例负责）；
- 总线给事件盖 `seq`（投递侧的事）；
- 订阅者队列满了丢最旧的并记 warning——绝不阻塞发布方，
  因为"摘掉所有订阅者流程照样跑完"是设计里的硬规矩（`05` §12.2）。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import replace

from pmstudio.application.events import Event

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 256


class InProcessEventBus:
    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: list[asyncio.Queue[Event]] = []
        self._seq = 0

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: Event) -> None:
        self._seq += 1
        stamped = replace(event, seq=self._seq)
        for queue in list(self._subscribers):
            if queue.full():
                logger.warning("事件订阅者队列已满，丢弃最旧事件：%s", stamped.name)
                queue.get_nowait()
            queue.put_nowait(stamped)

    async def subscribe(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)
