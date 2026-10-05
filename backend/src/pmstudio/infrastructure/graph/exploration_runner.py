"""探索运行器：一次请求内跑完 + 内存任务表（取消用）。与 `RoundRunner` 同形，不上图。

为什么不复用 `RoundRunner`：那是轮次的状态图入口；探索是**单次顺序调用**（没有状态路由），
套一张图只会多一处真相源。这里只做三件事：同项目串行（先占位再 await，检查是原子的）、
把当前任务登记下来供取消、起跑前清掉进程崩过留下的 `running` 僵壳。

取消的纪律与轮次逐字相同：**只让等模型的调用停下来，不写任何业务状态**——
状态由 `AbandonExploration` 先写（写法只有一处）。
"""

import asyncio
from typing import Any

from pmstudio.application.dto import ExplorationView
from pmstudio.application.errors import ExplorationBusy
from pmstudio.application.use_cases import RetryExploration, RunInquiry
from pmstudio.domain.exploration.exploration import ExplorationScope
from pmstudio.domain.ports import ExplorationReader
from pmstudio.domain.shared.ids import ExplorationId, ProjectId

__all__ = ["ExplorationRunner"]

CANCEL_WAIT_SECONDS = 5.0


class ExplorationRunner:
    def __init__(
        self,
        *,
        run_inquiry: RunInquiry,
        retry_exploration: RetryExploration,
        explorations: ExplorationReader,
    ) -> None:
        self._run_inquiry = run_inquiry
        self._retry = retry_exploration
        self._explorations = explorations
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
            # 内存里没有、库里还挂着 running 的 = 上次进程崩过的僵壳：就地判放弃
            await self._run_inquiry.clear_stale(project_id)
            exploration_id = await self._run_inquiry.create(
                project_id=project_id, scope=scope
            )
            current = asyncio.current_task()
            if current is not None:
                self._tasks[str(exploration_id)] = current
            try:
                return await self._run_inquiry.execute(exploration_id)
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
            current = asyncio.current_task()
            if current is not None:
                self._tasks[str(xid)] = current
            try:
                hint = await self._retry.resume(xid)
                return await self._run_inquiry.execute(xid, retry_hint=hint)
            finally:
                self._tasks.pop(str(xid), None)
        finally:
            self._running_projects.discard(key)

    async def cancel(self, exploration_id: ExplorationId) -> None:
        """取消正在跑的那一趟。没有在跑的（停在等人 / 进程崩过）直接返回。"""

        task = self._tasks.get(str(exploration_id))
        if task is None or task.done():
            return
        task.cancel()
        # 等它退出。用 wait 不用 wait_for：wait_for 超时会**再取消一次**，那不是帮忙。
        await asyncio.wait({task}, timeout=CANCEL_WAIT_SECONDS)
