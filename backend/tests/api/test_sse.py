import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from pmstudio.api import routes, sse
from pmstudio.api.sse import event_stream
from pmstudio.application.events import PACKET_EMITTED
from pmstudio.application.use_cases import UserView
from pmstudio.domain.shared.ids import UserId
from pmstudio.harness.events import Event
from pmstudio.infrastructure.sqlite import owner_scope

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


class StubStream:
    def __init__(self, events: list[Event]) -> None:
        self._events = events

    async def _iterate(self):  # type: ignore[no-untyped-def]
        for event in self._events:
            yield event

    def subscribe(self):  # type: ignore[no-untyped-def]
        return self._iterate()


class IdleStream:
    """永远不给事件的订阅（连接还开着）——心跳只在这种情况出现。"""

    async def _iterate(self):  # type: ignore[no-untyped-def]
        await asyncio.sleep(3600)
        if False:  # pragma: no cover —— 让它是异步生成器
            yield None

    def subscribe(self):  # type: ignore[no-untyped-def]
        return self._iterate()


async def test_event_stream_formats_named_sse_frames():
    stream = StubStream(
        [
            Event(
                name=PACKET_EMITTED,
                project_id="p1",
                round_id="r1",
                payload={"seat_id": "seat.data_scale"},
                occurred_at=NOW,
            )
        ]
    )

    chunks = [chunk async for chunk in event_stream(stream, heartbeat_seconds=5)]

    # 第 0 个 chunk 现在固定是建流注释（下面那条用例钉它），业务事件从第 1 个开始
    assert chunks[0] == ": ready\n\n"
    assert chunks[1].startswith("event: packet.emitted\ndata: ")
    assert '"seat_id": "seat.data_scale"' in chunks[1]
    assert chunks[1].endswith("\n\n")


async def test_event_stream_opens_with_a_ready_comment():
    """建流立刻给一行注释：顶栏不必等 15 秒心跳才从"推送连接中"变成"已连接"。"""

    async def first_chunk() -> str:
        async for chunk in event_stream(IdleStream(), heartbeat_seconds=3600):
            return chunk
        return ""

    assert await asyncio.wait_for(first_chunk(), timeout=1) == ": ready\n\n"


async def test_event_stream_sends_heartbeat_when_idle():
    stream = IdleStream()

    async def chunks_before_heartbeat() -> list[str]:
        chunks: list[str] = []
        async for chunk in event_stream(stream, heartbeat_seconds=0.01):
            chunks.append(chunk)
            if chunk == ": ping\n\n":
                return chunks
        return chunks

    # 第 0 个是建流注释，心跳从第 1 个开始（建流注释不替代心跳）
    assert await asyncio.wait_for(chunks_before_heartbeat(), timeout=1) == [
        ": ready\n\n",
        ": ping\n\n",
    ]


class _OwnedProjects:
    """`services.list_projects` 的最小替身：只认一个 owner 的项目 id。"""

    def __init__(self, project_ids: set[str]) -> None:
        self._project_ids = project_ids

    async def __call__(self):  # type: ignore[no-untyped-def]
        return tuple(
            SimpleNamespace(project_id=project_id) for project_id in self._project_ids
        )


async def test_events_route_wires_owner_visibility_into_the_stream():
    """`routes.events` 必须把 `visible` 传给裁剪逻辑——删掉 `visible=visible` 这条会红。

    直接调处理函数（不经 HTTP）：拿回 `StreamingResponse`，喂两条事件（一条自己的、
    一条别人的），断言**别人的那条没被投出去**。这钉的是"接线"，不是"判据"——
    判据本身由 `test_isolation_matrix.py::test_sse_only_delivers_my_events` 覆盖。
    """

    bus = StubStream(
        [
            Event(name=PACKET_EMITTED, project_id="p-mine", occurred_at=NOW),
            Event(name=PACKET_EMITTED, project_id="p-other", occurred_at=NOW),
        ]
    )
    services = SimpleNamespace(stream=bus, list_projects=_OwnedProjects({"p-mine"}))
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(services=services, owner_scope=owner_scope)
        )
    )
    user = UserView(user_id=UserId("u-me"), username="me", role="admin", status="active")

    response = await routes.events(request, user)
    frames = [chunk async for chunk in response.body_iterator]

    business = [frame for frame in frames if frame.startswith("event:")]
    assert len(business) == 1, f"别人的事件漏进来了：{business}"
    assert '"p-mine"' in business[0]


# ── owner_id 快路与订阅者清理（P4）─────────────────────────────────────────


