"""模型调用的韧性层：隔离舱 / 熔断 / 重试预算 / 抖动。

判据分两类：**开闸/关闸的时机**与**不误伤**。

- 一个失败不许开闸（低流量下第一个失败就熔断＝熔断器自己制造假象）；
- 样本不足不许下结论（`failure_rate` 给 `None`，不是 0）；
- **用户输错不许熔断**（`classify()` 判成 POLICY/VALIDATION 的失败不计）；
- 半开要放**一次**探测，不是全放。
"""

import asyncio
import random

import pytest

from pmstudio.harness.model.failure import FailureKind, classify
from pmstudio.harness.model.resilience import (
    BreakerConfig,
    Bulkhead,
    BulkheadConfig,
    BulkheadFullError,
    CircuitBreaker,
    CircuitOpenError,
    RetryBudget,
    RetryBudgetConfig,
    jittered_backoff,
)


class _Timeout(Exception):
    pass


class _Validation(Exception):
    pass


# ── 熔断 ────────────────────────────────────────────────────────────────────


async def _喂一次(breaker: CircuitBreaker, error: BaseException | None) -> None:
    """喂一次调用结果给熔断器。**走 `call_upstream` 这条真实路径**——
    半开迁移、计数清零、开闸都在里面，只调钩子会漏掉一半。"""

    async def upstream() -> str:
        if error is not None:
            raise error
        return "ok"

    try:
        await breaker.call_upstream(upstream)
    except BaseException:  # noqa: BLE001 —— 上面抛的异常本来就该冒出来
        pass


async def _喂失败(breaker: CircuitBreaker, count: int) -> None:
    for _ in range(count):
        await _喂一次(breaker, _Timeout())


async def _raise_validation() -> str:
    raise _Validation()


async def test_一次失败不许开闸():
    """服务刚起、流量很低时，第一个失败不该把熔断器打开。"""

    breaker = CircuitBreaker(BreakerConfig(minimum_samples=10))
    await _喂失败(breaker, 9)

    assert breaker.is_open is False
    assert breaker.failures == 9


async def test_连续依赖失败到阈值就开闸():
    breaker = CircuitBreaker(BreakerConfig(minimum_samples=10))
    await _喂失败(breaker, 10)

    assert breaker.is_open is True
    with pytest.raises(CircuitOpenError):
        breaker.allow()


async def test_用户输错不进熔断计数():
    """策略/校验类失败是**请求自己**的问题，不该算成"依赖不健康"。"""

    breaker = CircuitBreaker(BreakerConfig(minimum_samples=5))
    for _ in range(20):
        await _喂一次(breaker, _Validation())

    assert breaker.failures == 0
    assert breaker.is_open is False


async def test_任何一次成功都把失败计数清零():
    """**pybreaker 的原生语义（2026-10-16 实测确认）：累计计数、成功即清零。**

    这条钉住的是这个语义本身——它意味着**没有滑动窗口失败率**：
    交替失败/成功时计数永远回不到阈值，熔断也就永远不会开（实测 40 次不开）。
    写在这里是为了让下一个读代码的人知道这是**有意的取舍**，不是 bug。
    """

    breaker = CircuitBreaker(BreakerConfig(minimum_samples=5))
    await _喂失败(breaker, 4)
    assert breaker.failures == 4

    await _喂一次(breaker, None)
    assert breaker.failures == 0

    await _喂失败(breaker, 4)
    assert breaker.is_open is False


async def test_开闸后经过恢复时间转半开():
    """半开的探针**就是那次真实调用**——所以半开成功一次才算闭合。"""

    breaker = CircuitBreaker(
        BreakerConfig(minimum_samples=2, recovery_timeout=0.05)
    )
    await _喂失败(breaker, 2)
    assert breaker.is_open is True

    await asyncio.sleep(0.06)

    assert breaker.is_open is False, "过了恢复窗口还没放行——探测请求会被永久拒绝"
    await _喂一次(breaker, None)     # 这次真实调用成功了
    assert breaker.is_open is False  # 闭合


async def test_半开时探针失败则继续开闸():
    """恢复窗口到了**不等于**上游好了：探针失败要立刻回到开闸，而不是放行一片。"""

    breaker = CircuitBreaker(BreakerConfig(minimum_samples=2, recovery_timeout=0.05))
    await _喂失败(breaker, 2)
    assert breaker.is_open is True

    await asyncio.sleep(0.06)
    # 探针失败 → 重新开闸
    await _喂失败(breaker, 1)

    assert breaker.is_open is True, "探针失败后闸门重新打开了——上游还没恢复"


async def test_半开时探针真的被await_过():
    """探针必须是**那次真实调用**：库若走同步 `call()`，协程不会被 await
    （`RuntimeWarning`）并被当成探针成功。这条用"协程被跑过的痕迹"钉住它。"""

    breaker = CircuitBreaker(BreakerConfig(minimum_samples=1, recovery_timeout=0.05))
    await _喂失败(breaker, 1)
    await asyncio.sleep(0.06)

    ran: list[str] = []

    async def probe() -> str:
        ran.append("probe")
        return "ok"

    await breaker.call_upstream(probe)

    assert ran == ["probe"], "半开探针没真的执行——熔断器会误判成已恢复"
    assert breaker.is_open is False


