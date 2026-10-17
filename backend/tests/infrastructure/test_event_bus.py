import asyncio
from datetime import UTC, datetime

from pmstudio.application.events import ROUND_UPDATED
from pmstudio.harness.events import Event, InProcessEventBus

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def make_event() -> Event:
    return Event(name=ROUND_UPDATED, project_id="p1", occurred_at=NOW)


async def test_bus_delivers_in_publish_order():
    """总线保证**顺序**。**不保证序号**——`seq` 字段 2026-10-17（P4）删掉了：
    它是逐进程自增的，而前端拿它当全局水位，多 worker 下会丢事件。"""

    bus = InProcessEventBus()

    async def reader() -> list[Event]:
        seen: list[Event] = []
        async for event in bus.subscribe():
            seen.append(event)
            if len(seen) == 2:
                return seen
        return seen

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)  # 让订阅者先注册
    bus.publish(make_event())
    bus.publish(make_event())

    events = await asyncio.wait_for(task, timeout=1)
    assert len(events) == 2
    assert events[0] == events[1] == make_event()


async def test_bus_delivers_to_every_subscriber():
    bus = InProcessEventBus()
    first: list[Event] = []
    second: list[Event] = []

    async def reader(sink: list[Event]) -> None:
        async for event in bus.subscribe():
            sink.append(event)
            return

    tasks = [asyncio.create_task(reader(first)), asyncio.create_task(reader(second))]
    await asyncio.sleep(0)
    bus.publish(make_event())
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert len(first) == 1
    assert len(second) == 1


async def test_bus_unsubscribes_when_reader_stops():
    bus = InProcessEventBus()

    async def reader() -> None:
        async for _ in bus.subscribe():
            return

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)  # 先订阅
    bus.publish(make_event())  # 收到一条才可能"停下来"
    await asyncio.wait_for(task, timeout=1)
    await asyncio.sleep(0)
    assert bus.subscriber_count == 0


async def test_publish_never_blocks_when_a_subscriber_is_silent():
    """摘掉订阅者流程照样跑完：慢订阅者不许拖住发布方。"""

    bus = InProcessEventBus(queue_size=2)

    async def reader() -> None:
        async for _ in bus.subscribe():
            await asyncio.sleep(0)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0)
    for _ in range(5):  # 塞爆队列也不许抛异常
        bus.publish(make_event())

    task.cancel()
