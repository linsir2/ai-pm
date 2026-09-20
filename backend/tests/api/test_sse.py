"""SSE 事件推送测试。

SSE 是长连接，TestClient 的 stream() 会阻塞事件循环，无法用于测试 SSE。
这里直接测试 SSE 生成器函数的行为。
"""

import asyncio
import contextlib
import json
import tempfile
from pathlib import Path

import pytest

from pmstudio.bootstrap.runtime import build_runtime
from pmstudio.contracts.enums import RoundEntry
from pmstudio.contracts.models.scope import Scope
from pmstudio.harness.fake import FakeHarness
from web_api.sse import event_stream


class _FakeRequest:
    def __init__(self, runtime):
        self.app = type("App", (), {"state": type("State", (), {"runtime": runtime})()})()
        self._disconnected = False

    async def is_disconnected(self):
        return self._disconnected


async def _collect_sse_events(runtime, round_id: str | None = None, max_events: int = 1) -> list[dict]:
    """直接调用 event_stream 生成器，收集 SSE 事件。"""
    req = _FakeRequest(runtime)
    resp = await event_stream(req, round_id=round_id)

    assert resp.media_type == "text/event-stream"

    events = []

    async def _collect():
        async for chunk in resp.body_iterator:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for line in text.split("\n"):
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
                    if len(events) >= max_events:
                        return

    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(_collect(), timeout=5.0)

    return events


@pytest.fixture
async def runtime():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    r = await build_runtime(db_path, harness=FakeHarness())
    yield r
    r.close()


class TestSSE:
    @pytest.mark.asyncio
    async def test_sse_stream_receives_historical_events(self, runtime):
        """SSE 连接建立后能收到之前发生的事件（历史回放）。"""
        proj = await runtime.project_service.create_project("reg_tpl_initial", "test")
        await runtime.round_driver.start_round(
            proj.project_id, RoundEntry.MAIN, "test", Scope()
        )

        events = await _collect_sse_events(runtime, max_events=1)
        assert len(events) >= 1
        assert "type" in events[0]

    @pytest.mark.asyncio
    async def test_sse_filter_by_round_id(self, runtime):
        """按 round_id 过滤，只收到该轮的事件。"""
        proj = await runtime.project_service.create_project("reg_tpl_initial", "test")
        rnd = await runtime.round_driver.start_round(
            proj.project_id, RoundEntry.MAIN, "test", Scope()
        )

        events = await _collect_sse_events(runtime, round_id=rnd, max_events=1)
        assert len(events) >= 1
        assert events[0].get("round_id") == rnd

    @pytest.mark.asyncio
    async def test_sse_event_format(self, runtime):
        """SSE 事件格式正确（data: {json}）。"""
        proj = await runtime.project_service.create_project("reg_tpl_initial", "test")
        await runtime.round_driver.start_round(
            proj.project_id, RoundEntry.MAIN, "test", Scope()
        )

        events = await _collect_sse_events(runtime, max_events=1)
        assert len(events) >= 1
        assert "type" in events[0]
        assert "at" in events[0]
        assert "payload" in events[0]
