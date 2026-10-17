"""SSE：只推事件，不做业务。心跳保证连接不被中间层掐掉；按 owner 裁剪保证不漏投。

多用户改造 Task 6：事件本身仍是全局总线（订阅者能收到**所有人**项目的通知），
归属裁剪放在这一层——`event_stream` 拿到一条事件先问 `visible`，不是自己的直接跳过。
**不动总线/发布侧的机制**（`harness.events`、各用例的 `publish`），只在这一处收口。
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pmstudio.harness.events import Event, EventStream

DEFAULT_HEARTBEAT_SECONDS = 15.0


def as_json(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return {key: as_json(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): as_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [as_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def owner_visible(
    *, scope: Any, list_owned: Any, owner: Any
) -> Callable[[Event], Awaitable[bool]]:
    """造一个"这条事件的项目归当前用户吗"的判据（按 owner 裁剪 SSE）。

    - `scope`：owner 上下文模块（`set_owner_scope` / `clear_owner_scope`）。**每次判断
      都要在 scope 下取**，因为 SSE 生成器是在请求依赖收尾之后才跑的（见 `routes.events`）。
    - `list_owned`：为零参的 async 可调用，返回当前 scope 下该用户拥有的项目摘要
      （生产传 `services.list_projects`）。遇到没判过的项目 id 才重取一次（新项目会在
      连接建立之后才出现，一次性快照会把它漏掉）。

    每次判断都恢复 scope（`finally`），绝不把归属漏出去。

    归属**只判一次就缓存**（owner 写入后不可改，§0.7）：别人的项目永远不会变成自己的，
    自己的也不会变别人的。所以缓存布尔值安全，且避免每条外部事件都查一次库。

    **事件自带 `owner_id` 时不查库**（P4 多 worker）：多 worker 下每条 worker 都会
    收到**全部**事件，落到本 worker 的连接上要一条条判归属——查库判据会让每个陌生
    project_id 都触发一次全表聚合查询，200 个连接 × N 个用户 × M 个项目就是 NM 次。
    事件上的 owner 是投递侧盖的，与"归属写入后不可改"是同一条依据。

    **`owner_id` 缺失 ≠ 可见**：回落到查库判定。只有查不到归属才放行，
    这条路径是"没盖到章"的兜底，不是快路。
    """

    seen: dict[str, bool] = {}

    async def visible(event: Event) -> bool:
        if event.owner_id is not None:
            return event.owner_id == str(owner)
        project_id = event.project_id
        if project_id in seen:
            return seen[project_id]
        token = scope.set_owner_scope(owner)
        try:
            owned = await list_owned()
        finally:
            scope.clear_owner_scope(token)
        owned_ids = {str(item.project_id) for item in owned}
        seen[project_id] = project_id in owned_ids
        return seen[project_id]

    return visible


async def event_stream(
    stream: EventStream,
    *,
    heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
    visible: Callable[[Event], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    iterator = stream.subscribe()
    try:
        # 建流先发一行注释：`EventSource` 的 onopen 因此立刻到，顶栏不用等 15 秒心跳
        # 才显示"已连接"（`04-未决.md` 界面侧-2）。
        # 放在 `try` 里面（我第一版写在外面）：虽然"刚建流就断开"时内层生成器
        # 还没启动、确实没有订阅者可漏，但把它放进 try 让"发完 ready 就断"与
        # "进了循环才断"走同一条清理路径，少一条要推理的分支。
        yield ": ready\n\n"
        while True:
            try:
                event = await asyncio.wait_for(
                    anext(iterator), timeout=heartbeat_seconds
                )
            except TimeoutError:
                yield ": ping\n\n"
                continue
            except StopAsyncIteration:
                return
            if visible is not None and not await visible(event):
                continue
            payload = json.dumps(as_json(event), ensure_ascii=False)
            yield f"event: {event.name}\ndata: {payload}\n\n"
    finally:
        # **必须显式关掉内层迭代器**（P4 顺手修的现存泄漏）：客户端断连时
        # `StreamingResponse` 抛异常打断 `async for`，没有这个 `finally` 的话
        # 内层 `subscribe()` 的清理要等异步生成器被 GC 才跑——订阅者摘除是
        # **非确定性**的。单 worker 下泄漏慢，多 worker 下速率 ×N。
        close = getattr(iterator, "aclose", None)
        if close is not None:
            with contextlib.suppress(Exception):
                await close()