async def test_开闸期间连上游都不碰():
    """熔断的全部意义：坏的时候**一个包都不发**。"""

    sent: list[int] = []

    async def upstream() -> str:
        sent.append(1)
        raise _Timeout()

    breaker = CircuitBreaker(
        BreakerConfig(minimum_samples=2, recovery_timeout=60)
    )
    for _ in range(2):
        with pytest.raises(_Timeout):
            await breaker.call_upstream(upstream)
    assert len(sent) == 2

    with pytest.raises(CircuitOpenError):
        await breaker.call_upstream(upstream)

    assert len(sent) == 2, f"开闸后又发了 {len(sent) - 2} 个请求——闸门没起作用"


async def test_非依赖失败不会让闸门打开():
    breaker = CircuitBreaker(BreakerConfig(minimum_samples=2))
    for _ in range(10):
        with pytest.raises(_Validation):
            await breaker.call_upstream(_raise_validation)

    assert breaker.is_open is False
    assert breaker.failures == 0


# ── 隔离舱 ──────────────────────────────────────────────────────────────────


async def test_隔离舱限住在飞数():
    bulkhead = Bulkhead(BulkheadConfig(capacity=2, queue_wait_seconds=5))
    running = 0
    peak = 0

    async def work() -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        try:
            await asyncio.sleep(0.02)
        finally:
            running -= 1

    await asyncio.gather(*[bulkhead.run(lambda: work()) for _ in range(8)])

    assert peak <= 2, f"峰值 {peak} 超过容量 2"
    assert bulkhead.peak_in_flight <= 2


async def test_隔离舱满了快速失败而不是无限排队():
    """排队必须封顶：不封顶的话请求会在舱门前堆成一串，越堆越慢，最后全体超时。"""

    bulkhead = Bulkhead(BulkheadConfig(capacity=1, queue_wait_seconds=0.05))
    released = asyncio.Event()

    async def slow() -> None:
        await released.wait()

    holder = asyncio.create_task(bulkhead.run(slow))
    await asyncio.sleep(0.01)

    with pytest.raises(BulkheadFullError):
        await bulkhead.run(lambda: slow())

    released.set()
    await holder
    assert bulkhead.rejections == 1


async def test_隔离舱异常时也释放许可():
    """许可泄漏会让舱在几次崩溃后永久满——这种 bug 只在异常路径出现，最容易漏。"""

    bulkhead = Bulkhead(BulkheadConfig(capacity=1, queue_wait_seconds=1))

    async def boom() -> None:
        raise ValueError("boom")

    for _ in range(3):
        with pytest.raises(ValueError):
            await bulkhead.run(boom)

    # 还能拿到许可 = 前几次异常没有把舱漏满
    async def ok() -> str:
        return "ok"

    assert await bulkhead.run(ok) == "ok"


# ── 重试预算 ────────────────────────────────────────────────────────────────


def test_预算耗尽就不许再重试():
    budget = RetryBudget(RetryBudgetConfig(ratio=0.25, burst=2, refill_per_second=0))

    assert budget.take() is True
    assert budget.take() is True
    assert budget.take() is False  # 预算用光：宁可失败也不放大


def test_原始请求补充预算():
    budget = RetryBudget(RetryBudgetConfig(ratio=1.0, burst=2, refill_per_second=0))
    budget.take()
    budget.take()
    assert budget.take() is False

    budget.record_original()

    assert budget.take() is True


def test_预算是突发封顶的():
    budget = RetryBudget(RetryBudgetConfig(ratio=1.0, burst=3, refill_per_second=0))
    for _ in range(50):
        budget.record_original()

    assert budget.tokens <= 3


# ── 抖动 ────────────────────────────────────────────────────────────────────


def test_退避带抖动且落在区间内():
    rng = random.Random(20261016)
    values = [jittered_backoff(2.0, ratio=0.5, rng=rng) for _ in range(200)]

    assert all(1.0 <= v <= 3.0 for v in values)
    # 关键：必须有分散。固定退避会让所有调用方在同一刻重试（重试风暴）
    assert len(set(round(v, 4) for v in values)) > 50


def test_抖动不会退到零以下():
    values = [jittered_backoff(0.1, ratio=0.5, rng=random.Random(i)) for i in range(50)]

    assert all(v > 0 for v in values)


# ── 失败分类与熔断口径对齐 ──────────────────────────────────────────────────


def test_熔断只认依赖类失败():
    """`_DEPENDENCY_FAILURES` 就是熔断的口径本身，改它等于改熔断行为。"""

    assert classify(_Timeout()) in {
        FailureKind.TIMEOUT,
        FailureKind.TRANSIENT,
        FailureKind.QUOTA,
    }
    assert classify(_Validation()) not in {
        FailureKind.TIMEOUT,
        FailureKind.TRANSIENT,
        FailureKind.QUOTA,
    }