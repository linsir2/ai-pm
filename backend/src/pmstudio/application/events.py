"""v1 事件**的名字**。事件本身（信封 + 协议 + 总线）已搬进 `pmstudio.harness.events`。

为什么名字留在这儿：事件名是业务词汇，而且它是**跨边界的契约**——
前端 `useEvents.ts` 硬编码这些名字订阅，`workspace.ts` 硬编码它们的
payload 键。改名的代价是"SSE 静默死亡但界面看起来健康"，所以

1. 这六个常量**一个都不许改**（`tests/application/test_ports_events_errors.py` 钉住集合）；
2. 载荷形状由 `tests/harness/test_events.py` 与前端契约测试钉住。

原来 `Event.__post_init__` 会拿这份清单做校验（不在清单里就抛"未定义的事件名"）——
那是通用信封反向依赖业务词汇，已随事件类一起搬走并删除。
"""

ROUND_UPDATED = "round.updated"
PACKET_EMITTED = "packet.emitted"
DOC_CHANGED = "doc.changed"
GENERATION_FAILED = "generation.failed"
#: 探索的状态变了（running/done/failed/abandoned）。payload 只有 id 与状态，
#: 真身一律从 `GET /projects/{id}/explorations` 读回（M8）。
EXPLORATION_UPDATED = "exploration.updated"
#: 讨论线程的状态变了（open/closed）。payload 只有 id 与状态，
#: 真身一律从 `GET /projects/{id}/discussions` 读回（M9）。
DISCUSSION_UPDATED = "discussion.updated"

EVENT_NAMES = frozenset(
    {
        ROUND_UPDATED,
        PACKET_EMITTED,
        DOC_CHANGED,
        GENERATION_FAILED,
        EXPLORATION_UPDATED,
        DISCUSSION_UPDATED,
    }
)
