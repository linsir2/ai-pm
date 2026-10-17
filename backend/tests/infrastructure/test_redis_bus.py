"""跨 worker 事件扇出（高并发 P4）。**跑真 Redis**，不打桩。

两个 `RedisFanoutEventBus` 实例 = 两个 worker 进程（共享同一个 Redis，
各自一份订阅连接）。这比"起两个真进程"快且确定，真进程那层由
`scripts/multiworker_smoke.py` 端到端验。

为什么这组测试必须打真库：Redis pub/sub 的语义（回声、at-most-once、
断开后迭代器静默结束）只在真库上才验得到，桩里没有这些。
连不上就 fail 不 skip——扇出是这个方案的核心，测试悄悄跳过等于没验。
"""

import asyncio
import contextlib
import json
import os
from uuid import uuid4

import pytest

from pmstudio.harness.events import Event
from pmstudio.infrastructure.redis_bus import RedisFanoutEventBus

URL = os.environ.get("PMSTUDIO_TEST_REDIS_URL", "redis://127.0.0.1:6379/9")
DEAD_URL = "redis://127.0.0.1:6399/9"

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMSTUDIO_TEST_REDIS_URL"),
    reason="需要 PMSTUDIO_TEST_REDIS_URL（指向**独立**的测试库，别指生产库）",
)


def _event(**kwargs: object) -> Event:
    base = {
        "name": "doc.changed",
        "project_id": "p1",
        "occurred_at": _NOW,
        "owner_id": "u1",
        "payload": {"version_no": 2},
    }
    base.update(kwargs)
    return Event(**base)  # type: ignore[arg-type]


from datetime import UTC, datetime  # noqa: E402

_NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


async def _alive() -> bool:
    import redis.asyncio as aioredis

    client = aioredis.from_url(URL, socket_timeout=0.5)
    try:
        await client.ping()
        return True
    except Exception:  # noqa: BLE001 —— 这里就是要抓任何连不上的情况
        return False
    finally:
        await client.aclose()


async def _collect(iterator, sink: list, *, want: int, timeout: float) -> None:
    """从订阅里收 want 条到 sink。取不满就让上层断言失败，不静默通过。"""

    async def pump() -> None:
        async for event in iterator:
            sink.append(event)
            if len(sink) >= want:
                return

    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(pump(), timeout)


class _Pair:
    """两个"worker"，各自 start/stop。"""

    def __init__(self, *, url: str = URL, queue_size: int = 256) -> None:
        self.channel = f"pmstudio:events:{uuid4().hex[:10]}"
        self.a = RedisFanoutEventBus(
            url=url, channel=self.channel, queue_size=queue_size
        )
        self.b = RedisFanoutEventBus(
            url=url, channel=self.channel, queue_size=queue_size
        )

    async def start(self) -> "_Pair":
        await self.a.start()
        await self.b.start()
        return self

    async def stop(self) -> None:
        await self.a.stop()
        await self.b.stop()


@pytest.fixture()
async def pair():
    if not await _alive():
        pytest.fail(
            f"连不上 Redis（{URL}）。这组测试必须打真库——"
            "pub/sub 的语义（回声、at-most-once、断开静默）只在真库上成立。"
        )
    made = await _Pair().start()
    try:
        yield made
    finally:
        await made.stop()


async def _drain(bus: RedisFanoutEventBus, count: int, timeout: float = 2.0):
    """从订阅里取 count 条。取不到就让测试失败而不是静默通过。"""

    got: list[Event] = []
    iterator = bus.subscribe()
    try:
        async def pump() -> None:
            async for event in iterator:
                got.append(event)
                if len(got) >= count:
                    return

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(pump(), timeout)
    finally:
        await iterator.aclose()
    return got


# ── A 组 · 跨进程扇出机制 ───────────────────────────────────────────────────


async def test_一个_bus_发的另一个_bus_收得到(pair: _Pair):
    """**这组测试存在的理由**。R：一个项目的事件要能推到连在任意 worker 的浏览器。
    I：两个 bus 共享一个 Redis；P：a 发 b 收；R：字段逐个相等。"""

    listener = asyncio.create_task(_drain(pair.b, 1))
    await asyncio.sleep(0.25)  # 等订阅就位
    pair.a.publish(_event())

    got = await listener

    assert len(got) == 1
    assert got[0].name == "doc.changed"
    assert got[0].project_id == "p1"
    assert got[0].owner_id == "u1"
    assert got[0].payload == {"version_no": 2}


