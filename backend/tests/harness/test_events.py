"""事件信封与总线搬进 harness 后的形状。

**这一层为什么该独立**：`Event` 是"运行时怎么把发生过的事通知出去"的载体，
与"产品经理写产品文档"无关。但它原来住在 `application/events.py`，而且
`__post_init__` 用 PM 的四个事件名做校验——**通用信封反向依赖业务词汇**。
那是本项目唯一一个"通用件依赖业务词"的地方，本文件就是它的拆除证明。

事件名仍然住在 application（那是业务词汇），并且**一个都不许改**：
前端 `useEvents.ts:10-15` 硬编码订阅、`workspace.ts:113-190` 硬编码 payload 键，
且当时零测试——改名的后果是"SSE 静默死亡但顶栏显示已连上"。
"""

import asyncio

from pmstudio.harness.events import (
    DEFAULT_QUEUE_SIZE,
    Event,
    EventPublisher,
    EventStream,
    InProcessEventBus,
)

NOW = None  # 具名常量在下面赋值，避免 import 期就构造 datetime


def an_event(name: str = "round.updated", **overrides: object) -> Event:
    from datetime import UTC, datetime

    base: dict[str, object] = {
        "name": name,
        "project_id": "p1",
        "occurred_at": datetime(2026, 10, 4, tzinfo=UTC),
        "round_id": "r1",
        "payload": {"step": "seats_running"},
    }
    base.update(overrides)
    return Event(**base)  # type: ignore[arg-type]


# —— 事件名不再由信封自己认 ——


def test_any_event_name_is_accepted() -> None:
    """`__post_init__` 曾经拿业务事件名做校验并抛"未定义的事件名"。

    那是通用信封反向依赖业务词汇：信封该管"这条通知自洽吗"，不管"这种通知该不该存在"。
    事件名的**权威**是 application 侧那四个常量 + 前端的订阅清单。
    """

    assert an_event("round.updated").name == "round.updated"
    assert an_event("doc.changed").name == "doc.changed"
    assert an_event("完全没注册过的名字").name == "完全没注册过的名字"


def test_事件上不再有_seq_字段() -> None:
    """**2026-10-17（P4）删除 `seq`**。

    它是逐进程自增的，而前端 `workspace.ts` 拿它当**全局水位**——多 worker 下
    会把大约 3/4 的事件当成重复而丢掉。它防的"重复投递"在本架构里不可能发生
    （进程内队列与 Redis pub/sub 都是 at-most-once）。
    """

    assert "seq" not in Event.__dataclass_fields__


def test_owner_id_业务侧一律留空_由总线盖() -> None:
    """owner 是**投递判据**：业务侧盖章要透传 41 处，而分层守卫规定
    application 根本 import 不到 `owner_scope`。"""

    assert an_event().owner_id is None
    assert "owner_id" in Event.__dataclass_fields__



# —— 总线：只做通知，绝不阻塞 ——


async def test_the_bus_delivers_in_publish_order() -> None:
    """总线保证**顺序**（这才是订阅端唯一需要的性质），不保证序号。

    注意 `subscribe()` 是**惰性异步生成器**：不先让它跑到 `await`，订阅者还没注册，
    先 publish 的两条会进一个不存在的队列——所以下面用 reader 协程 + `sleep(0)`。
    """

    bus: InProcessEventBus = InProcessEventBus()

    async def reader() -> list[Event]:
        seen: list[Event] = []
        async for event in bus.subscribe():
            seen.append(event)
            if len(seen) == 2:
                return seen
        return seen

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)  # 让订阅者先注册
    bus.publish(an_event("a"))
    bus.publish(an_event("b"))

    events = await asyncio.wait_for(task, timeout=1)
    assert [event.name for event in events] == ["a", "b"]


async def test_a_full_subscriber_queue_drops_the_oldest_and_never_blocks() -> None:
    """订阅者队列满了丢最旧的并记 warning——**绝不阻塞发布方**。

    这是"事件只做通知"的兜底：发布方永远不该因为某个界面卡住而停住。
    """

    bus: InProcessEventBus = InProcessEventBus(queue_size=2)

    async def reader() -> list[str]:
        seen: list[str] = []
        async for event in bus.subscribe():
            seen.append(event.name)
            if len(seen) == 2:
                return seen
        return seen

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    for index in range(5):  # 队列只有 2 格，发布 5 条
        bus.publish(an_event(f"e{index}"))

    kept = await asyncio.wait_for(task, timeout=1)
    assert kept == ["e3", "e4"], "满了要丢最旧的，不是丢最新的"


async def test_the_run_completes_with_every_subscriber_gone() -> None:
    """`05-DDD架构.md §12.2` 的判据固化成测试：摘掉全部订阅者，发布照旧不报错。"""

    bus: InProcessEventBus = InProcessEventBus()
    assert bus.subscriber_count == 0
    assert bus.publish(an_event()) is None  # 旧口径：publish 返回 None，不抛就是通过
    assert bus.subscriber_count == 0


def test_the_bus_satisfies_both_publisher_and_stream_protocols() -> None:
    bus = InProcessEventBus()
    assert isinstance(bus, EventPublisher)
    assert isinstance(bus, EventStream)


def test_the_default_queue_size_is_a_documented_constant() -> None:
    assert DEFAULT_QUEUE_SIZE == 256
