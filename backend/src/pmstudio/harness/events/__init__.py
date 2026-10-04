"""运行时事件：信封、两个协议、进程内总线。

**它不管事件叫什么**。事件名（`round.updated` / `packet.emitted` / …）是业务词汇，
住在 `pmstudio.application.events`；这里只保证"这条通知自洽、投递不阻塞发布方"。
原来 `Event.__post_init__` 会拿业务事件名做校验并抛"未定义的事件名"——那是通用信封
反向依赖业务词汇，本层搬出来时一并删掉（前端订阅清单才是事件名的权威）。
"""

from pmstudio.harness.events.bus import DEFAULT_QUEUE_SIZE, InProcessEventBus
from pmstudio.harness.events.schemas import Event, EventPublisher, EventStream

__all__ = ["DEFAULT_QUEUE_SIZE", "Event", "EventPublisher", "EventStream", "InProcessEventBus"]