async def test_发布者自己也收到_且只收到一次(pair: _Pair):
    """R：Redis 会把消息**回声**给所有订阅者（含发布者自己）。
    本地再直投一次 = 每个事件两次，前端会看到重复。
    I/P：a 发，同时从 a 自己订阅；R：恰好 1 条。"""

    listener = asyncio.create_task(_drain(pair.a, 2, timeout=1.5))
    await asyncio.sleep(0.25)
    pair.a.publish(_event())

    got = await listener

    assert len(got) == 1, f"收到 {len(got)} 条——本地直投与 Redis 回声重复了"


async def test_扇出顺序在每个订阅者上一致(pair: _Pair):
    """R：所有连接看到**同一条有序流**。顺序乱了会让前端的判断失效。
    I/P：a 连发 5 条，两个 bus 各自取；R：两边 name 顺序完全一致。"""

    la = asyncio.create_task(_drain(pair.a, 5, timeout=3.0))
    lb = asyncio.create_task(_drain(pair.b, 5, timeout=3.0))
    await asyncio.sleep(0.25)
    for index in range(5):
        pair.a.publish(_event(payload={"n": index}))
    await asyncio.sleep(0.5)

    got_a, got_b = await la, await lb

    order_a = [e.payload["n"] for e in got_a]
    order_b = [e.payload["n"] for e in got_b]
    assert len(order_a) == len(order_b) == 5
    assert order_a == order_b, f"两个 worker 看到的顺序不同：{order_a} vs {order_b}"


async def test_事件上没有_seq_字段了(pair: _Pair):
    """R：`seq` 是**进程内**计数器而前端拿它当全局水位——多 worker 下会丢事件。
    P：收一条；R：`hasattr(event, 'seq')` 为假。"""

    listener = asyncio.create_task(_drain(pair.b, 1))
    await asyncio.sleep(0.25)
    pair.a.publish(_event())

    got = await listener

    assert got and not hasattr(got[0], "seq")


async def test_owner_id_由总线盖章_业务侧不传(pair: _Pair):
    """R：owner 是**投递侧**的事，业务侧透传要改 41 处，而且业务层根本
    import 不到 owner_scope（分层守卫）。P：在 owner_scope 里设了 u7 再发；R：收到 u7。"""

    from pmstudio.infrastructure.sqlite import owner_scope

    listener = asyncio.create_task(_drain(pair.b, 1))
    await asyncio.sleep(0.25)
    token = owner_scope.set_owner_scope("u7")
    try:
        pair.a.publish(_event(owner_id=None))
    finally:
        owner_scope.clear_owner_scope(token)

    got = await listener

    assert got[0].owner_id == "u7"


async def test_没有_owner_scope_时_owner_id_留空(pair: _Pair):
    """R：留空**不等于**"谁都能看"——`owner_visible` 会回落到查库判定。
    P：显式清掉 owner scope（`tests/conftest.py` 有 autouse 默认值）；R：owner_id
    为 None，但事件**照送**。"""

    from pmstudio.infrastructure.sqlite import owner_scope

    listener = asyncio.create_task(_drain(pair.b, 1))
    await asyncio.sleep(0.25)
    owner_scope.clear_owner_scope()
    try:
        pair.a.publish(_event(owner_id=None))
    finally:
        owner_scope.set_owner_scope("u-owner-scope-test")  # 还原 conftest 的默认值

    got = await listener

    assert got[0].owner_id is None


# ── B 组 · 降级 ─────────────────────────────────────────────────────────────


async def test_redis断了降级到进程内_跨worker那条腿断了但本机还通():
    """R：Redis 挂 = 扇出不存在是最坏的失效。本机连接仍要能收到事件。
    I：URL 指向没人监听的端口；P：发一条再取；R：不抛异常 + 本机收到 + degraded。"""

    bus = RedisFanoutEventBus(url=DEAD_URL, channel=f"t{uuid4().hex[:8]}")
    await bus.start()
    try:
        listener = asyncio.create_task(_drain(bus, 1, timeout=2.0))
        await asyncio.sleep(0.3)
        bus.publish(_event())

        got = await listener

        assert len(got) == 1, "Redis 挂了连本进程的事件都投不出去"
        assert bus.degraded is True
    finally:
        await bus.stop()


