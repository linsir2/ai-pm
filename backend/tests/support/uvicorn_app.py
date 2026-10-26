"""测试专用 uvicorn app factory：真进程边界 + 可注入的测试替身。

只被 `tests/e2e/test_uvicorn_multiworker.py` 这类用例通过
`uvicorn tests.support.uvicorn_app:build_app --factory` 拉起。生产 `build_app`
不认识本模块，也没有"用假实现"的开关。

跨进程阻塞用 **socket 闸**：子进程到达指定模型 purpose 时连上父进程的监听地址并
等它断开；父进程收到连接 = "已到达阻塞点"，关连接 = "放行"。进程内 `asyncio.Event`
跨不了进程，sleep 又会把测试变成碰运气，两者都不用。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from fastapi import FastAPI

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.application.ports import WebFetcher, WebSearchGateway
from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.harness.model.contracts import ModelGateway, ModelReply, ModelRequest
from pmstudio.infrastructure.tools.tavily import PARSER_VERSION
from tests.support.doubles import RecordingWebFetch, RecordingWebSearch, ScriptedModelGateway

SCRIPT_ENV = "PMSTUDIO_TEST_SCRIPT"
GATE_ENV = "PMSTUDIO_TEST_GATE"
GATE_PURPOSE_ENV = "PMSTUDIO_TEST_GATE_PURPOSE"
CALL_LOG_ENV = "PMSTUDIO_TEST_CALL_LOG"
LEASE_TTL_ENV = "PMSTUDIO_TEST_LEASE_TTL_SECONDS"
LEASE_HEARTBEAT_ENV = "PMSTUDIO_TEST_LEASE_HEARTBEAT_SECONDS"


class SocketGate:
    """跨进程放行闸：连上父进程，发一行"到达"，然后等父进程关闭连接。"""

    def __init__(self, address: str) -> None:
        host, _, port = address.rpartition(":")
        if not host or not port:
            raise ValueError(f"PMSTUDIO_TEST_GATE 要是 host:port，现在是 {address!r}")
        self._host = host
        self._port = int(port)

    async def wait(self) -> None:
        reader, writer = await asyncio.open_connection(self._host, self._port)
        try:
            writer.write(b"arrived\n")
            await writer.drain()
            await reader.read()  # EOF = 父进程放行
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


def _append_call(record: dict[str, object]) -> None:
    """把一次外部调用追加到父进程给的日志（跨进程 O_APPEND，单行原子）。"""

    path = os.environ.get(CALL_LOG_ENV)
    if not path:
        return
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


class GatedScriptedGateway(ScriptedModelGateway):
    """第一次到达 `gate_purpose` 时停在 socket 闸上，其余调用照常走脚本。"""

    def __init__(
        self,
        *,
        gate: SocketGate | None,
        gate_purpose: str,
        replies: dict[str, str | list[str]],
        seat_replies: dict[str, str | list[str]],
    ) -> None:
        super().__init__(replies=replies, seat_replies=seat_replies)
        self._gate = gate
        self._gate_purpose = gate_purpose
        self._gate_passed = False

    async def complete(self, request: ModelRequest) -> ModelReply:
        _append_call(
            {
                "kind": "model",
                "purpose": request.purpose,
                "metadata": dict(request.metadata),
                "pid": os.getpid(),
            }
        )
        if (
            self._gate is not None
            and not self._gate_passed
            and request.purpose == self._gate_purpose
        ):
            self._gate_passed = True
            await self._gate.wait()
        return await super().complete(request)


class GatedWebSearch:
    """记录/放行联网检索：`gate_purpose=web_search` 时第一次调用停在 socket 闸上。"""

    def __init__(
        self,
        *,
        inner: RecordingWebSearch,
        gate: SocketGate | None,
        gate_purpose: str,
    ) -> None:
        self._inner = inner
        self._gate = gate
        self._gate_purpose = gate_purpose
        self._gate_passed = False

    async def search(self, *, query: str, limit: int) -> tuple[ParsedMaterial, ...]:
        _append_call(
            {"kind": "web_search", "query": query, "limit": limit, "pid": os.getpid()}
        )
        if (
            self._gate is not None
            and not self._gate_passed
            and self._gate_purpose == "web_search"
        ):
            self._gate_passed = True
            await self._gate.wait()
        return await self._inner.search(query=query, limit=limit)


def _web_packet(raw: dict[str, object]) -> ParsedMaterial:
    url = str(raw.get("url") or "")
    if not url:
        raise RuntimeError("web_results 的每一项都要有 url")
    text = str(raw.get("text") or "")
    return ParsedMaterial(
        origin="web_search",
        title=str(raw.get("title") or url),
        source_ref=url,
        media_type="text/html",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="tavily#c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="网页正文",
            ),
        ),
        parser_id="tavily",
        parser_version=PARSER_VERSION,
    )


def _web_search_from_script(script: dict[str, object]) -> RecordingWebSearch:
    raw = script.get("web_results")
    if not isinstance(raw, dict):
        return RecordingWebSearch()
    returns_by_query = {
        str(query): tuple(
            _web_packet(item) for item in items if isinstance(item, dict)
        )
        for query, items in raw.items()
        if isinstance(items, list)
    }
    return RecordingWebSearch(returns_by_query=returns_by_query)


def _apply_lease_ttl(container: object) -> None:
    """测试专用：把 runner 的租约 TTL/心跳调短（生产 build_app 不读这些变量）。"""

    raw_ttl = os.environ.get(LEASE_TTL_ENV)
    raw_heartbeat = os.environ.get(LEASE_HEARTBEAT_ENV)
    if not raw_ttl and not raw_heartbeat:
        return
    ttl = float(raw_ttl) if raw_ttl else None
    heartbeat = float(raw_heartbeat) if raw_heartbeat else None
    if (ttl is not None and ttl <= 0) or (heartbeat is not None and heartbeat <= 0):
        raise RuntimeError(f"{LEASE_TTL_ENV} / {LEASE_HEARTBEAT_ENV} 必须大于 0")
    services = container.app.state.services  # type: ignore[attr-defined]
    for name in ("runner", "exploration_runner"):
        target = getattr(services, name)
        if ttl is not None:
            target._lease_ttl_seconds = ttl
        if heartbeat is not None:
            target._lease_heartbeat_seconds = heartbeat


def create_app(
    *,
    settings: Settings,
    gateway: ModelGateway,
    web_search: WebSearchGateway,
    fetcher: WebFetcher,
) -> FastAPI:
    """显式接受三个替身的装配辅助；uvicorn 入口是下面的 `build_app`。"""

    container = build_container(
        settings,
        gateway=gateway,
        web_search=web_search,
        fetcher=fetcher,
    )
    return container.app


def build_app() -> FastAPI:
    """`uvicorn ...:build_app --factory` 的测试入口。"""

    settings = Settings.from_env()
    script_path = os.environ.get(SCRIPT_ENV)
    if not script_path:
        raise RuntimeError(f"测试 factory 需要 {SCRIPT_ENV}")
    script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    replies = script.get("replies", {})
    seat_replies = script.get("seat_replies", {})

    address = os.environ.get(GATE_ENV)
    gate: SocketGate | None = None
    purpose = ""
    if address:
        purpose = os.environ.get(GATE_PURPOSE_ENV, "")
        if not purpose:
            raise RuntimeError(f"给了 {GATE_ENV} 就必须给 {GATE_PURPOSE_ENV}")
        gate = SocketGate(address)

    gateway: ModelGateway = GatedScriptedGateway(
        gate=gate,
        gate_purpose=purpose,
        replies=replies,
        seat_replies=seat_replies,
    )
    container = build_container(
        settings,
        gateway=gateway,
        web_search=GatedWebSearch(
            inner=_web_search_from_script(script),
            gate=gate,
            gate_purpose=purpose,
        ),
        fetcher=RecordingWebFetch(),
    )
    _apply_lease_ttl(container)
    return container.app
