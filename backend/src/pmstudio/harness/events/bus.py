"""进程内事件总线：只做通知与投影。

- 事件在事务提交后发布（用例负责）；
- 订阅者队列满了丢最旧的并记 warning——绝不阻塞发布方，
  因为"摘掉所有订阅者流程照样跑完"是设计里的硬规矩（`05-DDD架构.md §12.2`）。
"""

import asyncio
import logging
from collections.abc import AsyncIterator

from pmstudio.harness.events.schemas import Event

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_SIZE = 256


class InProcessEventBus:
    def __init__(self, *, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        self._queue_size = queue_size
        self._subscribers: list[asyncio.Queue[Event]] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: Event) -> None:
        """投给所有本地订阅者。**同步、不阻塞发布方**。"""

        for queue in list(self._subscribers):
            if queue.full():
                logger.warning("事件订阅者队列已满，丢弃最旧事件：%s", event.name)
                queue.get_nowait()
            queue.put_nowait(event)

    async def subscribe(self) -> AsyncIterator[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)