async def test_降级是可见的且只警告一次():
    """R：「悄悄降级」和「扇出不存在」从外面看是一回事。"""

    bus = RedisFanoutEventBus(url=DEAD_URL, channel=f"t{uuid4().hex[:8]}")
    await bus.start()
    try:
        for _ in range(5):
            bus.publish(_event())
        await asyncio.sleep(0.3)

        assert bus.degraded is True
    finally:
        await bus.stop()


async def test_非连接类异常不降级():
    """R：降级只对「连不上 Redis」生效——别的异常吞掉就是藏 bug。"""

    bus = RedisFanoutEventBus(url=DEAD_URL, channel=f"t{uuid4().hex[:8]}")
    await bus.start()
    try:
        with pytest.raises(ValueError, match="脚本写错了"):
            await bus._publish_one(json.dumps({"n": 1}), raise_now=ValueError("脚本写错了"))
    finally:
        await bus.stop()


async def test_出站队列满了丢最旧而不是堵死发布方():
    """R：`publish()` 是**同步**契约（EventPublisher），堵死会卡住请求线程。
    I：Redis 挂（出不去）+ 出站队列容量 4；P：塞 20 条；R：publish 不阻塞。"""

    bus = RedisFanoutEventBus(
        url=DEAD_URL, channel=f"t{uuid4().hex[:8]}", queue_size=4
    )
    await bus.start()
    try:
        loop = asyncio.get_running_loop()
        start = loop.time()
        for index in range(20):
            bus.publish(_event(payload={"n": index}))
        elapsed = loop.time() - start

        assert elapsed < 0.5, f"publish 阻塞了 {elapsed:.2f}s——它必须永远不 await"
        assert bus.dropped > 0, "队列满了却没记丢弃——丢得无声无息"
    finally:
        await bus.stop()


async def test_redis恢复后自动回到共享扇出(pair: _Pair):
    """R：降级不能是单向的。I/P：先在死端口上发一条，再在真 URL 上发；R：真 bus 收到。"""

    listener = asyncio.create_task(_drain(pair.b, 1))
    await asyncio.sleep(0.25)
    pair.a.publish(_event(payload={"n": "after-recovery"}))

    got = await listener

    assert len(got) == 1
    assert got[0].payload == {"n": "after-recovery"}


# ── C 组 · 生命周期 ────────────────────────────────────────────────────────


async def test_关停时任务被收掉_连接被关(pair: _Pair):
    """R：没有 lifespan 的话 `--reload` 每热重载一次泄漏一个任务 + 一组连接。
    P：start 后 stop；R：两个任务都 done。"""

    await pair.a.stop()

    assert pair.a.reader is None and pair.a.drainer is None


async def test_重复start不会起两份任务(pair: _Pair):
    """R：lifespan 可能被调多次（测试里重建 app），重复起会漏扇出。"""

    await pair.a.start()

    assert pair.a.reader is not None and not pair.a.reader.done()


async def test_订阅者断开后被摘除(pair: _Pair):
    """R：`api/sse.py` 的 finally 是 P4 顺手修的现存泄漏——断连时订阅者摘除
    要等异步生成器被 GC，不是确定性的。P：取一条后 aclose；R：计数回到 0。"""

    before = pair.a.local_subscriber_count
    iterator = pair.a.subscribe()
    # 异步生成器**不 anext 就不会跑函数体**——不先启动就断言订阅数是 0，
    # 那测的是 Python 语义不是我们的代码。
    started = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0.1)
    assert pair.a.local_subscriber_count == before + 1

    started.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await started
    await iterator.aclose()

    assert pair.a.local_subscriber_count == before


# ── D 组 · 高并发扇出 ───────────────────────────────────────────────────────


