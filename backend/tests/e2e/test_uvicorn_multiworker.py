"""P3b Task 7a：两个**真 uvicorn 进程**共享同一个 SQLite，抢同一轮的 act lease。

这条用例补的是进程边界本身：两个进程各有一个 `RoundRunner`（内存快路径互相看不见），
只有 rounds 行上的 DB lease 能挡住第二个请求。模型与联网/抓页替身由
`tests/support/uvicorn_app.py` 在子进程里装配，生产 `build_app` 不参与。

阻塞点用**跨进程 socket 闸**：子进程到达席位模型调用时连上父进程的监听 socket，
父进程收到"已到达"后才发第二个请求；释放靠关闭连接。不用 sleep 猜时序。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from pmstudio.api.deps import SESSION_COOKIE

BACKEND = Path(__file__).resolve().parents[2]
USERNAME = "multiworker-admin"
PASSWORD = "multiworker-admin-pw"

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "order-export",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}],
    },
    ensure_ascii=False,
)

SEAT = "seat.data_scale"

SEAT_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "异常与边界",
                "claim": "无导出权限的用户不显示导出入口",
                "because": "订单含客户信息",
                "evidence_refs": [],
            }
        ],
    },
    ensure_ascii=False,
)

INTEGRATION_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "exceptions",
                },
                "text": "无导出权限的用户不显示导出入口",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "订单含客户信息",
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _GateControl:
    def __init__(self) -> None:
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()


@contextlib.asynccontextmanager
async def _socket_gate() -> AsyncIterator[tuple[_GateControl, int]]:
    """父进程侧闸门：子进程连上来 = 已到阻塞点；关连接 = 放行。

    `release` 只是**父进程内**唤醒 handler 的开关，跨进程的同步靠 socket 连接本身。
    """

    control = _GateControl()
    connections: set[asyncio.Task[None]] = set()

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        connections.add(task)
        try:
            await reader.readline()
            control.arrived.set()
            await control.release.wait()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            connections.discard(task)

    server = await asyncio.start_server(_handle, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    try:
        yield control, port
    finally:
        control.release.set()
        server.close()
        await server.wait_closed()
        if connections:
            await asyncio.gather(*connections, return_exceptions=True)


def _spawn(port: int, db: Path, script: Path, gate_port: int, log: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(BACKEND / "src"), str(BACKEND))),
        "PMSTUDIO_MODEL": "scripted",
        "PMSTUDIO_API_KEY": "test-key",
        "PMSTUDIO_DB": str(db),
        "PMSTUDIO_FIRST_ADMIN_USER": USERNAME,
        "PMSTUDIO_FIRST_ADMIN_PASSWORD": PASSWORD,
        "PMSTUDIO_LOGIN_IP_MAX_ATTEMPTS": "10000",
        "PMSTUDIO_LOGIN_USERNAME_MAX_ATTEMPTS": "10000",
        "PMSTUDIO_TEST_SCRIPT": str(script),
        "PMSTUDIO_TEST_GATE": f"127.0.0.1:{gate_port}",
        "PMSTUDIO_TEST_GATE_PURPOSE": "seat_review",
    }
    handle = log.open("wb")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "tests.support.uvicorn_app:build_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=str(BACKEND),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    finally:
        handle.close()


async def _wait_healthy(
    client: httpx.AsyncClient, port: int, proc: subprocess.Popen, log: Path
) -> None:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(
                f"worker 提前退出（code={proc.returncode}）：\n{log.read_text()[-3000:]}"
            )
        try:
            response = await client.get(f"http://127.0.0.1:{port}/api/health")
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"worker 在 30s 内没有健康：\n{log.read_text()[-3000:]}")


def _stop(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


def _count(db: Path, sql: str, params: tuple[object, ...] = ()) -> int:
    connection = sqlite3.connect(db, timeout=10.0)
    try:
        return int(connection.execute(sql, params).fetchone()[0])
    finally:
        connection.close()


def _evidence_count(db: Path, project_id: str) -> int:
    connection = sqlite3.connect(db, timeout=10.0)
    try:
        row = connection.execute(
            "SELECT payload FROM evidence_ledgers WHERE project_id = ?", (project_id,)
        ).fetchone()
        assert row is not None, "项目没有证据账本"
        return len(json.loads(row[0]))
    finally:
        connection.close()


async def test_two_uvicorn_workers_share_one_round_lease(tmp_path: Path) -> None:
    script = tmp_path / "script.json"
    script.write_text(
        json.dumps(
            {
                "replies": {"claims": CLAIMS_REPLY, "integration": INTEGRATION_REPLY},
                "seat_replies": {SEAT: SEAT_REPLY},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    db = tmp_path / "pmstudio.db"
    ports = [_free_port(), _free_port()]
    logs = [tmp_path / "worker-a.log", tmp_path / "worker-b.log"]

    async with _socket_gate() as (gate, gate_port):
        procs = [
            _spawn(port, db, script, gate_port, log)
            for port, log in zip(ports, logs, strict=True)
        ]
        try:
            async with (
                httpx.AsyncClient(timeout=30.0) as client_a,
                httpx.AsyncClient(timeout=30.0) as client_b,
            ):
                await asyncio.gather(
                    _wait_healthy(client_a, ports[0], procs[0], logs[0]),
                    _wait_healthy(client_b, ports[1], procs[1], logs[1]),
                )

                login = await client_a.post(
                    f"http://127.0.0.1:{ports[0]}/api/auth/login",
                    json={"username": USERNAME, "password": PASSWORD},
                )
                assert login.status_code == 200, login.text[:500]
                token = login.cookies.get(SESSION_COOKIE)
                assert token, "登录没有下发会话 cookie"
                client_a.cookies.set(SESSION_COOKIE, token)
                client_b.cookies.set(SESSION_COOKIE, token)

                created = await client_a.post(
                    f"http://127.0.0.1:{ports[0]}/api/projects",
                    json={"title": "多 worker 抢同一轮"},
                )
                assert created.status_code == 201, created.text[:500]
                project_id = created.json()["project_id"]

                started = await client_a.post(
                    f"http://127.0.0.1:{ports[0]}/api/projects/{project_id}/rounds",
                    json={"input": "想做个订单导出的功能"},
                )
                assert started.status_code == 200, started.text[:500]
                round_id = started.json()["round_id"]

                opening = asyncio.create_task(
                    client_a.post(
                        f"http://127.0.0.1:{ports[0]}/api/rounds/{round_id}/opening",
                        json={"seats": [SEAT]},
                    )
                )
                await asyncio.wait_for(gate.arrived.wait(), timeout=30.0)
                assert not opening.done(), "闸门到达时第一个请求不该已经结束"

                busy = await client_b.post(
                    f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}/opening",
                    json={"seats": [SEAT]},
                )
                assert busy.status_code == 409, busy.text[:500]
                assert busy.json()["code"] == "round_busy"

                gate.release.set()
                opened = await asyncio.wait_for(opening, timeout=30.0)
                assert opened.status_code == 200, opened.text[:500]
                assert opened.json()["waiting_on"] == "draft_card"

                view = (
                    await client_b.get(
                        f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}",
                    )
                ).json()
                assert view["step"] == "awaiting_confirm"
                assert len(view["seat_packets"]) == 1
                assert view["seat_packets"][0]["status"] == "completed"
                assert len(view["draft"]["proposals"]) == 1

                assert _count(db, "SELECT COUNT(*) FROM materials") == 0
                assert _evidence_count(db, project_id) == 2, (
                    "一条用户主张 + 一条整合者判断；第二个请求没进图，不该多出 E"
                )
        finally:
            gate.release.set()
            _stop(procs)
