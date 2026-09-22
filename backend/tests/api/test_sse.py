import asyncio
from datetime import UTC, datetime

from pmstudio.api.sse import event_stream
from pmstudio.application.events import PACKET_EMITTED, Event

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

    assert chunks[0].startswith("event: packet.emitted\ndata: ")
    assert '"seat_id": "seat.data_scale"' in chunks[0]
    assert chunks[0].endswith("\n\n")


async def test_event_stream_sends_heartbeat_when_idle():
    stream = IdleStream()

    async def next_chunk() -> str:
        async for chunk in event_stream(stream, heartbeat_seconds=0.01):
            return chunk
        return ""

    assert await asyncio.wait_for(next_chunk(), timeout=1) == ": ping\n\n"