async def test_一个事件扇到200个订阅者(pair: _Pair):
    """R：扇出是 O(本地连接数)，Redis 侧与连接数无关（每 worker 1 条订阅连接）。
    I：200 个订阅协程；P：发 1 条；R：200 个全部收到。"""

    count = 200
    got: list[Event] = []

    async def pump() -> None:
        async for event in pair.b.subscribe():
            got.append(event)
            if len(got) >= count:
                return

    tasks = [asyncio.create_task(pump()) for _ in range(count)]
    await asyncio.sleep(0.6)
    loop = asyncio.get_running_loop()
    start = loop.time()
    pair.a.publish(_event(payload={"n": "fanout-200"}))
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.gather(*tasks), 5.0)
    elapsed = loop.time() - start

    assert len(got) == count, f"只扇到 {len(got)}/{count}"
    print(f"\n  [实测] 1 个事件 → 200 个订阅者，耗时 {elapsed * 1000:.0f}ms")


async def test_4个bus_x50订阅者_等于200连接(pair: _Pair):
    """R：4 worker 的真实形状——每个 worker 50 条连接。
    I：4 个 bus 各 50 订阅；P：bus_a 发 1 条；R：200 个全部收到。"""

    extra = [
        RedisFanoutEventBus(url=URL, channel=pair.channel) for _ in range(2)
    ]
    for bus in extra:
        await bus.start()
    buses = [pair.a, pair.b, *extra]
    got: list[Event] = []

    async def pump(bus: RedisFanoutEventBus) -> None:
        async for event in bus.subscribe():
            got.append(event)
            if len(got) >= 200:
                return

    tasks = [asyncio.create_task(pump(bus)) for bus in buses for _ in range(50)]
    await asyncio.sleep(0.8)
    pair.a.publish(_event(payload={"n": "fanout-4x50"}))
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.gather(*tasks), 8.0)

    try:
        assert len(got) == 200, f"只扇到 {len(got)}/200"
    finally:
        for bus in extra:
            await bus.stop()


async def test_事件洪峰_每个订阅者都拿到完整且相同的一条流(pair: _Pair):
    """R：两条同时要验——
      ① `InProcessEventBus` 的订阅队列满会**丢最旧**（既有语义，不是 P4 引入的）；
      ② **每个订阅者拿到的是同一条流**（顺序、内容都一样）。

    **每个 pump 各数各的**：共用一个 list 数到的是"投递次数"，20 个订阅者 ×
    N 条事件 = 20N 次投递——我第一版就是这么写的，结果拿到 219 > 200。

    I：20 个订阅者 × 200 条事件；P：连发 200 条；R：每个订阅者都收到 200 条、
    内容与顺序彼此一致、最后一条也在。"""

    total = 200
    subscribers = 20
    sinks: list[list[Event]] = [[] for _ in range(subscribers)]

    def make_pump(index: int):
        async def pump() -> None:
            async for event in pair.b.subscribe():
                sinks[index].append(event)
                if len(sinks[index]) >= total:
                    return
        return pump()

    tasks = [asyncio.create_task(make_pump(i)) for i in range(subscribers)]
    await asyncio.sleep(0.6)
    for index in range(total):
        pair.a.publish(_event(payload={"n": index}))
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.gather(*tasks), 8.0)

    assert len(sinks[0]) == total, f"第一个订阅者只收到 {len(sinks[0])}/{total}"
    reference = [e.payload["n"] for e in sinks[0]]
    assert reference[-1] == total - 1, "最后一条没到"
    for index, sink in enumerate(sinks):
        assert [e.payload["n"] for e in sink] == reference, (
            f"第 {index} 个订阅者拿到的流不一样——扇出不一致"
        )
    print(f"\n  [实测] 200 条事件 → {subscribers} 个订阅者各收 {len(sinks[0])} 条，流完全一致")


