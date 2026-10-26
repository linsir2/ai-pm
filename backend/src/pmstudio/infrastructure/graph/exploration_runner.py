"""探索运行器：一次请求内跑完 + 内存任务表（取消用）。与 `RoundRunner` 同形，不上图。

为什么不复用 `RoundRunner`：那是轮次的状态图入口；探索是**单次顺序调用**（没有状态路由），
套一张图只会多一处真相源。这里只做三件事：同项目串行（先占位再 await，检查是原子的）、
把当前任务登记下来供取消、起跑前清掉进程崩过留下的 `running` 僵壳。

跨进程互斥靠探索行上的 DB lease（P3b）：`RunInquiry.create` / `RetryExploration.resume`
在各自 UoW 里已经 acquire 好，runner **只 bind**，不二次 acquire；后台心跳续租，
失租把在途模型调用停下来并翻成 409。

取消的纪律与轮次逐字相同：**只让等模型的调用停下来，不写任何业务状态**——
状态由 `AbandonExploration` 先写（写法只有一处）。
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, TypeVar

from pmstudio.application.dto import ExplorationView
from pmstudio.application.errors import ExplorationBusy, ExplorationLeaseLost
from pmstudio.application.lease import (
    LEASE_HEARTBEAT_SECONDS,
    LEASE_TTL_SECONDS,
    LeaseHeartbeat,
    LeaseScope,
    clear_lease_scope,
    set_lease_scope,
    wall_clock_ms,
)
from pmstudio.application.ports import LeaseStore
from pmstudio.application.use_cases import RetryExploration, RunInquiry
from pmstudio.domain.exploration.exploration import ExplorationScope
from pmstudio.domain.ports import ExplorationReader
from pmstudio.domain.shared.ids import ExplorationId, ProjectId, UserId
from pmstudio.domain.shared.lease import LeaseToken
from pmstudio.infrastructure.sqlite.owner_scope import require_owner

__all__ = ["ExplorationRunner"]

CANCEL_WAIT_SECONDS = 5.0
_T = TypeVar("_T")


class ExplorationRunner:
    def __init__(
        self,
        *,
        run_inquiry: RunInquiry,
        retry_exploration: RetryExploration,
        explorations: ExplorationReader,
        leases: LeaseStore | None = None,
        lease_ttl_seconds: float = LEASE_TTL_SECONDS,
        lease_heartbeat_seconds: float = LEASE_HEARTBEAT_SECONDS,
    ) -> None:
        self._run_inquiry = run_inquiry
        self._retry = retry_exploration
        self._explorations = explorations
        self._leases = leases
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_heartbeat_seconds = lease_heartbeat_seconds
        self._running_projects: set[str] = set()
        #: 正在跑的那个 asyncio 任务（`cancel()` 要按 exploration_id 找到它）
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def start(
        self, *, project_id: ProjectId, scope: ExplorationScope
    ) -> ExplorationView:
        key = str(project_id)
        if key in self._running_projects:
            raise ExplorationBusy(project_id)
        # 先占位、再 await：中间没有让出事件循环的点，两次并发请求穿不过去
        self._running_projects.add(key)
        try:
            owner = self._owner()
            # 内存里没有、库里还挂着 running 的 = 上次进程崩过的僵壳：就地判放弃。
            # 活租约（另一个 worker 正在跑）不会被清，下面 create 会撞唯一索引 → 409。
            await self._run_inquiry.clear_stale(project_id, owner=owner)
            started = await self._run_inquiry.create(
                project_id=project_id, scope=scope, owner=owner
            )
            exploration_id = started.exploration_id
            current = asyncio.current_task()
            if current is not None:
                self._tasks[str(exploration_id)] = current
            try:
                return await self._run_with_lease(
                    exploration_id,
                    started.token,
                    lambda: self._run_inquiry.execute(exploration_id),
                )
            finally:
                self._tasks.pop(str(exploration_id), None)
        finally:
            self._running_projects.discard(key)

    async def retry(self, *, exploration_id: str) -> ExplorationView:
        xid = ExplorationId(exploration_id)
        exploration = self._explorations.get(xid)
        key = str(exploration.project_id)
        if key in self._running_projects:
            raise ExplorationBusy(exploration.project_id)
        self._running_projects.add(key)
        try:
            owner = self._owner()
            # 同项目有进程崩过的 running 时，resume 置 RUNNING 会撞唯一索引；
            # 先按同一条判据清僵壳（活租约不动）。
            await self._run_inquiry.clear_stale(exploration.project_id, owner=owner)
            resumed = await self._retry.resume(xid, owner=owner)
            current = asyncio.current_task()
            if current is not None:
                self._tasks[str(xid)] = current
            try:
                return await self._run_with_lease(
                    xid,
                    resumed.token,
                    lambda: self._run_inquiry.execute(xid, retry_hint=resumed.hint),
                )
            finally:
                self._tasks.pop(str(xid), None)
        finally:
            self._running_projects.discard(key)

    def _owner(self) -> UserId | None:
        """DB 租约要用归属；没注入 lease store（Harness/内存夹具）时不需要。"""

        if self._leases is None:
            return None
        return require_owner(None)

    async def _run_with_lease(
        self,
        exploration_id: ExplorationId,
        token: LeaseToken | None,
        run: Callable[[], Awaitable[_T]],
    ) -> _T:
        """把 create/resume 已拿到的 token 绑进 contextvar，带心跳跑完这次动作。"""

        if token is None:
            return await run()
        lease = token
        scope = LeaseScope(holder=lease.holder, owner=UserId(lease.user_id))
        scope.bind(lease)
        context_token = set_lease_scope(scope)
        ttl_ms = int(self._lease_ttl_seconds * 1000)
        main = asyncio.current_task()

        def _stop_main() -> None:
            if main is not None and not main.done():
                main.cancel()

        heartbeat = asyncio.create_task(
            LeaseHeartbeat(
                scope=scope,
                renew=lambda item, now_ms, _ttl: self._leases.renew(
                    item.kind,
                    item.target_id,
                    UserId(item.user_id),
                    item.holder,
                    item.token,
                    now_ms,
                    _ttl,
                ),
                interval_seconds=self._lease_heartbeat_seconds,
                ttl_ms=ttl_ms,
                on_lost=_stop_main,
            ).run()
        )
        try:
            try:
                return await run()
            except asyncio.CancelledError:
                if scope.lost:
                    # 心跳先发现锁被接管：对调用方是 409，不是"用户放弃了"。
                    raise ExplorationLeaseLost(exploration_id) from None
                # 外部取消（放弃 / 进程关闭）原样抛：谁取消的谁负责收尾。
                raise
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
            # 尽力而为：0 行（被接管/已过期）不是错误，也绝不报 409。
            try:
                self._leases.release(
                    lease.kind,
                    lease.target_id,
                    UserId(lease.user_id),
                    lease.holder,
                    lease.token,
                    now_ms=wall_clock_ms(),
                )
            except Exception:  # noqa: BLE001 —— 收尾不许掩盖原异常
                pass
            clear_lease_scope(context_token)

    async def cancel(self, exploration_id: ExplorationId) -> None:
        """取消正在跑的那一趟。没有在跑的（停在等人 / 进程崩过）直接返回。"""

        task = self._tasks.get(str(exploration_id))
        if task is None or task.done():
            return
        task.cancel()
        # 等它退出。用 wait 不用 wait_for：wait_for 超时会**再取消一次**，那不是帮忙。
        await asyncio.wait({task}, timeout=CANCEL_WAIT_SECONDS)
