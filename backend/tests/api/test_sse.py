import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from pmstudio.api import routes
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
                seq=3,
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
            Event(name=PACKET_EMITTED, project_id="p-mine", occurred_at=NOW, seq=1),
            Event(name=PACKET_EMITTED, project_id="p-other", occurred_at=NOW, seq=2),
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