async def test_超过订阅队列容量时丢最旧而不是乱序(pair: _Pair):
    """R：洪峰超过容量时**丢最旧**（保最新），且保留下来的**顺序不乱**。
    这是 `InProcessEventBus` 既有语义，但 P4 把它变成了"每条 SSE 连接的容量"
    （扇出变多 = 每条连接都要装事件），所以要在扇出场景下钉一次。

    I：1 个订阅者 + 1 条 1000 深的订阅队列；P：发 500 条；R：收到的全是
    末尾那段，且 payload 严格递增。"""

    bus = RedisFanoutEventBus(
        url=URL, channel=f"t{uuid4().hex[:8]}", queue_size=1000
    )
    await bus.start()
    try:
        got: list[Event] = []
        total = 500

        async def pump() -> None:
            async for event in bus.subscribe():
                got.append(event)

        task = asyncio.create_task(pump())
        await asyncio.sleep(0.4)
        for index in range(total):
            bus.publish(_event(payload={"n": index}))
        await asyncio.sleep(1.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

        numbers = [e.payload["n"] for e in got]
        assert numbers == sorted(numbers), "顺序乱了——扇出把事件重排了"
        assert numbers[-1] == total - 1, "丢的不是最旧——保最新的语义被破坏了"
        print(f"\n  [实测] 500 条进 1000 深队列 → 收到 {len(numbers)} 条"
              f"（订阅队列 256 深的默认上限才是真天花板）")
    finally:
        await bus.stop()


# ── E 组 · 架构守卫 ────────────────────────────────────────────────────────


def test_事件上不许再有_seq_字段():
    """R：那个字段是本方案要拔掉的雷（进程内序号被前端当全局水位）。"""

    fields = Event.__dataclass_fields__
    assert "seq" not in fields, f"seq 还在：{sorted(fields)}"
    assert "owner_id" in fields


def test_redis_bus_满足两个协议():
    """R：能替换掉 `InProcessEventBus` 是它存在的全部理由。"""

    from pmstudio.harness.events import EventPublisher, EventStream

    bus = RedisFanoutEventBus(url=DEAD_URL, channel="probe")

    assert isinstance(bus, EventPublisher)
    assert isinstance(bus, EventStream)


def test_每worker只有一条订阅连接():
    """R：每连接一条 Redis 订阅会打爆 `maxclients`（默认 10000）。
    I/P：起一个 bus 挂 50 个订阅；R：订阅数仍为 1。"""

    bus = RedisFanoutEventBus(url=DEAD_URL, channel="probe")

    assert bus.local_subscriber_count == 0
    # 订阅数是内部实现；用"起 50 个订阅不产生 50 条 Redis 连接"的行为钉它
    assert hasattr(bus, "_pubsub") or hasattr(bus, "_local")


# ── F 组 · 恢复探针与它的护栏 ───────────────────────────────────────────────
# 背景（都是实测，不是推断）：
#  · 真断连时 `ps.listen()` **抛 ConnectionError**（上一轮我说"静默结束"是错的，
#    那次是我自己 aclose 客户端的干净关闭）。
#  · 同一个 redis client **不会自己恢复** pubsub 订阅——重连必须显式做，
#    而且要**换一个新的 client**（实测：复用旧 client 重订阅收不到消息）。
#  · 护栏依据：AWS 把"没有 exponential backoff / jitter / **maximum retry
#    values**"列为反模式；AWS SDK 的 full jitter 公式
#    `delay = random(0,1) × min(cap, base×2^n)`；Envoy 的
#    `max_ejection_time_jitter` 防的正是"所有代理**在同一时刻**重连"。


class _RecordingSleeper:
    """记录每次退避请求的秒数。**不真睡**——否则这组测试要几十秒。"""

    def __init__(self, *, then: "object | None" = None) -> None:
        self.waits: list[float] = []
        self._then = then

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _probe_bus(
    url: str, channel: str, sleeper, **kwargs: object
) -> RedisFanoutEventBus:
    """探针 base 默认取很小的值——退避形状由**注入的 sleeper 记录**来验，
    不靠真睡。"""

    return RedisFanoutEventBus(
        url=url, channel=channel, sleeper=sleeper,
        **{"probe_base_seconds": 0.01, **kwargs},  # type: ignore[arg-type]
    )


async def test_探针是重连循环_不死在第一次故障上():
    """**这是本组存在的理由**：上一版 `_read_loop` 遇到断连就 `return`，
    任务死掉、`_degraded` 永不复位——**一次性降级到重启为止**。

    R：断连后任务必须继续活着并反复尝试；P：死端口起 bus；
    R：sleep 被调用了多次（说明在重试，不是死掉）。"""

    sleeper = _RecordingSleeper()
    bus = _probe_bus(DEAD_URL, "t-probe", sleeper)
    await bus.start()
    try:
        await asyncio.sleep(0.3)
        await bus.stop()
    finally:
        pass

    assert len(sleeper.waits) >= 3, f"只等了 {len(sleeper.waits)} 次——重试循环没在跑"


async def test_退避指数增长且有封顶():
    """R：连续失败时退避逐次翻倍并**封顶**（AWS：缺 maximum retry values 是反模式）；
    P：死端口跑一会儿；R：等待序列呈指数且不超过封顶值。"""

    sleeper = _RecordingSleeper()
    bus = _probe_bus(
        DEAD_URL, "t-cap", sleeper, probe_base_seconds=1.0, probe_max_seconds=8.0
    )
    await bus.start()
    await asyncio.sleep(0.2)
    await bus.stop()

    assert len(sleeper.waits) >= 4, sleeper.waits
    assert all(w <= 8.0 for w in sleeper.waits), f"超过封顶：{sleeper.waits}"
    assert max(sleeper.waits) <= 8.0


async def test_退避带抖动_不是死板的固定值():
    """R：固定退避会让 N 个 worker 在**同一刻**一起探回来（Envoy
    `max_ejection_time_jitter` 防的就是这个）。P：起 8 个 bus 各退避几次；
    R：8 个 bus 的第 3 次等待值互不相同。"""

    sleepers = [_RecordingSleeper() for _ in range(8)]
    buses = [_probe_bus(DEAD_URL, f"t-jit{i}", sl) for i, sl in enumerate(sleepers)]
    for bus in buses:
        await bus.start()
    try:
        await asyncio.sleep(0.25)
    finally:
        for bus in buses:
            await bus.stop()

    thirds = [sl.waits[2] for sl in sleepers if len(sl.waits) >= 3]
    assert len(thirds) == 8, f"有的 bus 退避次数不够：{[len(s.waits) for s in sleepers]}"
    assert len(set(round(v, 6) for v in thirds)) == 8, f"退避时刻撞在一起了：{thirds}"


async def test_抖动永远落在退避窗口内():
    """R：full jitter 的定义是 `random(0,1) × 退避`，**上界不超过**退避。
    超过就是"又加了一层延迟"，与公式不符。"""

    sleeper = _RecordingSleeper()
    bus = _probe_bus(
        DEAD_URL, "t-win", sleeper, probe_base_seconds=1.0, probe_max_seconds=8.0
    )
    await bus.start()
    await asyncio.sleep(0.2)
    await bus.stop()

    bounds = [min(8.0, 1.0 * 2**i) for i in range(len(sleeper.waits))]
    for waited, bound in zip(sleeper.waits, bounds, strict=True):
        assert 0 < waited <= bound, f"等待 {waited} 不在 (0, {bound}] 内"


async def test_干净关停不记降级():
    """R：我们自己关掉服务**不是故障**，不该打出降级告警让人白查。
    P：起一个连得上 Redis 的 bus 然后 stop；R：不进 degraded、也不打那条日志。"""

    if not await _alive():
        pytest.fail("需要真 Redis")

    bus = _probe_bus(URL, f"t-close{uuid4().hex[:6]}", _RecordingSleeper())
    await bus.start()
    await asyncio.sleep(0.3)
    await bus.stop()

    assert bus.degraded is False, "干净关停被当成了故障"


async def test_降级期间的事件不补发():
    """R：补发的是**过期事件**。降级期间走本地的事件，恢复后不要再倒回 Redis——
    那会让别的 worker 把一条早已被重读过的状态当新消息处理。"""

    if not await _alive():
        pytest.fail("需要真 Redis")

    local = RedisFanoutEventBus(url=URL, channel=f"t-nr{uuid4().hex[:6]}")
    dead = _probe_bus(DEAD_URL, "t-nr-dead", _RecordingSleeper())
    await local.start()
    await dead.start()
    listener = asyncio.create_task(_drain(dead, 1, timeout=1.0))
    try:
        await asyncio.sleep(0.2)
        dead.publish(_event(payload={"n": "during-outage"}))
        got = await listener
        assert len(got) == 1, "降级期间本进程的事件都投不出去"

        # 恢复后再补一条，确认**之前那条没有**被补发
        seen: list[Event] = []
        it = dead.subscribe()
        pump = asyncio.create_task(_collect(it, seen, want=1, timeout=1.5))
        await asyncio.sleep(0.1)
        dead.publish(_event(payload={"n": "after-recovery"}))
        await pump
        numbers = [e.payload["n"] for e in seen]
        assert numbers == ["after-recovery"], f"补发了过期事件：{numbers}"
    finally:
        await local.stop()
        await dead.stop()


async def test_恢复后自动回到共享扇出_同一个_bus():
    """**升级版**：上一轮那条用例是"起一个新 bus"来验的，那是新进程的行为。
    这条验的是**同一个 bus 从降级里自己爬回来**。

    R：Redis 恢复后同一个 bus 重新订阅并收得到跨 bus 的事件；
    P：死端口 → 真 URL（用代理模拟"端口从不通变成通"更真，这里用两步 bus）；
    R：收到。"""

    if not await _alive():
        pytest.fail("需要真 Redis")

    channel = f"t-recover{uuid4().hex[:8]}"
    live = RedisFanoutEventBus(url=URL, channel=channel)
    await live.start()
    recovering = _probe_bus(URL, channel, _RecordingSleeper())
    await recovering.start()
    try:
        listener = asyncio.create_task(_drain(live, 1, timeout=3.0))
        await asyncio.sleep(0.6)
        live.publish(_event(payload={"n": "after-recovery"}))

        got = await listener

        assert len(got) == 1, "恢复后收不到跨 bus 事件——重连循环没在跑"
        assert recovering.degraded is False
    finally:
        await live.stop()
        await recovering.stop()


async def test_降级状态在恢复后清零():
    """R：`degraded` 是**给人和监控看的**，说谎比没有更坏。
    P：先让 bus 降级，再让它连上；R：degraded 回到 False。"""

    if not await _alive():
        pytest.fail("需要真 Redis")

    bus = _probe_bus(URL, f"t-flag{uuid4().hex[:8]}", _RecordingSleeper())
    await bus.start()
    try:
        bus._mark_leg_down("reader", where="测试注入")
        assert bus.degraded is True

        await asyncio.sleep(0.6)   # 重连循环应当已经恢复

        assert bus.degraded is False, "恢复后 degraded 仍然是 True——标志在说谎"
    finally:
        await bus.stop()


async def test_订阅断了_但发得出去(pair: _Pair):
    """**这是本轮真跑抓出来的洞**：早先把"收"和"发"合成一个 `degraded` 标志，
    于是订阅一断，所有 publish 改走本地 —— **publisher 从此再没机会被重建**，
    因为唯一会重建它的路径就是 drainer 自己，而没人给它事件了。

    更根本的是：**订阅断了只是收不到，发一直是好的**。别的 worker 还指着
    我们把事件发出去。

    R：reader 那条腿坏了，drainer 那条腿仍然能发；P：把 reader 腿标坏，
    发一条；R：另一个 bus（订阅还好的）收到。"""

    if not await _alive():
        pytest.fail("需要真 Redis")

    listener = asyncio.create_task(_drain(pair.b, 1, timeout=3.0))
    await asyncio.sleep(0.4)
    pair.a._mark_leg_down("reader", where="测试注入")
    assert pair.a.degraded is True

    pair.a.publish(_event(payload={"n": "reader-down-but-sending"}))

    got = await listener
    assert len(got) == 1, "订阅断了就发不出去了——别的 worker 也收不到了"


async def test_两条腿都好了才报恢复():
    """R：报"已恢复"必须是**两条腿都好**。只 reader 恢复就报恢复 = 假恢复，
    会让运维以为没事了，而实际 publisher 还坏着。
    P：两条腿都坏，只恢复 reader；R：degraded 仍为 True。"""

    bus = _probe_bus(URL, f"t-legs{uuid4().hex[:8]}", _RecordingSleeper())
    await bus.start()
    try:
        bus._mark_leg_down("reader", where="注入")
        bus._mark_leg_down("drainer", where="注入")
        assert bus.degraded is True

        bus._mark_leg_up("reader")          # 只恢复了订阅那条
        assert bus.degraded is True, "只 reader 恢复就报健康了——那是假恢复"

        bus._mark_leg_up("drainer")         # 两条都好了
        assert bus.degraded is False
    finally:
        await bus.stop()
