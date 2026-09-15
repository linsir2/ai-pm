"""M12 事件流 —— 唯一的一条总线。

它只做一件事：把状态变化通知给关心它的层。**事件不携带完整数据**（payload 是形状里的最小
定位信息），订阅者要细节就回黑板或存储读。

这里定死的四条语义（CONTRACTS §6.6 / C13）：

1. **异步、按订阅顺序 await**：一次 `publish` 要等整条链跑完才返回。
   所以订阅者里不要塞慢 IO（联网这类），否则一次发布就把整条链卡住。
2. **嵌套发布排队**：处理器里再 `publish` 只是入队，当前处理器返回之后才排空，同一队列 FIFO。
   这里说的"一轮"是**一次 publish 引发的整条分发**，与 `round_id` 无关。
3. **异常隔离**：一个订阅者抛异常不阻断其它订阅者；异常记进日志（stdout 那条 WARNING）。
4. **先落流水，再分发**：流水回答"这件事到底发了没有"，它不受订阅者成败影响；
   反过来，流水写不进去就不算发出去——当场炸在调用方，不静默丢事件。

两条边界的说明：

- **白名单**：`PRODUCERS` 是窄发布的依据，身份不对在落流水之前就拒。
- **流水里的轮次与项目**取自 payload：`doc.changed` / `registry.updated` 的 payload 里没有这两个
  定位信息，它们的流水行就是空的（这不是漏，是这两种变化本来就不绑某一轮）。
"""

import logging
from collections import deque

from pmstudio.common.errors import ContractViolation, EventDispatchOverflow
from pmstudio.common.ids import EVENT, IdGenerator, TimestampIdGenerator
from pmstudio.contracts.enums import EventType, ProducerIdentity
from pmstudio.contracts.interfaces.communication import EventHandler
from pmstudio.contracts.interfaces.storage import EventLogPort
from pmstudio.contracts.skeleton.events import PRODUCERS, Event, EventLogEntry

logger = logging.getLogger(__name__)

DEFAULT_MAX_DISPATCH_PER_PUBLISH = 1000


class EventBus:
    """订阅表 + 一个分发队列。整个系统只有这一个实例（由装配根造出来）。"""

    def __init__(
        self,
        log: EventLogPort,
        *,
        max_dispatch_per_publish: int = DEFAULT_MAX_DISPATCH_PER_PUBLISH,
        ids: IdGenerator | None = None,
    ) -> None:
        self._log = log
        self._max_dispatch = max_dispatch_per_publish
        self._ids = ids or TimestampIdGenerator()
        self._subscribers: dict[EventType, list[EventHandler]] = {}
        self._queue: deque[Event] = deque()
        self._draining = False

    async def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """订一个类型。同一个处理器订两次就会收到两条——去重是调用方的事。"""
        self._subscribers.setdefault(event_type, []).append(handler)

    async def publish(self, event: Event, *, producer: ProducerIdentity) -> None:
        """发一条事件。发布者必须是这个事件的生产者（C13 的白名单）。"""
        self._reject_foreign_producer(event.type, producer)
        await self._log.append(self._build_entry(event, producer))

        self._queue.append(event)
        if self._draining:
            return  # 嵌套发布：排队，等当前处理器返回后排空
        await self._drain()

    # ── 内部 ────────────────────────────────────────────────

    @staticmethod
    def _reject_foreign_producer(event_type: EventType, producer: ProducerIdentity) -> None:
        allowed = PRODUCERS[event_type]
        if producer not in allowed:
            names = "、".join(sorted(identity.value for identity in allowed))
            raise ContractViolation(f"{event_type.value} 只能由 {names} 发，收到 {producer.value}")

    def _build_entry(self, event: Event, producer: ProducerIdentity) -> EventLogEntry:
        # payload 里有什么定位信息就记什么：这两列只是检索用的索引，不是必填。
        payload = event.payload
        return EventLogEntry(
            event_id=self._ids.new_id(EVENT),
            at=event.at,
            type=event.type,
            producer=producer,
            payload=payload,  # 这一步顺带校验 payload 与 type 是否配对
            round_id=getattr(payload, "round_id", None),
            project_id=getattr(payload, "project_id", None),
        )

    async def _drain(self) -> None:
        self._draining = True
        try:
            dispatched = 0
            while self._queue:
                event = self._queue.popleft()
                for handler in tuple(self._subscribers.get(event.type, ())):
                    dispatched += 1
                    if dispatched > self._max_dispatch:
                        self._queue.clear()  # 别把已经堆起来的环留给下一次
                        raise EventDispatchOverflow(
                            f"一次发布分发了 {dispatched} 条，超过上限 {self._max_dispatch}"
                            "——检查订阅者之间是不是形成了环"
                        )
                    await self._invoke(handler, event)
        finally:
            self._draining = False

    @staticmethod
    async def _invoke(handler: EventHandler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            # 异常隔离：一个订阅者出错，不让"记忆失效"这种链条整段跑不掉。
            logger.warning("订阅者处理 %s 时抛异常，已隔离", event.type.value, exc_info=True)