async def test_事件带_owner_id_时不查库():
    """**P4 的关键快路**：多 worker 下每条 worker 都收到**全部**事件，
    落到本 worker 的连接上要一条条判归属——查库判据会让每个陌生 project_id
    都触发一次全表聚合查询。

    R：事件自带 owner_id 时一次库都不查；P：发一条带 owner_id 的事件；
    R：判定结果正确且 `list_owned` 调用次数为 0。"""

    calls: list[int] = []

    async def list_owned():  # noqa: ANN202
        calls.append(1)
        return await _OwnedProjects({"p-mine"})()

    visible = sse.owner_visible(scope=owner_scope, list_owned=list_owned, owner=UserId("u-me"))

    mine = Event(
        name=PACKET_EMITTED, project_id="p-mine", occurred_at=NOW, owner_id="u-me"
    )
    theirs = Event(
        name=PACKET_EMITTED, project_id="p-mine", occurred_at=NOW, owner_id="u-other"
    )

    assert await visible(mine) is True
    assert await visible(theirs) is False
    assert calls == [], f"带了 owner_id 还查了库 {len(calls)} 次"


async def test_owner_id_缺失时回落到查库():
    """**`owner_id` 缺失 ≠ 可见**。它是"没盖到章"的兜底路径，不是快路。"""

    calls: list[int] = []

    async def list_owned():  # noqa: ANN202
        calls.append(1)
        return await _OwnedProjects({"p-mine"})()

    visible = sse.owner_visible(scope=owner_scope, list_owned=list_owned, owner=UserId("u-me"))

    assert await visible(Event(name=PACKET_EMITTED, project_id="p-mine", occurred_at=NOW)) is True
    assert await visible(Event(name=PACKET_EMITTED, project_id="p-x", occurred_at=NOW)) is False
    assert len(calls) == 2


async def test_没有_owner_id_时缓存仍然生效():
    """既有语义不许退化：同一 project 只查一次。"""

    calls: list[int] = []

    async def list_owned():  # noqa: ANN202
        calls.append(1)
        return await _OwnedProjects({"p-mine"})()

    visible = sse.owner_visible(scope=owner_scope, list_owned=list_owned, owner=UserId("u-me"))

    for _ in range(3):
        await visible(Event(name=PACKET_EMITTED, project_id="p-mine", occurred_at=NOW))

    assert len(calls) == 1


async def test_订阅者断开后被摘除():
    """`event_stream` 的 `finally` 是 P4 顺手修的现存泄漏：没有它的话，
    客户端断连时内层 `subscribe()` 的清理要等异步生成器被 GC 才跑——
    订阅者摘除是**非确定性**的，多 worker 下速率 ×N。"""

    closed: list[int] = []

    class _ClosableStream:
        """记录自己被 aclose 了没有——这正是泄漏的那一步。"""

        async def subscribe(self):  # noqa: ANN202
            try:
                yield Event(name=PACKET_EMITTED, project_id="p", occurred_at=NOW)
                await asyncio.sleep(3600)
            finally:
                closed.append(1)

    stream = sse.event_stream(_ClosableStream(), heartbeat_seconds=0.01)
    assert await anext(stream) == ": ready\n\n"  # 建流那行

    # **必须先让它真的启动一次**：异步生成器不 `anext` 就没跑函数体，
    # 那时 `aclose()` 是空操作（没启动就没有要清理的东西，也确实没泄漏）。
    # 泄漏发生在"已经注册了订阅者之后再断开"这条路径上。
    frame = await anext(stream)
    assert frame.startswith("event:"), frame

    await stream.aclose()

    assert closed == [1], "断开后没有确定性地关掉内层订阅"


async def test_建流后立刻断开_不会报错也不会漏订阅():
    """姊妹路径：`: ready` 之后立刻断，**内层生成器还没启动**。

    这时 `aclose()` 合法地是空操作——异步生成器没跑过函数体就没有要清理的东西，
    也就确实没有泄漏。**重点是它不许炸**：内层没启动时 `aclose()` 仍要能被安全调到。
    """

    closed: list[int] = []

    class _NeverStarted:
        async def subscribe(self):  # noqa: ANN202
            try:
                await asyncio.sleep(3600)
                yield Event(name=PACKET_EMITTED, project_id="p", occurred_at=NOW)
            finally:
                closed.append(1)

    stream = sse.event_stream(_NeverStarted(), heartbeat_seconds=0.01)
    assert await anext(stream) == ": ready\n\n"
    await stream.aclose()   # 不许抛

    assert closed == [], "内层从未启动，不该有清理动作——有的话说明多注册了订阅者"
