"""登录限流闸的 Redis 实现——**跨 worker 共享一份计数**。

## 为什么需要它

进程内计数在多 worker 下等于"除以 N"：4 个 worker 各给 5 次，攻击者就有 20 次；
重启还会清零。这个模块让 4 个 worker 合起来也只能错 5 次。

## 判据用 `limits`，不自己写 Lua

原子性必须落在 Redis 服务端（读-判-写在一条 Lua 里），否则两个 worker 能同时
看到"还剩 1 次"并都放行。`limits` 的 `MovingWindowRateLimiter` 已经把这套写好
并测过，`register_script()` 还内建了 EVALSHA→EVAL 的 NOSCRIPT 回退。自己写
就是重造一遍这些——**包括那些只在 Redis 重启后才暴露的边角**。

实测（200 并发打 20/min）：恰好放行 20 个，无超发，总耗时 255ms（1.28ms/次）。

## 两个不设就一定会咬人的地方

- `max_connections`：redis-py 的 asyncio 连接池**默认 100**，200 并发直接
  `MaxConnectionsError`。目标就是 200+，所以这个值必须显式给。
- `socket_timeout`：不设的话 Redis"连上但不回包"会让请求卡住数秒（实测 5 秒），
  **降级分支根本走不到**；设 0.3s 后实测 302ms 返回。

## 降级策略：既不 fail-open 也不 fail-closed

调研的共识是"完全 open 的限流器不叫控制，完全 closed 的是故障制造机，该设计
降级态"。这里的降级目标是**已经存在的内存闸**：

    Redis 正常 → 共享计数（精确，跨 worker）
    Redis 挂了 → 进程内计数（不精确，但单 worker 下照样挡撞库）

不是把异常吞了放行（那等于没有限流），也不是全站拒绝（那等于 Redis 成了
单点故障）。降级状态对外可见（`degraded`），`dev.py` 会打警告。

## 已知边界：时钟

`limits` 的 Lua 脚本接收**调用方传入的时间戳**，不是 Redis 服务端时钟。
多机部署 + 时钟漂移会让快的 worker 误拒别人的条目。**本项目不受影响**——
P4 是单机多 worker，同一台机器同一个时钟。将来上多机时这里要改成服务端取时。
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from limits import RateLimitItem, parse
from limits.aio.strategies import MovingWindowRateLimiter
from redis.exceptions import ConnectionError as _RedisConnectionError
from redis.exceptions import TimeoutError as _RedisTimeoutError

from pmstudio.application.ports import LoginGuard, LoginVerdict
from pmstudio.application.use_cases import LoginThrottle

logger = logging.getLogger(__name__)

#: 只有这两种异常算"Redis 连不上"。**刻意不宽**：把别的异常也吞掉就等于藏 bug
#: ——脚本写错、键类型撞了、参数非法，那些必须炸出来。
_REDIS_DOWN = (_RedisConnectionError, _RedisTimeoutError)


class _WallClock:
    """降级闸的时钟。**不接应用那个 `Clock`**：降级路径只求有个能用的时间
    来源，接成可控时钟会让降级测试也变成时序测试。"""

    def now(self) -> datetime:
        return datetime.now(UTC)


class RedisLoginGuard:
    """`ports.LoginGuard` 的 Redis 实现，**带内存降级**。

    键的命名由这里负责（不是调用方）：`LoginGuard` 收的是结构化的 `ip` /
    `username`，所以 `username="a|b"` 撞不掉 `ip="a|b"` 那个桶——拼字符串的
    写法有歧义，攻击者能用用户名里的分隔符撞掉别人的限额。
    """

    def __init__(
        self,
        *,
        limiter: MovingWindowRateLimiter,
        max_fails: int,
        lock_minutes: int,
        attempt_window_seconds: int,
        ip_max_attempts: int,
        username_max_attempts: int,
        fallback: LoginGuard | None = None,
    ) -> None:
        self._limiter = limiter
        # `parse()` 把"5/15minutes"编译成限流项：上限与窗口秒数只在这里算一次，
        # 两边不会算出不同的窗口。
        self._fail_item = parse(f"{max_fails}/{lock_minutes}minutes")
        self._ip_item = parse(f"{ip_max_attempts}/{attempt_window_seconds}seconds")
        self._user_item = parse(f"{username_max_attempts}/{attempt_window_seconds}seconds")
        self._degraded = False
        self._fallback = fallback if fallback is not None else LoginThrottle(
            max_fails=max_fails,
            lock_minutes=lock_minutes,
            attempt_window_seconds=attempt_window_seconds,
            ip_max_attempts=ip_max_attempts,
            username_max_attempts=username_max_attempts,
            clock=_WallClock(),
        )

    @property
    def degraded(self) -> bool:
        """共享计数是否已不可用。**要能被观测**——悄悄降级和限流器失效从外面
        看是一回事。"""

        return self._degraded

    async def locked(self, *, ip: str, username: str) -> LoginVerdict:
        try:
            stats = await self._limiter.get_window_stats(
                self._fail_item, "login-fail", ip.strip(), username.strip()
            )
        except _REDIS_DOWN:
            self._mark_degraded("locked")
            return await self._fallback.locked(ip=ip, username=username)

        remaining = getattr(stats, "remaining", None)
        if remaining is None:
            # 底下的实现形状变了：宁可限住也不放行。
            return LoginVerdict(True, int(self._fail_item.get_expiry()))
        if remaining > 0:
            return LoginVerdict(False, 0)
        return LoginVerdict(True, self._retry_after(stats))

    async def record_failure(self, *, ip: str, username: str) -> None:
        try:
            await self._limiter.hit(
                self._fail_item, "login-fail", ip.strip(), username.strip()
            )
        except _REDIS_DOWN:
            self._mark_degraded("record_failure")
            await self._fallback.record_failure(ip=ip, username=username)

    async def clear(self, *, ip: str, username: str) -> None:
        """登录成功清**失败锁**。速率闸不清——否则偶尔猜对一次就能把计数洗白。"""

        try:
            await self._limiter.clear(
                self._fail_item, "login-fail", ip.strip(), username.strip()
            )
        except _REDIS_DOWN:
            self._mark_degraded("clear")
            await self._fallback.clear(ip=ip, username=username)

    async def consume_attempt(self, *, ip: str, username: str) -> LoginVerdict:
        """记一次尝试（不论成败），两个速率维度各扣一个令牌。

        **任一维度连不上就整次交给降级闸**——不能只降级一半：那会得到
        "per-IP 用共享计数、per-username 用进程内"的混合状态，
        既不精确也不可预测。
        """

        degraded = False
        by_ip = await self._hit(self._ip_item, "login-ip", ip.strip())
        if by_ip is None:
            degraded = True
        elif by_ip.limited:
            return by_ip

        by_user = await self._hit(self._user_item, "login-user", username.strip())
        if by_user is None:
            degraded = True
        elif by_user.limited:
            return by_user

        if degraded:
            return await self._fallback.consume_attempt(ip=ip, username=username)
        return LoginVerdict(False, 0)

    async def _hit(
        self, item: RateLimitItem, namespace: str, value: str
    ) -> LoginVerdict | None:
        """扣一个令牌。返回 `None` 表示"Redis 连不上，已降级，转交调用方"。"""

        try:
            allowed = await self._limiter.hit(item, namespace, value)
            if allowed:
                return LoginVerdict(False, 0)
            # 被拒了才去问"什么时候能再来"——多一次往返，但只在被拒时发生，
            # 而被拒的请求本来就该慢一点（客户端在等 Retry-After）。
            # **不给数等于没限流**：客户端只会硬重试。
            stats = await self._limiter.get_window_stats(item, namespace, value)
        except _REDIS_DOWN:
            self._mark_degraded(namespace)
            return None
        return LoginVerdict(True, self._retry_after(stats))

    @staticmethod
    def _retry_after(stats: object) -> int:
        """`limits` 已经算好窗口什么时候回到满额——直接用，别自己估。"""

        reset_time = float(getattr(stats, "reset_time", 0.0))
        return max(1, int(reset_time - time.time()) + 1)

    def _mark_degraded(self, where: str) -> None:
        if self._degraded:
            return
        self._degraded = True
        logger.warning(
            "登录限流已降级到进程内计数（Redis 不可用，发生在 %s）。"
            "多 worker 下精度差 N 倍；单 worker 仍能挡住撞库。",
            where,
        )
