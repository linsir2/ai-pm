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
import random
from collections.abc import AsyncIterator, Awaitable, Callable
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

#: 重连探针的退避。**base 2s / 封顶 30s** 依据：
#: AWS 把"只有指数退避、**没有 maximum retry values**"列为反模式
#: （"adding backoff ... and maximum retry values"）——封顶是为了不让一个死掉的
#: Redis 被每 30 秒探一次之外还叠加出一堆待办。
DEFAULT_PROBE_BASE_SECONDS = 2.0
DEFAULT_PROBE_MAX_SECONDS = 30.0


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
        probe_base_seconds: float = DEFAULT_PROBE_BASE_SECONDS,
        probe_max_seconds: float = DEFAULT_PROBE_MAX_SECONDS,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self._url = url
        self._channel = channel
        self._queue_size = queue_size
        self._socket_timeout = socket_timeout
        self._max_connections = max_connections
        self._local = InProcessEventBus()
        self._outbound: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._probe_base = probe_base_seconds
        self._probe_max = probe_max_seconds
        self._sleep = sleeper or asyncio.sleep
        # full jitter = `random(0, 1) × window`；包一层是为了能注入测。
        self._jitter = jitter or (lambda window: random.uniform(0.0, window))
        self._closing = False
        #: 出站用的 **publisher client**，与 reader 的订阅连接分开。
        #: 两个理由：Redis 里"订阅态连接"跑不了普通命令；而且 reader 每次重连
        #: 都换 client，让 drainer 去借它的引用等于把两个生命周期搅在一起
        #: （第一版就是这么写的，结果 reader 一改 drainer 就静默失效）。
        self._publisher: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._drainer: asyncio.Task[None] | None = None
        #: 两条腿各自记账：``reader`` = 收（订阅），``drainer`` = 发（publisher）。
        #: **必须分开**——订阅断了只是收不到，发还是好的；反过来 publisher 断了
        #: 我们发得出去但自己也通过回声收不到（那正好是单 worker 的行为）。
        #: 合成一个标志就会出现"reader 恢复了就把标志清掉、而 publisher 还坏着"
        #: 的假恢复（真跑抓到过）。
        self._legs_down: set[str] = set()
        self._dropped = 0

    # ── 可观测 ──────────────────────────────────────────────────────────────

    @property
    def degraded(self) -> bool:
        """共享扇出当前是否**不完整**。**要能被观测**——悄悄降级和"扇出不存在"
        从外面看是一回事。

        语义是"两条腿里至少一条断了"：只断了订阅 = 我们收不到别的 worker 的事件；
        只断了 publisher = 我们发得出去、收不到自己的回声。两者都不影响
        "本 worker 自己发的事件本 worker 能收到"。
        """

        return bool(self._legs_down)

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
        self._closing = False
        self._reader = asyncio.create_task(self._read_loop(), name="pmstudio-events-reader")
        self._drainer = asyncio.create_task(
            self._drain_loop(), name="pmstudio-events-drainer"
        )

    async def stop(self) -> None:
        """收掉两个任务并关连接。**幂等**：关停路径上可能被重复调到。"""

        # 先置位：reader 循环靠它认出"这是我们自己关的，不是故障"。
        # 不置位的话，`pubsub.aclose()` 让 `listen()` 正常结束，
        # `_read_loop` 会把干净关停当成连接异常、**打一条降级告警**——
        # 而关停本来就会打日志，运维会白查一场。
        self._closing = True
        for task in (self._reader, self._drainer):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._reader = None
        self._drainer = None
        if self._publisher is not None:
            with contextlib.suppress(Exception):
                await self._publisher.aclose()
            self._publisher = None

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
        # **永远入队，不看健康标志**。发不出去时由 drainer 自己兜底并重试——
        # 早先版本在这里 `if self._degraded: 走本地`，那是把"reader 坏"和
        # "发不出去"混成一个标志，于是：drainer 坏了 → 标志为真 → 没人入队 →
        # **drainer 再也没机会自愈**。而这两件事根本不是一回事——
        # 订阅断了只是我们**收不到**跨 worker 事件，**发**一直是好��。
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
            try:
                await self._publish_one(payload)
                self._mark_leg_up("drainer")
            except _REDIS_DOWN:
                self._mark_leg_down("drainer")
                # **兜底走本地**——发不出去的那一条也不能让界面不更新。
                # 不重试这一条：drainer 会在**下一条**消息上自己再试，
                # 成功就把这条腿标回健康。事件是「去重读」的信号，补发是多余的。
                with contextlib.suppress(Exception):
                    self._local.publish(_from_json(payload))
            except asyncio.CancelledError:
                raise

    async def _publish_one(self, payload: str, *, raise_now: BaseException | None = None) -> None:
        """发一条。`raise_now` 只给测试用：证明**非连接类异常不会被降级**。"""

        if raise_now is not None:
            raise raise_now
        if self._publisher is None:
            self._publisher = aioredis.from_url(
                self._url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
                max_connections=self._max_connections,
            )
        try:
            await self._publisher.publish(self._channel, payload)
        except _REDIS_DOWN:
            # 断掉的连接留在池里只会一直失败——丢掉，下次用时重建
            with contextlib.suppress(Exception):
                await self._publisher.aclose()
            self._publisher = None
            raise

    def _next_backoff(self, attempt: int) -> float:
        """**full jitter**：`random(0, 1) × min(cap, base × 2^attempt)`。

        抖动不是装饰。N 个 worker 经历**同一次**故障后会在同一刻探回来——
        Envoy 为此专门有 `max_ejection_time_jitter`，注释写的就是防
        "all proxies try to reconnect to host at the same time"。
        不抖的话 4 个 worker 每 2/4/8/16 秒整齐地各探一次，恢复瞬间是一波冲击。
        AWS 的结论是 full jitter 把重试**均匀铺满整个窗口**，比"留一点退避"的
        equal jitter 更省工作量也更省时间。
        """

        window = min(self._probe_max, self._probe_base * (2**attempt))
        return self._jitter(window)

    async def _read_loop(self) -> None:
        """**订阅 + 收，且断了会自己爬回来**。这条循环本身就是那个探针。

        为什么探针是"重新订阅"而不是 `PING`：重新订阅成功证明的是
        **能收到消息**——这正是扇出需要的能力，而 PING 只证明"能发命令"。
        探一次真订阅比探一个 PING 更有信息量，且不多花什么。

        **每次尝试都换一个新的 client**（实测：复用断了的 client 重订阅，
        `PUBLISH` 返回有接收者但那个订阅收不到任何消息）。
        """

        attempt = 0
        while not self._closing:
            client: Any = None
            pubsub: Any = None
            try:
                client = aioredis.from_url(
                    self._url,
                    socket_timeout=self._socket_timeout,
                    socket_connect_timeout=self._socket_timeout,
                    max_connections=self._max_connections,
                )
                pubsub = client.pubsub(ignore_subscribe_messages=True)
                await pubsub.subscribe(self._channel)
                # **订阅成功 = 探针成功 = 恢复。**退避立刻归零（Envoy 的
                # "健康检查通过就递减倍率"），否则下一次故障会从一个很大的
                # 退避开始——明明 Redis 刚证明过自己是好的。
                self._mark_leg_up("reader")
                attempt = 0
                async for message in pubsub.listen():
                    if message is None or message.get("type") != "message":
                        continue
                    self._local.publish(_from_json(message["data"]))
            except asyncio.CancelledError:
                raise
            except _REDIS_DOWN as error:
                if not self._closing:
                    self._mark_leg_down("reader", where=f"{type(error).__name__}")
            else:
                # 迭代器**正常结束**：连接静默断了（干净关闭会走到这里）。
                # 关停过程中的这一支**不记降级**——那不是故障。
                if not self._closing:
                    self._mark_leg_down("reader", where="连接静默断开")
            finally:
                if pubsub is not None:
                    with contextlib.suppress(Exception):
                        await pubsub.aclose()
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.aclose()
            if self._closing:
                return
            attempt += 1
            await self._sleep(self._next_backoff(attempt - 1))

    def _mark_leg_down(self, leg: str, *, where: str = "未标注") -> None:
        if leg in self._legs_down:
            return
        self._legs_down.add(leg)
        logger.warning(
            "事件扇出降级：%s 那条腿断了（%s）。"
            "两条腿的坏法不同——订阅断了只收不到别的 worker 的事件，"
            "publisher 断了发不出去。界面靠重读自愈。",
            leg,
            where,
        )

    def _mark_leg_up(self, leg: str) -> None:
        was_degraded = bool(self._legs_down)
        self._legs_down.discard(leg)
        if was_degraded and not self._legs_down:
            # **恢复也必须可观测**：只打降级不打恢复，事故就永远关不掉——
            # 运维看到的是"最后一条降级告警"而不是"它恢复了"。
            logger.warning(
                "事件扇出已恢复到 Redis 共享模式（两条腿都接上了）。"
                "降级期间漏掉的事件不会补发——它们只是『去重读』的信号。"
            )
