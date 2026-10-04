import asyncio
from datetime import UTC, datetime

from pmstudio.api.sse import event_stream
from pmstudio.application.events import PACKET_EMITTED
from pmstudio.harness.events import Event

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
