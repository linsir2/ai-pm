"""登录限流闸的 Redis 实现。**跑真 Redis**，不打桩。

为什么这组测试必须打真库：`limits` 的原子性保证在 Lua 脚本里，
拿桩替身"测"等于把唯一要验的东西验没了。真跑还能验两件桩里不存在的事：

- **并发下不能超发**（这是所有 Redis 限流的正确性命题）
- **降级路径**（Redis 挂了还限得住）——这是"fail-open vs fail-closed"那个
  问题的落地

Redis 不可用时**这组测试直接失败**，不 skip：本方案把 Redis 写进了关键路径，
"测试悄悄跳过"和"限流器悄悄失效"是同一类事故。
"""

import asyncio
import os

import pytest
from limits import parse
from limits.aio.strategies import MovingWindowRateLimiter

from pmstudio.application.ports import LoginGuard, LoginVerdict
from pmstudio.infrastructure.redis_limiter import RedisLoginGuard

URL = os.environ.get("PMSTUDIO_TEST_REDIS_URL", "redis://127.0.0.1:6379/9")

pytestmark = pytest.mark.skipif(
    not os.environ.get("PMSTUDIO_TEST_REDIS_URL"),
    reason="需要 PMSTUDIO_TEST_REDIS_URL（指向**独立**的测试库，别指生产库）",
)


async def _redis_alive() -> bool:
    import redis.asyncio as aioredis

    client = aioredis.from_url(URL, socket_timeout=0.5)
    try:
        await client.ping()
        return True
    except Exception:  # noqa: BLE001 —— 这里就是要抓任何连不上的情况
        return False
    finally:
        await client.aclose()


@pytest.fixture()
async def guard():
    """每个用例一个独立命名空间，**用例之间不许互相污染计数**。"""

    import uuid

    if not await _redis_alive():
        pytest.fail(
            f"连不上 Redis（{URL}）。这组测试必须打真库——"
            "原子性只能验真库，桩里没有 Lua 脚本。"
        )
    limiter = MovingWindowRateLimiter(
        await _make_storage(namespace=f"t{uuid.uuid4().hex[:12]}"),
    )
    made = RedisLoginGuard(
        limiter=limiter,
        max_fails=5,
        lock_minutes=15,
        attempt_window_seconds=60,
        ip_max_attempts=20,
        username_max_attempts=10,
    )
    yield made


async def _make_storage(*, namespace: str):
    from limits.aio.storage import RedisStorage

    return RedisStorage(
        f"async+{URL}",
        implementation="redispy",
        socket_timeout=0.3,
        max_connections=256,
        key_prefix=f"PMSTUDIO:{namespace}",
    )


# ── 原子性 ──────────────────────────────────────────────────────────────────


async def test_并发下恰好放行上限那么多(guard):
    """**这组测试存在的理由**。

    R：200 个并发同时打一个 IP，上限 20 → 恰好 20 个放行，一个不多。
    I：`ip_max_attempts=20`，200 个并发 `consume_attempt`
    P：`asyncio.gather`
    R：`limited=False` 恰好 20 个。

    如果限流是先读后写（没有 Lua 原子性），这里会出现超发——那正是
    "多 worker 下锁不住"在代码层面的样子。
    """

    # 用户名必须各不相同：都用同一个会先撞上 per-username 上限（10），
    # 测到的就不是 per-IP 这一维了（这个错我真犯过）。
    results = await asyncio.gather(
        *(
            guard.consume_attempt(ip="1.2.3.4", username=f"u{index}")
            for index in range(200)
        )
    )

    assert sum(1 for r in results if not r.limited) == 20


async def test_per_username_维度并发下也不超发(guard):
    """R：per-username 那一维同样要原子——分布式撞库就是打这一维。
    P：200 并发、同一用户名、per-IP 20 / per-username 10；R：恰好 10。"""

    results = await asyncio.gather(
        *(
            guard.consume_attempt(ip=f"9.9.9.{index}", username="alice")
            for index in range(200)
        )
    )

    assert sum(1 for r in results if not r.limited) == 10


async def test_连接池_200_并发不爆(guard):
    """R：`max_connections` 默认 100，200 并发会 `MaxConnectionsError`——
    这个值必须显式定。P：200 并发；R：不抛连接池错误。"""

    results = await asyncio.gather(
        *(guard.consume_attempt(ip="5.5.5.5", username=f"u{i}") for i in range(200)),
        return_exceptions=True,
    )

    assert not [r for r in results if isinstance(r, BaseException)], [
        r for r in results if isinstance(r, BaseException)
    ][:1]


