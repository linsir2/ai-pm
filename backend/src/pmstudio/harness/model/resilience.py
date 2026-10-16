"""模型调用的韧性层（高并发 P2）：隔离舱 + 熔断 + 重试预算。

为什么是三样而不是一样：
- **熔断**（breaker）：依赖**已经**坏了之后，不再做无用功——直接失败，不占槽位不花钱。
- **隔离舱**（bulkhead）：依赖**正在**坏的时候，限制它能占住多少资源。两者互补，
  缺一个都会出事：只有熔断，坏掉的那一瞬间仍在并发堆积；只有隔离舱，故障期间
  每��请求都还在排队等一个注定失败的调用。
- **重试预算**（retry budget）：重试是**放大器**。上游抖动时所有调用方一起退避重试，
  会把本来能恢复的服务打死。预算把"额外尝试"限制在原始请求的一定比例内。

三个都只包住**外部依赖调用**这一层，不进 `domain`（分层守卫）。

## 为什么熔断只计"依赖不健康"的失败

`classify()` 已经把异常分成处置类。重试只看 `TRANSIENT` / `TIMEOUT`；熔断的失败集
比它**更窄**：`POLICY` / `VALIDATION` / `UNKNOWN` 是**请求本身**的问题
（用户输入错、schema 不合、内容被策略拒），把它们计进熔断会让"用户输错一次就把
上游熔断"——那不是熔断，那是误伤。

## 为什么滑动窗口 + 最小样本数

绝对计数在低流量下会误开：服务刚启动时第一个调用失败就开闸，于是熔断器自己
制造了一次"上游全挂"的假象。滑动窗口按**失败率**判定，并要求**最小样本数**，
样本不够就只观察不下结论。
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from pybreaker import (
    STATE_OPEN,
    CircuitBreakerError,
)
from pybreaker import CircuitBreaker as _PyBreaker

from pmstudio.harness.model.failure import FailureKind, classify


class CircuitOpenError(RuntimeError):
    """熔断打开：依赖已知不健康，不再发请求。"""

    code = "circuit_open"


class BulkheadFullError(RuntimeError):
    """隔离舱满且排队超时：不能让请求无限堆在依赖前面。"""

    code = "bulkhead_full"


#: 熔断只认这几类失败——"依赖不健康"。其余（策略/校验/未知）是请求自己的问题，
#: 计进去等于让用户输错密码把上游熔断。
_DEPENDENCY_FAILURES = frozenset({FailureKind.TRANSIENT, FailureKind.TIMEOUT, FailureKind.QUOTA})


@dataclass(frozen=True, slots=True)
class BreakerConfig:
    """熔断参数。默认值的依据写在字段注释里，不拍脑袋。

    **判据是 pybreaker 的原生语义：累计失败计数，任一次成功即清零**，
    达到 `minimum_samples` 次依赖失败就开闸。**不是**滑动窗口失败率——这一点是
    2026-10-16 在真库上实测出来的（见下方 `CircuitBreaker` 的说明）。
    """

    #: 累计多少次依赖失败就开闸。5 次：防的是"第一个失败就熔断"——
    #: 启动第一秒拿到一次超时不该让整个模型通道停摆。
    minimum_samples: int = 5
    #: 开闸后等多久放一次探测请求（半开 → `pybreaker.reset_timeout`）。
    recovery_timeout: float = 30.0
    #: 半开后要连续成功几次才算恢复。1 次：一次就够，再不行就继续开闸。
    success_threshold: int = 1


_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class BulkheadConfig:
    """隔离舱参数。"""

    #: 同时进行的上游调用数。4 起步：再往上不会让吞吐变好（单事件循环），
    #: 只会把上游打得更狠。
    capacity: int = 4
    #: 排队最多等多久（秒）。等不到就快速失败——**排队不是无限的**，
    #: 否则请求会在舱门前堆成一串，越堆越慢，最后全体超时。
    queue_wait_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class RetryBudgetConfig:
    """重试预算：额外尝试最多占原始请求的多少比例。"""

    #: 0.25 = 每 4 次原始请求，最多允许多 1 次尝试。
    ratio: float = 0.25
    #: 突发上限：就算预算算出来是 0，也至少允许这么多次额外尝试，
    #: 否则一个刚重启的服务会**一次都不能重试**。
    burst: float = 2.0
    #: 每秒补充速率（按原始请求计）。
    refill_per_second: float = 1.0


class Bulkhead:
    """隔离舱：同时进行的上游调用数有上限，多出来的排队，排队超时就快速失败。"""

    def __init__(self, config: BulkheadConfig | None = None) -> None:
        self._config = config or BulkheadConfig()
        self._sem: asyncio.Semaphore | None = None
        self._rejections = 0
        self._peak = 0
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        """当前在飞数（舱满时把它写进错误详情：运维一眼看出是"真满了"还是"配置太小"）。"""

        return self._in_flight

    @property
    def rejections(self) -> int:
        return self._rejections

    @property
    def peak_in_flight(self) -> int:
        """历史峰值在飞数——验收隔离舱是否真的在生效，就看它。"""

        return self._peak

    def _get_semaphore(self) -> asyncio.Semaphore:
        # 名字不叫 `_semaphore`：那个名字已经被实例属性占了，
        # 属性会遮蔽同名方法，`self._semaphore()` 就变成对 None 调用。
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._config.capacity)
        return self._sem

    async def slot(self) -> None:
        try:
            await asyncio.wait_for(
                self._get_semaphore().acquire(), timeout=self._config.queue_wait_seconds
            )
        except TimeoutError as error:
            self._rejections += 1
            raise BulkheadFullError(
                f"模型调用排队超时（等 {self._config.queue_wait_seconds}s 没排上）"
                "：上游正在忙，稍后再试"
            ) from error
        self._in_flight += 1
        self._peak = max(self._peak, self._in_flight)

    def release(self) -> None:
        self._in_flight -= 1
        sem = self._sem
        if sem is not None:
            sem.release()

    async def run(self, work: Callable[[], object]) -> object:
        """在舱内跑一次异步工作（`work` 必须返回 awaitable），出来一定释放许可。"""

        await self.slot()
        try:
            return await work()
        finally:
            self.release()


class CircuitBreaker:
    """熔断器。**状态机是 pybreaker 的**，我们只做两件适配。

    **为什么不自己写判据**：2026-10-16 在真库（pybreaker 1.4.1）上实测过——
    它的 `CircuitClosedState.on_failure` 判据是 `counter >= fail_max`，而
    `_handle_success` 会 `reset_counter()`。所以它**没有滑动窗口失败率**，
    50% 失败率实测**永不开闸**（40 次交替调用跑完 `state` 仍是 `closed`），
    连续失败才开。我们采纳这个语义，而不是假装它有失败率。

    **适配只有两处**：
    1. **"什么算失败"交给库的 `exclude`**：只有 `classify()` 判成依赖不健康
       （timeout / 5xx / 限流）的才计入；用户输入错误、schema 不合这类
       **上游是好的、是我们自己的问题**绝不能开闸。
    2. **半开迁移要自己先做**（见 `call_upstream`）：库在 open 态收到调用时，
       `CircuitOpenState.before_call` 会调**同步的** `call()` 去跑探针，
       而我们要保护的是协程——同步 `call()` 只会拿到一个**从没被 await 的协程对象**，
       还把它当成"探针成功"当场闭合。实测会漏 `RuntimeWarning` 并错误恢复。
    """

    def __init__(self, config: BreakerConfig | None = None) -> None:
        config = config or BreakerConfig()
        self._config = config
        self._breaker = _PyBreaker(
            fail_max=config.minimum_samples,
            reset_timeout=config.recovery_timeout,
            success_threshold=config.success_threshold,
            # **"什么算失败"用库自己的 `exclude` 扩展点表达**，不另写一套判据：
            # 返回 True = 不计入失败 = 上游是好的，是我们自己的问题
            # （用户输入不对、schema 不合）。这类错绝不能开闸。
            exclude=[lambda exc: classify(exc) not in _DEPENDENCY_FAILURES],
            # 达标开闸时不要抛库异常：让它**照原样把上游的异常抛回去**，
            # 网关那边统一按 `classify()` 处理；换成 CircuitBreakerError
            # 就等于把"上游超时"变成"没人看得懂的 500"。
            throw_new_error_on_trip=False,
        )

    @property
    def is_open(self) -> bool:
        """闸门此刻是否拒绝新请求。"""

        try:
            self.allow()
        except CircuitOpenError:
            return True
        return False

    @property
    def failures(self) -> int:
        """连续依赖失败次数（开闸前用它确认"差几次就开"）。"""

        return self._breaker.fail_counter

    def allow(self) -> None:
        """开闸就直接拒——**不进隔离舱、不发请求、不花钱**。

        **这只是建议性的预检**：目的仅仅是"闸门关着就别占隔离舱的槽位"。
        真正的判定在 `call_upstream()` 里由库自己按 `reset_timeout` 做，
        所以即使这里的计时与库有出入，也不会让请求穿过一道本该关着的闸门。
        """

        if not self._recovered():
            raise CircuitOpenError("模型调用已熔断：上游正在恢复中，稍后再试")

    def _recovered(self) -> bool:
        """闸门此刻是否放行。**过了恢复窗口就在这里先切到半开**。

        计时读的是库自己记的 `_state_storage.opened_at`（私有字段，这是一处
        明确的私有读取）。曾经想自己用 `time.monotonic()` 记"第一次看见开闸"的
        时刻——那是错的：开闸发生在**上一次调用的记账里**，而观察发生在下一次，
        于是计时每次都被重置，恢复窗口永远等不满。
        库在每次 `open()`（含半开探测失败后的重开）都会刷新这个时间戳，
        它是唯一权威来源。
        """

        state = self._breaker.current_state
        if state != STATE_OPEN:
            # STATE_CLOSED 直接放行；STATE_HALF_OPEN 放行探测。
            return True
        opened_at = self._breaker._state_storage.opened_at
        if opened_at is not None and (
            datetime.now(UTC) < opened_at + timedelta(seconds=self._config.recovery_timeout)
        ):
            return False
        # 恢复窗口到了：**自己**用库的 `half_open()` 切过去。
        # 不能让库在 `before_call` 里切——那条路是同步的，跑不了协程。
        self._breaker.half_open()
        return True

    async def call_upstream(self, factory: Callable[[], Awaitable[_T]]) -> _T:
        """跑一次上游调用并交给 pybreaker 记账。

        用库的 `call_async` 而不是"先 allow 再 record"：半开时的探针**必须是
        那次真实调用**。实测踩过两个坑——

        * 拿假探针去问 `before_call`：它会把假探针的"成功"当成恢复成功，
          熔断器当场闭合，而上游其实还没好；
        * 不先迁移、让库自己在 `before_call` 里迁：那条路走**同步** `call()`，
          协程根本没被 await（`RuntimeWarning`），却被记成探针成功。

        所以先 `_recovered()`（含自己切半开），再让库用 `call_async` 跑真调用。
        """

        if not self._recovered():
            raise CircuitOpenError("模型调用已熔断：上游正在恢复中，稍后再试")
        try:
            return await self._breaker.call_async(factory)
        except CircuitBreakerError as error:
            raise CircuitOpenError("模型调用已熔断：上游正在恢复中，稍后再试") from error


class RetryBudget:
    """重试预算：令牌桶。额外尝试要先拿到令牌，拿不到就**不再重试**（直接失败）。

    没有预算的重试是放大器：上游抖动 → 全部调用方一起退避 → 一起重试 → 流量翻倍
    打到还在恢复中的上游。预算把这个倍数钉死。
    """

    def __init__(self, config: RetryBudgetConfig | None = None) -> None:
        self._config = config or RetryBudgetConfig()
        self._tokens = self._config.burst

    @property
    def tokens(self) -> float:
        return self._tokens

    def take(self) -> bool:
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    def refill(self) -> None:
        self._tokens = min(
            self._config.burst, self._tokens + self._config.refill_per_second
        )

    def record_original(self) -> None:
        """记一次原始请求（不是重试）：按比例补充预算。"""

        self._tokens = min(
            self._config.burst, self._tokens + self._config.ratio
        )


def jittered_backoff(base: float, *, ratio: float = 0.5, rng: random.Random | None = None) -> float:
    """退避加抖动。**抖动不是可选的优化**。

    固定退避会让所有同时失败的调用方**在同一时刻**一起重试（重试风暴），
    恰好在最需要喘息的时刻把上游再打一遍。抖动把重试摊开到 `[base*(1-ratio), base*(1+ratio)]`。
    """

    generator = rng or random
    return base * (1.0 + generator.uniform(-ratio, ratio))