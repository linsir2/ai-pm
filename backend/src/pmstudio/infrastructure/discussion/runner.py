"""讨论 panel 轮次的执行器：串行 + 取消（照 `ExplorationRunner` 的样子）。

一个动作 = 一次 run-round 请求内跑完一轮（轮内并行发言 + 综合）。
同一个 discussion 同时只允许一轮在跑——双击"再跑一轮"、两个标签页、
前端自动循环重发都会撞上；撞上就 `DiscussionBusy`，不排队（排队会掩盖"卡住了"）。
取消只让在途调用停下来，**不写状态**：已落库的 turn 照留，线程还是 open，
停下来等它或直接再跑都行（写状态是 CloseDiscussion 的活）。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pmstudio.application.dto import ThreadView
from pmstudio.application.errors import DiscussionBusy
from pmstudio.application.use_cases import RunPanelRound
from pmstudio.domain.ports import DiscussionReader
from pmstudio.domain.shared.ids import DiscussionId

__all__ = ["CANCEL_WAIT_SECONDS", "DiscussionRunner"]

#: `cancel()` 之后等任务退出的上限（照 exploration_runner；用 wait 不用 wait_for，
#: wait_for 超时会**再取消一次**，那不是帮忙）。
CANCEL_WAIT_SECONDS = 5.0


class DiscussionRunner:
    def __init__(
        self, *, run_panel_round: RunPanelRound, discussions: DiscussionReader
    ) -> None:
        self._run_panel_round = run_panel_round
        self._discussions = discussions
        self._running: set[str] = set()
        #: 正在跑的那一轮的 asyncio 任务（`cancel()` 要按 discussion_id 找到它）
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    async def run_round(self, *, discussion_id: DiscussionId) -> ThreadView:
        """跑一轮。串行化 + 任务注册（abort 要能找到它）。"""
        key = str(discussion_id)
        if key in self._running:
            raise DiscussionBusy(discussion_id)
        # 先占位、再 await：中间没有让出事件循环的点，两次并发请求穿不过去
        self._running.add(key)
        try:
            current = asyncio.current_task()
            if current is not None:
                self._tasks[key] = current
            try:
                return await self._run_panel_round(discussion_id=discussion_id)
            finally:
                self._tasks.pop(key, None)
        finally:
            self._running.discard(key)

    async def abort(self, *, discussion_id: DiscussionId) -> bool:
        """停掉正在跑的那一轮。没有在跑的直接返回 False（幂等成功）。

        **归属先于副作用**（Task 6 fix round 2）：先按 owner 读线程，跨 owner 即
        `UnknownDiscussion`（→404）——绝不取消别人的在途调用。任务表是**进程内全局**的
        （按 discussion_id 索引），不先鉴权的话，任何登录用户拿到别人的 id 就能掐停对方。
        """

        self._discussions.get(discussion_id)
        task = self._tasks.get(str(discussion_id))
        if task is None or task.done():
            return False
        task.cancel()
        # 等它退出。用 wait 不用 wait_for：wait_for 超时会**再取消一次**，那不是帮忙。
        await asyncio.wait({task}, timeout=CANCEL_WAIT_SECONDS)
        return True
