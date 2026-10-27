"""真 PostgreSQL + 两个真 uvicorn 进程：round lease 的跨进程互斥与 SIGKILL 接管。

阻塞点用跨进程 socket 闸；子进程的 app URL 带测试 schema 的 search_path，
不回落 SQLite。SIGKILL 用例用 test-only 的短 TTL（`PMSTUDIO_TEST_LEASE_TTL_SECONDS`），
等数据库里的 lease 自然过期后由另一个 worker 接管。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row
from scripts.migrate import apply_migrations

from pmstudio.api.deps import SESSION_COOKIE
from pmstudio.application.errors import LeaseLost
from pmstudio.application.lease import LeaseScope, clear_lease_scope, set_lease_scope
from pmstudio.domain.shared.ids import RoundId, UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    set_owner_scope,
)
from pmstudio.infrastructure.postgres.repositories import SqliteRoundRepository
from pmstudio.infrastructure.security import hash_password
from pmstudio.infrastructure.sqlite.users import SqliteUserRepository
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
)

BACKEND = Path(__file__).resolve().parents[2]
USERNAME = "multiworker-pg-admin"
PASSWORD = "multiworker-pg-admin-pw"

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


@pytest.fixture(scope="module")
def database() -> PgTestDatabase:
    pg = load_environment()
    prepare(pg)
    apply_migrations(pg.direct_url)
    return pg


def _seed_admin(database: PgTestDatabase) -> None:
    with psycopg.connect(database.direct_url, autocommit=True) as connection:
        users = SqliteUserRepository(connection)
        if users.get_by_name(USERNAME) is not None:
            return
        user_id = users.register(
            username=USERNAME, password_hash=hash_password(PASSWORD), role="admin"
        )
        users.set_status(user_id, "active")


@pytest.fixture(autouse=True)
def _clean(database: PgTestDatabase) -> Iterator[None]:
    truncate_data_tables(database)
    _seed_admin(database)
    yield
    truncate_data_tables(database)


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


def _spawn(
    port: int,
    database: PgTestDatabase,
    script: Path,
    gate_port: int,
    log: Path,
    *,
    lease_ttl: float | None = None,
) -> subprocess.Popen:
    env = {
        **database.environment_for_children(),
        "PYTHONPATH": os.pathsep.join((str(BACKEND / "src"), str(BACKEND))),
        "PMSTUDIO_MODEL": "scripted",
        "PMSTUDIO_API_KEY": "test-key",
        "PMSTUDIO_FIRST_ADMIN_USER": USERNAME,
        "PMSTUDIO_FIRST_ADMIN_PASSWORD": PASSWORD,
        "PMSTUDIO_LOGIN_IP_MAX_ATTEMPTS": "10000",
        "PMSTUDIO_LOGIN_USERNAME_MAX_ATTEMPTS": "10000",
        "PMSTUDIO_TEST_SCRIPT": str(script),
        "PMSTUDIO_TEST_GATE": f"127.0.0.1:{gate_port}",
        "PMSTUDIO_TEST_GATE_PURPOSE": "seat_review",
    }
    if lease_ttl is not None:
        env["PMSTUDIO_TEST_LEASE_TTL_SECONDS"] = str(lease_ttl)
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


def _query(database: PgTestDatabase, sql: str, params: tuple[object, ...] = ()):
    with psycopg.connect(
        database.direct_url, autocommit=True, row_factory=dict_row
    ) as connection:
        return connection.execute(sql, params).fetchall()


def _count(database: PgTestDatabase, sql: str, params: tuple[object, ...] = ()) -> int:
    rows = _query(database, sql, params)
    assert rows
    return int(rows[0]["n"])


def _evidence_count(database: PgTestDatabase, project_id: str) -> int:
    row = _query(
        database,
        "SELECT payload FROM evidence_ledgers WHERE project_id = %s",
        (project_id,),
    )
    assert row, "项目没有证据账本"
    return len(row[0]["payload"])


def _round_lease_row(database: PgTestDatabase, round_id: str) -> dict:
    rows = _query(
        database,
        "SELECT owner_id, lease_owner, lease_token, lease_expires_at"
        " FROM rounds WHERE round_id = %s",
        (round_id,),
    )
    assert rows, f"找不到轮次 {round_id}"
    return rows[0]


def _expire_round_lease(database: PgTestDatabase, round_id: str) -> None:
    rows = _query(
        database,
        "UPDATE rounds SET lease_expires_at ="
        " (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - 1000"
        " WHERE round_id = %s RETURNING lease_expires_at",
        (round_id,),
    )
    assert rows, f"没有更新到轮次 {round_id} 的租约"


def _wait_expired(database: PgTestDatabase, round_id: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = _round_lease_row(database, round_id)
        now = _query(
            database,
            "SELECT (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT AS ms",
        )[0]["ms"]
        if row["lease_expires_at"] is None or int(row["lease_expires_at"]) <= int(now):
            return
        time.sleep(0.1)
    raise AssertionError("等待租约自然过期超时")


def _assert_old_token_fenced(database: PgTestDatabase, round_id: str, old: dict) -> None:
    owner = UserId(str(old["owner_id"]))
    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id=round_id,
        user_id=str(owner),
        holder=str(old["lease_owner"]),
        token=int(old["lease_token"]),
        expires_at_ms=int(old["lease_expires_at"]),
    )
    with psycopg.connect(
        database.direct_url, autocommit=True, row_factory=dict_row
    ) as connection:
        set_owner_scope(owner)
        repository = SqliteRoundRepository(connection)
        round_ = repository.get(RoundId(round_id), owner=owner)
        scope = LeaseScope(holder=token.holder, owner=owner)
        scope.bind(token)
        context = set_lease_scope(scope)
        try:
            with pytest.raises(LeaseLost):
                repository.save(round_, owner=owner)
        finally:
            clear_lease_scope(context)
            clear_owner_scope()


def _script(tmp_path: Path) -> Path:
    path = tmp_path / "script.json"
    path.write_text(
        json.dumps(
            {
                "replies": {"claims": CLAIMS_REPLY, "integration": INTEGRATION_REPLY},
                "seat_replies": {SEAT: SEAT_REPLY},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


async def _login(client: httpx.AsyncClient, port: int) -> str:
    response = await client.post(
        f"http://127.0.0.1:{port}/api/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
    )
    assert response.status_code == 200, response.text[:500]
    token = response.cookies.get(SESSION_COOKIE)
    assert token, "登录没有下发会话 cookie"
    return token


async def _project_and_round(client: httpx.AsyncClient, port: int) -> tuple[str, str]:
    created = await client.post(
        f"http://127.0.0.1:{port}/api/projects",
        json={"title": "PG 多 worker 抢同一轮"},
    )
    assert created.status_code == 201, created.text[:500]
    project_id = created.json()["project_id"]
    started = await client.post(
        f"http://127.0.0.1:{port}/api/projects/{project_id}/rounds",
        json={"input": "想做个订单导出的功能"},
    )
    assert started.status_code == 200, started.text[:500]
    return project_id, started.json()["round_id"]


async def test_two_uvicorn_workers_share_one_pg_round_lease(
    tmp_path: Path, database: PgTestDatabase
) -> None:
    script = _script(tmp_path)
    ports = [_free_port(), _free_port()]
    logs = [tmp_path / "worker-a.log", tmp_path / "worker-b.log"]

    async with _socket_gate() as (gate, gate_port):
        procs = [
            _spawn(port, database, script, gate_port, log)
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
                token = await _login(client_a, ports[0])
                client_a.cookies.set(SESSION_COOKIE, token)
                client_b.cookies.set(SESSION_COOKIE, token)
                project_id, round_id = await _project_and_round(client_a, ports[0])

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
                        f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}"
                    )
                ).json()
                assert view["step"] == "awaiting_confirm"
                assert len(view["seat_packets"]) == 1
                assert view["seat_packets"][0]["status"] == "completed"
                assert len(view["draft"]["proposals"]) == 1

                assert _count(database, "SELECT COUNT(*) AS n FROM materials") == 0
                assert _evidence_count(database, project_id) == 2, (
                    "一条用户主张 + 一条整合者判断；第二个请求没进图，不该多出 E"
                )
        finally:
            gate.release.set()
            _stop(procs)


async def test_sigkill_worker_takeover_fences_old_token(
    tmp_path: Path, database: PgTestDatabase
) -> None:
    script = _script(tmp_path)
    ports = [_free_port(), _free_port()]
    logs = [tmp_path / "worker-a.log", tmp_path / "worker-b.log"]

    async with _socket_gate() as (gate, gate_port):
        procs = [
            _spawn(port, database, script, gate_port, log, lease_ttl=2.0)
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
                token = await _login(client_a, ports[0])
                client_a.cookies.set(SESSION_COOKIE, token)
                client_b.cookies.set(SESSION_COOKIE, token)
                project_id, round_id = await _project_and_round(client_a, ports[0])

                opening = asyncio.create_task(
                    client_a.post(
                        f"http://127.0.0.1:{ports[0]}/api/rounds/{round_id}/opening",
                        json={"seats": [SEAT]},
                    )
                )
                await asyncio.wait_for(gate.arrived.wait(), timeout=30.0)
                assert not opening.done(), "闸门到达时 opening 不该已经结束"

                old = _round_lease_row(database, round_id)
                assert old["lease_owner"] is not None, "被杀的 worker 应持有 round lease"

                procs[0].kill()
                await asyncio.to_thread(procs[0].wait, 10)
                assert procs[0].returncode == -signal.SIGKILL, (
                    f"worker 不是被 SIGKILL 收掉的：returncode={procs[0].returncode}"
                )
                with contextlib.suppress(TimeoutError, httpx.HTTPError):
                    response = await asyncio.wait_for(opening, timeout=5.0)
                    raise AssertionError(
                        f"worker 被 SIGKILL 后 opening 仍返回 {response.status_code}"
                    )

                _wait_expired(database, round_id)
                gate.release.set()
                takeover = await client_b.post(
                    f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}/retry"
                )
                assert takeover.status_code == 200, takeover.text[:500]
                assert takeover.json()["waiting_on"] == "draft_card"

                after = _round_lease_row(database, round_id)
                assert int(after["lease_token"]) > int(old["lease_token"]), (
                    "接管应换 token（新 worker 拿到更大的 token）"
                )
                assert after["lease_owner"] != old["lease_owner"], (
                    "接管后 lease_owner 应换人"
                )
                _assert_old_token_fenced(database, round_id, old)

                view = (
                    await client_b.get(
                        f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}"
                    )
                ).json()
                assert view["step"] == "awaiting_confirm"
                assert len(view["seat_packets"]) == 1
                assert view["seat_packets"][0]["status"] == "completed"
                assert len(view["draft"]["proposals"]) == 1

                assert (
                    _count(
                        database,
                        "SELECT COUNT(*) AS n FROM materials WHERE project_id = %s",
                        (project_id,),
                    )
                    == 0
                )
                assert _evidence_count(database, project_id) == 2, (
                    "一条用户主张 + 一条整合者判断；接管不该重复编号"
                )

                abandoned = await client_b.post(
                    f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}/abandon"
                )
                assert abandoned.status_code == 200, abandoned.text[:500]
                assert abandoned.json()["step"] == "abandoned"

                retried = await client_b.post(
                    f"http://127.0.0.1:{ports[1]}/api/rounds/{round_id}/retry"
                )
                assert retried.status_code == 200, retried.text[:500]
                assert retried.json()["step"] == "abandoned", "retry 把 abandoned 复活了"
        finally:
            gate.release.set()
            _stop(procs)