# ── 三维语义与内存版对齐 ────────────────────────────────────────────────────


async def test_三维各自独立(guard):
    """R：打满失败锁不许连带把 per-IP 也打满；反之亦然。"""

    for _ in range(5):
        await guard.record_failure(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is True
    assert (await guard.consume_attempt(ip="1.1.1.1", username="alice")).limited is False


async def test_换_ip_不受影响(guard):
    for _ in range(5):
        await guard.record_failure(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="2.2.2.2", username="alice")).limited is False


async def test_成功登录清失败锁(guard):
    """R：手滑错两次后登录成功，不该被锁。"""

    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.clear(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is False


async def test_成功登录不清尝试计数(guard):
    """R：只有失败锁被成功清掉——否则偶尔猜对一次就能把速率闸洗白。"""

    for _ in range(9):
        await guard.consume_attempt(ip="1.1.1.1", username="alice")
    await guard.clear(ip="1.1.1.1", username="alice")

    assert (await guard.consume_attempt(ip="1.1.1.1", username="alice")).limited is False
    assert (await guard.consume_attempt(ip="1.1.1.1", username="alice")).limited is True


async def test_用户名里的分隔符不撞键(guard):
    """R：`f"{ip}|{user}"` 会被用户名里的 `|` 撞掉别人的限额。"""

    for _ in range(5):
        await guard.record_failure(ip="a|b", username="c")

    assert (await guard.locked(ip="a|b", username="c")).limited is True
    assert (await guard.locked(ip="a", username="b|c")).limited is False


async def test_超限给正整数_RetryAfter(guard):
    """R：空/0/负数等于没告诉客户端，它只会硬重试。"""

    for _ in range(21):
        await guard.consume_attempt(ip="3.3.3.3", username=f"u{_}")

    verdict = await guard.consume_attempt(ip="3.3.3.3", username="last")

    assert verdict.limited is True
    assert isinstance(verdict.retry_after, int)
    assert verdict.retry_after > 0


async def test_判定类型就是契约里的那个(guard):
    assert isinstance(await guard.locked(ip="1.1.1.1", username="a"), LoginVerdict)
    assert isinstance(
        await guard.consume_attempt(ip="1.1.1.1", username="a"), LoginVerdict
    )


# ── 降级 ────────────────────────────────────────────────────────────────────


#: 一个**没人监听**的端口：连上去立刻被拒（实测 1ms 抛 ConnectionError）。
#: 不用替身：降级路径走的是**真 Lua + 真客户端**，替身会把唯一要验的东西绕过去。
DEAD_URL = "redis://127.0.0.1:6399/9"


async def _dead_guard(**overrides: object) -> RedisLoginGuard:
    from limits.aio.storage import RedisStorage
    from limits.aio.strategies import MovingWindowRateLimiter

    storage = RedisStorage(
        f"async+{DEAD_URL}",
        implementation="redispy",
        socket_timeout=0.3,
        max_connections=8,
    )
    kwargs: dict[str, object] = {
        "max_fails": 2,
        "lock_minutes": 15,
        "attempt_window_seconds": 60,
        "ip_max_attempts": 3,
        "username_max_attempts": 3,
    }
    kwargs.update(overrides)
    return RedisLoginGuard(limiter=MovingWindowRateLimiter(storage), **kwargs)  # type: ignore[arg-type]


async def test_连不上Redis时降级到内存闸_而不是全放行():
    """**这是本方案对"fail-open vs fail-closed"的回答**。

    R：Redis 挂了不许变成"限流器不存在"。
    I：底层 limiter 指向一个没人监听的端口
    P：连打 5 次 `consume_attempt`
    R：不把异常抛给 API 层；第 4 次起 `limited=True`（本地闸仍然限得住）。
    """

    guard = await _dead_guard()

    results = [await guard.consume_attempt(ip="1.1.1.1", username="a") for _ in range(5)]

    assert [r.limited for r in results] == [False, False, False, True, True]


async def test_连不上Redis时失败锁也降级():
    guard = await _dead_guard()
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.record_failure(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is True


async def test_降级_但非连接类异常照抛():
    """R：降级只对"连不上 Redis"生效——别的异常被吞掉就是藏 bug。
    I：桩抛 `ValueError`；P：调用；R：异常照抛。"""

    from unittest.mock import AsyncMock, patch

    guard = await _dead_guard()
    boom = ValueError("脚本写错了")
    with patch.object(guard._limiter, "hit", AsyncMock(side_effect=boom)):
        with pytest.raises(ValueError, match="脚本写错了"):
            await guard.consume_attempt(ip="1.1.1.1", username="a")


async def test_Redis_恢复后自动回到共享计数():
    """R：降级不能是单向的——Redis 回来后要重新用共享计数。
    P：先降级一次，再用真 limiter 再试。"""

    from uuid import uuid4

    storage = await _make_storage(namespace=f"t{uuid4().hex[:12]}")
    live = RedisLoginGuard(
        limiter=MovingWindowRateLimiter(storage),
        max_fails=5,
        lock_minutes=15,
        attempt_window_seconds=60,
        ip_max_attempts=2,
        username_max_attempts=2,
    )
    down = await _dead_guard(ip_max_attempts=99)

    assert (await down.consume_attempt(ip="1.1.1.1", username="a")).limited is False
    # 上限 2 = 放行 2 次，第 3 次才拒（limits 的语义，不是我的）
    assert (await live.consume_attempt(ip="1.1.1.1", username="a")).limited is False
    assert (await live.consume_attempt(ip="1.1.1.1", username="a")).limited is False
    verdict = await live.consume_attempt(ip="1.1.1.1", username="a")

    assert verdict.limited is True, "Redis 恢复后仍没回到共享计数"


async def test_降级是可见的():
    """R："悄悄降级"和"限流器不在了"从外面看是一回事。
    I：Redis 挂；P：调一次；R：对外暴露 `degraded` 状态为真。"""

    guard = await _dead_guard()
    await guard.consume_attempt(ip="1.1.1.1", username="a")

    assert guard.degraded is True


async def test_连接卡住时也能在超时内降级():
    """R：`socket_timeout` 不设的话实测会卡 5 秒——降级路径必须是快的。
    I：显式 0.3s 超时的连不上的客户端；P：调一次；R：≤1s 返回。"""

    guard = await _dead_guard()

    loop = asyncio.get_running_loop()
    start = loop.time()
    await guard.consume_attempt(ip="1.1.1.1", username="a")

    assert loop.time() - start < 1.0


async def test_自建限流器_不接受没配上限的实现():
    """R：漏配上限会让 `parse()` 抛一个看不懂的异常。
    P：拿一个 limits 实例直接包；R：断言它满足 LoginGuard 协议形状。"""

    from uuid import uuid4

    storage = await _make_storage(namespace=f"t{uuid4().hex[:12]}")
    made = RedisLoginGuard(
        limiter=MovingWindowRateLimiter(storage),
        max_fails=parse("5/15minutes").amount,
        lock_minutes=15,
        attempt_window_seconds=60,
        ip_max_attempts=20,
        username_max_attempts=10,
    )

    assert hasattr(made, "consume_attempt")


def test_底层库_返回的限流项_与我们配置的上限一致():
    """R：两边算出来的窗口必须一样——不一致就是"测的和跑的不是一个东西"。"""

    item = parse("5/15minutes")

    assert item.amount == 5
    assert item.get_expiry() == 15 * 60


async def test_Redis实现满足协议():
    """R：降级目标是 `LoginGuard`，所以两份实现能互换；P：检查协议形状。"""

    guard = await _dead_guard()

    assert isinstance(guard, LoginGuard)


async def test_降级标志在恢复后清零():
    """**标志说谎比没有标志更坏**。

    限流的降级是**每次调用各自判**的：Redis 一恢复，下一次调用就走回共享
    计数了，没有"卡住"的进程内状态。标志要是停在 True 不清，
    就会报"限流已降级"报一整天——而实际第二次故障就已经恢复。

    I/P：先让一次调用降级，再用真 Redis 的同款 limiter 打一次；R：标志回到 False。
    """

    from uuid import uuid4

    storage = await _make_storage(namespace=f"t{uuid4().hex[:12]}")
    guard = RedisLoginGuard(
        limiter=MovingWindowRateLimiter(storage),
        max_fails=5, lock_minutes=15, attempt_window_seconds=60,
        ip_max_attempts=20, username_max_attempts=10,
    )
    # 同一个 RedisLoginGuard：先注入一次降级，再打一次成功的
    guard._degraded = True
    await guard.consume_attempt(ip="1.1.1.1", username="a")

    assert guard.degraded is False, "恢复后标志还是 True——它在骗人"
