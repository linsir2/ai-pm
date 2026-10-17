"""跨 worker 事件扇出（高并发 P4）——**一条共享订阅 + 进程内扇出**。

## 为什么不是"每条 SSE 连接订阅一次 Redis"

Redis 官方文档与实测都指向同一结论：Redis 把消息投给**订阅连接**，
不是投给 SSE 客户端。每连接一条订阅会被 `maxclients`（默认 10000）打死，
而这里每 worker 只要 **1 条**订阅连接，扇出在进程内做。

实测（本机，200 并发前）：一次 `PUBLISH` 0.30ms；1000 次 publish / 298ms，
两个订阅者各收全 1001 条。

## 为什么本地不直投、靠 Redis 回声

Redis 会把消息回送给**所有**订阅者，包括发布者自己。本地再直投一次 =
每个事件两次。靠回声 = 单一投递路径，且**每个连接看到同一条有序流**。

## 为什么 `publish()` 是"入队 + 后台 PUBLISH"

`EventPublisher.publish` 是**同步**契约（`harness/events/schemas.py`）。
改异步要 `await` 37 个业务调用点；引入 `asyncio.create_task` 又会造出一个
无人 await 的生命周期风险（全仓原本零 `create_task`）。入队 + 已有的后台
任务排空，两头都不占：发布方永远不 await，Redis 出错只在一个地方处理。

## 降级：Redis 挂了怎么办

**实测出来的坑**：`ps.listen()` 那个异步迭代器在连接断开时**静默结束、
不抛异常**。不加检测的话 worker 根本不知道 Redis 死了，界面会静默不更新。
所以 reader 任务正常结束就置 `degraded` 并让 drainer 停止出队。

降级目标是**进程内总线**（`_local`），与 P3 登录限流同一条规矩：
既不 fail-open（界面不更新）也不 fail-closed（全站不可用）。
代价是跨 worker 的事件会漏——**事件只是"去重读"的信号**，下一次 refread 会补上。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as _RedisConnectionError
from redis.exceptions import TimeoutError as _RedisTimeoutError

from pmstudio.harness.events import Event, InProcessEventBus
from pmstudio.infrastructure.sqlite import owner_scope

logger = logging.getLogger(__name__)

#: 只有这两种异常算"Redis 连不上"。**刻意不宽**：把别的异常吞掉就等于藏 bug。
_REDIS_DOWN = (_RedisConnectionError, _RedisTimeoutError)

#: 出站队列容量。满了丢最旧——与 `InProcessEventBus` 对订阅队列的处理一致。
DEFAULT_OUTBOUND_QUEUE = 1024


def _to_json(event: Event) -> str:
    return json.dumps(
        {
            "name": event.name,
            "project_id": event.project_id,
            "occurred_at": event.occurred_at.isoformat(),
            "owner_id": event.owner_id,
            "round_id": event.round_id,
            "payload": dict(event.payload),
        },
        ensure_ascii=False,
    )


def _from_json(raw: str | bytes) -> Event:
    data: dict[str, Any] = json.loads(raw)
    return Event(
        name=data["name"],
        project_id=data["project_id"],
        occurred_at=datetime.fromisoformat(data["occurred_at"]),
        owner_id=data.get("owner_id"),
        round_id=data.get("round_id"),
        payload=data.get("payload") or {},
    )


class RedisFanoutEventBus:
    """`EventPublisher` + `EventStream` 的 Redis 实现，**带进程内降级**。

    生命周期由 `api/app.py` 的 lifespan 管：`start()` 起 reader/drainer 两个任务，
    `stop()` 收掉它们并关连接。**不自己起任务**——谁创建谁负责关，
    否则 `--reload` 每热重载一次就泄漏一个任务 + 一组 Redis 连接。
    """

    def __init__(
        self,
        *,
        url: str,
        channel: str = "pmstudio:events",
        queue_size: int = DEFAULT_OUTBOUND_QUEUE,
        socket_timeout: float = 0.3,
        max_connections: int = 256,
    ) -> None:
        self._url = url
        self._channel = channel
        self._queue_size = queue_size
        self._socket_timeout = socket_timeout
        self._max_connections = max_connections
        self._local = InProcessEventBus()
        self._outbound: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._redis: Any = None
        self._pubsub: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._drainer: asyncio.Task[None] | None = None
        self._degraded = False
        self._warned = False
        self._dropped = 0

    # ── 可观测 ──────────────────────────────────────────────────────────────

    @property
    def degraded(self) -> bool:
        """共享扇出是否已不可用。**要能被观测**——悄悄降级和"扇出不存在"
        从外面看是一回事。"""

        return self._degraded

    @property
    def dropped(self) -> bool:
        """出站队列溢出丢弃过（Redis 慢到出不去）。丢得起——事件只是通知，
        下一次 refread 会补上——但要**看得见**。"""

        return self._dropped > 0

    @property
    def local_subscriber_count(self) -> int:
        return self._local.subscriber_count

    @property
    def reader(self) -> asyncio.Task[None] | None:
        return self._reader

    @property
    def drainer(self) -> asyncio.Task[None] | None:
        return self._drainer

    # ── 生命周期 ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """起 reader（收）与 drainer（发）。**重复调用是安全的**：lifespan 可能被调多次。"""

        if self._reader is not None and not self._reader.done():
            return
        self._reader = asyncio.create_task(self._read_loop(), name="pmstudio-events-reader")
        self._drainer = asyncio.create_task(
            self._drain_loop(), name="pmstudio-events-drainer"
        )

    async def stop(self) -> None:
        """收掉两个任务并关连接。**幂等**：关停路径上可能被重复调到。"""

        for task in (self._reader, self._drainer):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._reader = None
        self._drainer = None
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
            self._pubsub = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    # ── 发布 ────────────────────────────────────────────────────────────────

    def publish(self, event: Event) -> None:
        """同步发布。**永远不 await、永远不阻塞**——它跑在请求关键路径上。

        owner 在这里盖章：调用发生在请求任务内（`api/deps.py` 设的 contextvar
        在同一个任务里），读得到归属。业务侧盖章要透传 41 处，且分层守卫
        规定 application 根本 import 不到 `owner_scope`。
        """

        stamped = event
        if event.owner_id is None:
            current = owner_scope.current_owner_scope()
            if current is not None:
                stamped = replace(event, owner_id=str(current))
        payload = _to_json(stamped)
        if self._degraded:
            self._local.publish(stamped)
            return
        try:
            self._outbound.put_nowait(payload)
        except asyncio.QueueFull:
            # 丢最旧，不堵发布方——与 `InProcessEventBus` 同语义
            with contextlib.suppress(asyncio.QueueEmpty):
                self._outbound.get_nowait()
            self._dropped += 1
            logger.warning(
                "事件出站队列已满，丢弃最旧的一条（Redis 出得慢）。丢了不要紧："
                "事件只是『去重读』的信号，下一次重读会补上。"
            )
            with contextlib.suppress(asyncio.QueueFull):
                self._outbound.put_nowait(payload)

    # ── 订阅 ────────────────────────────────────────────────────────────────

    async def subscribe(self) -> AsyncIterator[Event]:
        async for event in self._local.subscribe():
            yield event

    # ── 两个后台任务 ────────────────────────────────────────────────────────

    async def _drain_loop(self) -> None:
        while True:
            payload = await self._outbound.get()
            if self._degraded:
                # 降级期间 publish 已经直接投本地了，这里不会有东西；
                # 真收到就丢掉——**不能补发**：补发的是过期事件。
                self._dropped += 1
                continue
            try:
                await self._publish_one(payload)
            except _REDIS_DOWN:
                self._mark_degraded("drainer")
                # **已经进队列的消息走本地**——降级不能从这一刻开始吞事件
                with contextlib.suppress(Exception):
                    self._local.publish(_from_json(payload))
            except asyncio.CancelledError:
                raise

    async def _publish_one(self, payload: str, *, raise_now: BaseException | None = None) -> None:
        """发一条。`raise_now` 只给测试用：证明**非连接类异常不会被降级**。"""

        if raise_now is not None:
            raise raise_now
        if self._redis is None:
            raise _RedisConnectionError("Redis 客户端还没建起来")
        await self._redis.publish(self._channel, payload)

    async def _read_loop(self) -> None:
        """收一条投本地。**连接断了就降级并退出**——实测 `ps.listen()` 在
        连接断开时是**静默结束、不抛异常**的，不主动判就会静默坏掉。"""

        try:
            self._redis = aioredis.from_url(
                self._url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
                max_connections=self._max_connections,
            )
            self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            await self._pubsub.subscribe(self._channel)
            async for message in self._pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                self._local.publish(_from_json(message["data"]))
        except asyncio.CancelledError:
            raise
        except _REDIS_DOWN as error:
            self._mark_degraded(f"reader: {type(error).__name__}")
        else:
            # 迭代器**正常结束** = 连接没了（实测：不抛异常）。
            # 不判这一支的话，Redis 死了没有任何人会知道。
            self._mark_degraded("reader: 连接静默断开")

    def _mark_degraded(self, where: str) -> None:
        self._degraded = True
        if self._warned:
            return
        self._warned = True
        logger.warning(
            "事件扇出已降级到进程内总线（Redis 不可用，发生在 %s）。"
            "**跨 worker 的事件会漏**——界面靠重读自愈，但别当成正常。",
            where,
        )