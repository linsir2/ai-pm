"""租约的进程内把手：一个**可变** `LeaseScope` 放进 contextvar。

为什么是可变对象而不是不可变值：LangGraph 的节点会复制 context，但复制的是**引用**；
`RoundRunner.run` 在 `ainvoke` 之前把同一个 scope 放进 contextvar，`StartRound.create`
拿到租约后 `bind` 到它上面，后续 `node_analyze` 里的 `Repository.save` 才能看见同一把锁。
如果每一层都 `set` 一个新值，节点复制到的是旧值——那条路在真跑里会 fencing 失败。

token **不进** `Round` / `Exploration` 聚合：聚合落库时不该带"谁在跑我"这种运行时事实，
否则重放/迁移一份 payload 会把一把早已过期的锁一起搬过去。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from dataclasses import replace

from pmstudio.application.errors import LeaseLost
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseToken

#: 租约有效期；心跳按 `LEASE_HEARTBEAT_SECONDS` 续租（Task 2/3 接线）。
LEASE_TTL_SECONDS = 90
LEASE_HEARTBEAT_SECONDS = 30


def wall_clock_ms() -> int:
    """UTC epoch 毫秒。租约只用这一个时钟口径（跨进程共享系统时钟）。"""

    return time.time_ns() // 1_000_000


class LeaseScope:
    """当前动作持有的那一把租约（可绑定、可标记丢失、可清空）。

    `ensure_held()` 只做**本地**判据（有没有 token、是否已标记丢失、本地记的过期时间）；
    真正的 fencing 在仓储的 SQL 条件里（`lease_owner` + `lease_token` +
    `lease_expires_at > now`），两者缺一不可。

    `holder / owner` 在**拿到 token 之前**就要有值：`StartRound.create` 要先 INSERT
    再在同一 UoW 里 `acquire`，那一刻 token 还不存在，只能从 scope 上取"谁在跑"。
    """

    def __init__(self, *, holder: str = "", owner: UserId | None = None) -> None:
        self._token: LeaseToken | None = None
        self._lost = False
        self._holder = holder
        self._owner = owner

    @property
    def token(self) -> LeaseToken | None:
        return self._token

    @property
    def holder(self) -> str:
        return self._token.holder if self._token is not None else self._holder

    @property
    def owner(self) -> UserId | None:
        if self._token is not None:
            return UserId(self._token.user_id)
        return self._owner

    @property
    def lost(self) -> bool:
        return self._lost

    def bind(self, token: LeaseToken) -> None:
        """绑定（或重新绑定）一把租约；重新绑定会清掉 lost 标记。"""

        self._token = token
        self._lost = False

    def note_renewed(self, expires_at_ms: int) -> None:
        """心跳续租成功后更新本地过期时间（token 本身不变）。"""

        if self._token is None:
            return
        self._token = replace(self._token, expires_at_ms=expires_at_ms)

    def mark_lost(self) -> None:
        self._lost = True

    def clear(self) -> None:
        self._token = None
        self._lost = False

    def ensure_held(self, *, now_ms: int | None = None) -> LeaseToken:
        """本地复核；不满足就抛 `LeaseLost`（调用方不要把它吞成"继续写"）。"""

        token = self._token
        if token is None or self._lost:
            raise LeaseLost(
                kind=token.kind if token is not None else "none",
                target_id=token.target_id if token is not None else "",
            )
        now = wall_clock_ms() if now_ms is None else now_ms
        if token.expires_at_ms <= now:
            self._lost = True
            raise LeaseLost(kind=token.kind, target_id=token.target_id)
        return token


RenewLease = Callable[[LeaseToken, int, int], bool]


class LeaseHeartbeat:
    """后台续租：按 `interval_seconds` 调 `renew(token, now_ms, ttl_ms)`。

    两条纪律：

    - **`database is locked` 不是失租**：短退避重试，直到续上或真的过期；
    - **只有 renew 返回 False 才置 lost**（owner/持有者/token 不匹配，或行已过期）。
      置 lost 后调 `on_lost()`（runner 用它取消主任务），循环退出。

    第一次续租要等 `StartRound.create` 把 token `bind` 进来；那之前每个 interval
    只是空转一次，所以**不能**把"token 还不存在"当成失租。
    """

    def __init__(
        self,
        *,
        scope: LeaseScope,
        renew: RenewLease,
        interval_seconds: float,
        ttl_ms: int,
        on_lost: Callable[[], None],
        sleep: Callable[[float], Awaitable[None]] | None = None,
        clock: Callable[[], int] = wall_clock_ms,
        retry_initial_seconds: float = 0.05,
        retry_max_seconds: float = 1.0,
    ) -> None:
        self._scope = scope
        self._renew = renew
        self._interval = interval_seconds
        self._ttl_ms = ttl_ms
        self._on_lost = on_lost
        self._sleep = sleep or asyncio.sleep
        self._clock = clock
        self._retry_initial = retry_initial_seconds
        self._retry_max = retry_max_seconds

    async def run(self) -> None:
        """一直续到失租或任务被取消（取消由调用方负责，runner 的 finally 会收掉）。"""

        while True:
            if not await self.beat_once():
                return
            await self._sleep(self._interval)

    async def beat_once(self) -> bool:
        """续一次。返回 False = 失租（已 `mark_lost` 并回调）；True = 续上/尚无 token。"""

        if self._scope.lost:
            return False
        token = self._scope.token
        if token is None:
            return True
        backoff = self._retry_initial
        while True:
            try:
                now_ms = self._clock()
                renewed = self._renew(token, now_ms, self._ttl_ms)
            except Exception as error:  # noqa: BLE001 —— 只把"锁忙"当可重试
                if not _database_locked(error):
                    raise
                await self._sleep(backoff)
                backoff = min(backoff * 2, self._retry_max)
                continue
            if not renewed:
                self._scope.mark_lost()
                self._on_lost()
                return False
            self._scope.note_renewed(now_ms + self._ttl_ms)
            return True


def _database_locked(error: BaseException) -> bool:
    text = str(error).lower()
    return "locked" in text or "busy" in text


_current: ContextVar[LeaseScope | None] = ContextVar("pmstudio_lease_scope", default=None)


def set_lease_scope(scope: LeaseScope) -> Token[LeaseScope | None]:
    """把当前上下文（async 任务）的租约把手设成 `scope`，返回可还原的 token。"""

    return _current.set(scope)


def current_lease_scope() -> LeaseScope | None:
    """当前上下文的租约把手；没设过就是 `None`。"""

    return _current.get()


def clear_lease_scope(token: Token[LeaseScope | None] | None = None) -> None:
    """还原上下文：给了 `set_lease_scope` 的 token 就还原，否则清空。"""

    if token is None:
        _current.set(None)
    else:
        _current.reset(token)
