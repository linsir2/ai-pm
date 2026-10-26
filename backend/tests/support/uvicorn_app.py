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

from pmstudio.application.ports import WebFetcher, WebSearchGateway
from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.harness.model.contracts import ModelGateway, ModelReply, ModelRequest
from tests.support.doubles import RecordingWebFetch, RecordingWebSearch, ScriptedModelGateway

SCRIPT_ENV = "PMSTUDIO_TEST_SCRIPT"
GATE_ENV = "PMSTUDIO_TEST_GATE"
GATE_PURPOSE_ENV = "PMSTUDIO_TEST_GATE_PURPOSE"


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


class GatedScriptedGateway(ScriptedModelGateway):
    """第一次到达 `gate_purpose` 时停在 socket 闸上，其余调用照常走脚本。"""

    def __init__(
        self,
        *,
        gate: SocketGate,
        gate_purpose: str,
        replies: dict[str, str | list[str]],
        seat_replies: dict[str, str | list[str]],
    ) -> None:
        super().__init__(replies=replies, seat_replies=seat_replies)
        self._gate = gate
        self._gate_purpose = gate_purpose
        self._gate_passed = False

    async def complete(self, request: ModelRequest) -> ModelReply:
        if not self._gate_passed and request.purpose == self._gate_purpose:
            self._gate_passed = True
            await self._gate.wait()
        return await super().complete(request)


def create_app(
    *,
    settings: Settings,
    gateway: ModelGateway,
    web_search: WebSearchGateway,
    fetcher: WebFetcher,
) -> FastAPI:
    """显式接受三个替身；测试进程内外都走这一条装配路。"""

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
    if address:
        purpose = os.environ.get(GATE_PURPOSE_ENV, "")
        if not purpose:
            raise RuntimeError(f"给了 {GATE_ENV} 就必须给 {GATE_PURPOSE_ENV}")
        gateway: ModelGateway = GatedScriptedGateway(
            gate=SocketGate(address),
            gate_purpose=purpose,
            replies=replies,
            seat_replies=seat_replies,
        )
    else:
        gateway = ScriptedModelGateway(replies=replies, seat_replies=seat_replies)

    return create_app(
        settings=settings,
        gateway=gateway,
        web_search=RecordingWebSearch(),
        fetcher=RecordingWebFetch(),
    )
