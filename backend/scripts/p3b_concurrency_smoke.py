#!/usr/bin/env python3
"""P3b Task 7b：两个真 uvicorn 进程共享 SQLite 的并发 / SIGKILL 冒烟。

验的是**进程边界**上的四件事：

1. 同轮 opening：A 在 `seat_review` 被 socket 闸挡住时，B 拿 409；放闸后只有 A 跑完；
2. 同轮 decision：A 在 `web_search` 上被挡住时，B 拿 409；放闸后 A 成功，
   B 对另一条 pending 申请重试成功；两条检索各执行一次；
3. 同项目 exploration：A 在 `inquiry` 上被挡住时，B 拿 409；放闸后只落一趟；
4. SIGKILL：杀掉正在跑 opening 的 worker，短 TTL（测试专用）过期后 B 接管；
   旧 token 的 save 在真仓储上被 fence；abandon 之后 retry 不复活。

阻塞靠 `tests/support/uvicorn_app.py` 的跨进程 socket 闸；等待 TTL 靠**轮询库里的
`lease_expires_at`**，不用 sleep 猜时序。模型/联网调用次数从测试 factory 的
`PMSTUDIO_TEST_CALL_LOG`（每个进程 O_APPEND 一行）读，不靠日志文案。

跑法（从 `backend/`）：

    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/p3b_concurrency_smoke.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src"
sys.path.insert(0, str(SRC))

from pmstudio.api.deps import SESSION_COOKIE  # noqa: E402
from pmstudio.application.errors import LeaseLost  # noqa: E402
from pmstudio.application.lease import (  # noqa: E402
    LeaseScope,
    clear_lease_scope,
    set_lease_scope,
    wall_clock_ms,
)
from pmstudio.domain.shared.ids import RoundId, UserId  # noqa: E402
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken  # noqa: E402
from pmstudio.infrastructure.sqlite.repositories import (  # noqa: E402
    SqliteRoundRepository,
)

USERNAME = "p3b-smoke-admin"
PASSWORD = "p3b-smoke-admin-pw"
SEAT = "seat.data_scale"
QUERY_A = "RBAC 权限模型的主流做法"
QUERY_B = "多租户隔离怎么做"
INSECURE_LOGIN_ENV = {
    "PMSTUDIO_LOGIN_IP_MAX_ATTEMPTS": "10000",
    "PMSTUDIO_LOGIN_USER_MAX_ATTEMPTS": "10000",
}

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
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量"}],
        "web_queries": [
            {"query": QUERY_A, "why": "要查行业做法"},
            {"query": QUERY_B, "why": "要查隔离方案"},
        ],
    },
    ensure_ascii=False,
)

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

INQUIRY_REPLY = json.dumps(
    {
        "basis": "读了文档",
        "hypotheses": [
            {
                "local_id": "H1",
                "statement": "运营按客户名找单",
                "grounding": "invented",
                "based_on": [],
                "falsification": "访谈没人这么找",
                "evidence_needed": "两个访谈",
            }
        ],
        "blind_spots": [
            {"local_id": "B1", "statement": "归属未知", "next_step": "问数据组"}
        ],
    },
    ensure_ascii=False,
)

WEB_RESULTS = {
    QUERY_A: [
        {
            "title": "RBAC 综述",
            "url": "https://example.test/rbac",
            "text": "行业通行做法是 RBAC：角色绑权限，用户绑角色。",
        }
    ],
    QUERY_B: [
        {
            "title": "多租户隔离",
            "url": "https://example.test/tenancy",
            "text": "多租户常见做法是共享库 + 行级隔离。",
        }
    ],
}


class SmokeError(RuntimeError):
    """一条冒烟判据没通过。"""


def _check(condition: object, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _url(port: int, path: str) -> str:
    return f"http://127.0.0.1:{port}{path}"


def _spawn(
    *,
    port: int,
    db: Path,
    script: Path,
    gate_port: int,
    purpose: str,
    call_log: Path,
    log: Path,
    lease_ttl: float | None = None,
) -> subprocess.Popen[bytes]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(SRC), str(BACKEND))),
        "PMSTUDIO_MODEL": "scripted",
        "PMSTUDIO_API_KEY": "test-key",
        "PMSTUDIO_TAVILY_API_KEY": "test-key",
        "PMSTUDIO_DB": str(db),
        "PMSTUDIO_FIRST_ADMIN_USER": USERNAME,
        "PMSTUDIO_FIRST_ADMIN_PASSWORD": PASSWORD,
        "PMSTUDIO_SESSION_TTL_DAYS": "1",
        **INSECURE_LOGIN_ENV,
        "PMSTUDIO_TEST_SCRIPT": str(script),
        "PMSTUDIO_TEST_GATE": f"127.0.0.1:{gate_port}",
        "PMSTUDIO_TEST_GATE_PURPOSE": purpose,
        "PMSTUDIO_TEST_CALL_LOG": str(call_log),
    }
    if lease_ttl is not None:
        env["PMSTUDIO_TEST_LEASE_TTL_SECONDS"] = str(lease_ttl)
        env["PMSTUDIO_TEST_LEASE_HEARTBEAT_SECONDS"] = str(lease_ttl / 5)
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
    client: httpx.AsyncClient, port: int, proc: subprocess.Popen[bytes], log: Path
) -> None:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SmokeError(
                f"worker 提前退出（code={proc.returncode}）：\n{log.read_text()[-4000:]}"
            )
        try:
            response = await client.get(_url(port, "/api/health"))
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.05)
    raise SmokeError(f"worker 在 30s 内没有健康：\n{log.read_text()[-4000:]}")


def _stop(procs: list[subprocess.Popen[bytes]]) -> None:
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
    for proc in procs:
        if proc.poll() is not None:
            continue
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


class _GateControl:
    def __init__(self) -> None:
        self.arrived = asyncio.Event()
        self.release = asyncio.Event()


@contextlib.asynccontextmanager
async def _socket_gate() -> AsyncIterator[tuple[_GateControl, int]]:
    """父进程侧闸门：子进程连上来 = 已到阻塞点；设置 release = 放行。"""

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


@dataclass
class Workers:
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient]
    procs: tuple[subprocess.Popen[bytes], subprocess.Popen[bytes]]
    ports: tuple[int, int]
    logs: tuple[Path, Path]
    cookie: str

    def client(self, index: int) -> httpx.AsyncClient:
        return self.clients[index]

    def port(self, index: int) -> int:
        return self.ports[index]

    def proc(self, index: int) -> subprocess.Popen[bytes]:
        return self.procs[index]

    def tail(self) -> str:
        parts: list[str] = []
        for index, log in enumerate(self.logs):
            parts.append(f"--- worker {index} ---\n{log.read_text()[-4000:]}")
        return "\n".join(parts)


@contextlib.asynccontextmanager
async def _workers(
    *,
    tmp: Path,
    db: Path,
    script: Path,
    gate_port: int,
    purpose: str,
    call_log: Path,
    stage: str,
    lease_ttl: float | None = None,
) -> AsyncIterator[Workers]:
    ports = (_free_port(), _free_port())
    logs = (tmp / f"{stage}-worker-a.log", tmp / f"{stage}-worker-b.log")
    procs = tuple(
        _spawn(
            port=port,
            db=db,
            script=script,
            gate_port=gate_port,
            purpose=purpose,
            call_log=call_log,
            log=log,
            lease_ttl=lease_ttl,
        )
        for port, log in zip(ports, logs, strict=True)
    )
    clients = (
        httpx.AsyncClient(timeout=60.0),
        httpx.AsyncClient(timeout=60.0),
    )
    workers = Workers(clients=clients, procs=procs, ports=ports, logs=logs, cookie="")
    try:
        await asyncio.gather(
            _wait_healthy(clients[0], ports[0], procs[0], logs[0]),
            _wait_healthy(clients[1], ports[1], procs[1], logs[1]),
        )
        login = await clients[0].post(
            _url(ports[0], "/api/auth/login"),
            json={"username": USERNAME, "password": PASSWORD},
        )
        _check(login.status_code == 200, f"登录失败：{login.status_code} {login.text[:500]}")
        cookie = login.cookies.get(SESSION_COOKIE) or ""
        _check(cookie, "登录没有下发会话 cookie")
        for client in clients:
            client.cookies.set(SESSION_COOKIE, cookie)
        workers.cookie = cookie
        yield workers
    except BaseException:
        print(workers.tail(), file=sys.stderr)
        raise
    finally:
        for client in clients:
            await client.aclose()
        _stop(list(procs))


async def _create_project(client: httpx.AsyncClient, port: int, title: str) -> str:
    response = await client.post(_url(port, "/api/projects"), json={"title": title})
    _check(response.status_code == 201, f"建项目失败：{response.status_code} {response.text[:500]}")
    return str(response.json()["project_id"])


async def _start_round(client: httpx.AsyncClient, port: int, project_id: str) -> str:
    response = await client.post(
        _url(port, f"/api/projects/{project_id}/rounds"),
        json={"input": "想做个订单导出的功能"},
    )
    _check(response.status_code == 200, f"起轮失败：{response.status_code} {response.text[:500]}")
    return str(response.json()["round_id"])


async def _open_round(client: httpx.AsyncClient, port: int, round_id: str) -> httpx.Response:
    return await client.post(
        _url(port, f"/api/rounds/{round_id}/opening"),
        json={"seats": [SEAT], "web_queries": [], "approved_request_ids": [], "allow_web": False},
    )


async def _decide(
    client: httpx.AsyncClient, port: int, round_id: str, request_id: str
) -> httpx.Response:
    return await client.post(
        _url(port, f"/api/rounds/{round_id}/web-requests/{request_id}/decision"),
        json={"decision": "approve"},
    )


def _connect(db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _scalar(db: Path, sql: str, params: tuple[object, ...] = ()) -> int:
    connection = _connect(db)
    try:
        return int(connection.execute(sql, params).fetchone()[0])
    finally:
        connection.close()


def _evidence(db: Path, project_id: str) -> list[dict[str, Any]]:
    connection = _connect(db)
    try:
        row = connection.execute(
            "SELECT payload FROM evidence_ledgers WHERE project_id = ?", (project_id,)
        ).fetchone()
        _check(row is not None, f"项目 {project_id} 没有证据账本")
        return list(json.loads(row["payload"]))
    finally:
        connection.close()


def _external_refs(db: Path, project_id: str) -> list[str]:
    refs = [
        str(item["source"]["external_ref"])
        for item in _evidence(db, project_id)
        if item.get("kind") == "external"
    ]
    return refs


def _lease_row(db: Path, round_id: str) -> sqlite3.Row:
    connection = _connect(db)
    try:
        row = connection.execute(
            "SELECT owner_id, lease_owner, lease_token, lease_expires_at"
            " FROM rounds WHERE round_id = ?",
            (round_id,),
        ).fetchone()
        _check(row is not None, f"找不到轮次 {round_id}")
        return row
    finally:
        connection.close()


def _round_step(db: Path, round_id: str) -> str:
    connection = _connect(db)
    try:
        row = connection.execute(
            "SELECT step FROM rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        _check(row is not None, f"找不到轮次 {round_id}")
        return str(row["step"])
    finally:
        connection.close()


def _calls(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _model_calls(
    calls: list[dict[str, Any]],
    *,
    purpose: str,
    round_id: str | None = None,
    exploration_id: str | None = None,
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for call in calls:
        if call.get("kind") != "model" or call.get("purpose") != purpose:
            continue
        metadata = call.get("metadata") or {}
        if round_id is not None and metadata.get("round_id") != round_id:
            continue
        if exploration_id is not None and metadata.get("exploration_id") != exploration_id:
            continue
        found.append(call)
    return found


def _web_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [call for call in calls if call.get("kind") == "web_search"]


async def _wait_until(
    predicate: Callable[[], Any], *, timeout: float, what: str
) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise SmokeError(f"超时（{timeout}s）：{what}")


async def _wait_lease_expired(db: Path, round_id: str, *, timeout: float = 15.0) -> sqlite3.Row:
    def _expired() -> sqlite3.Row | None:
        row = _lease_row(db, round_id)
        expires = row["lease_expires_at"]
        if expires is not None and int(expires) <= wall_clock_ms():
            return row
        return None

    return await _wait_until(_expired, timeout=timeout, what=f"{round_id} 的租约没有过期")


def _assert_old_token_fenced(db: Path, round_id: str, old: sqlite3.Row) -> None:
    """旧 worker 的迟到写：用真仓储 + 旧 token 保存，必须抛 `LeaseLost`。"""

    owner = UserId(str(old["owner_id"]))
    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id=round_id,
        user_id=str(owner),
        holder=str(old["lease_owner"]),
        token=int(old["lease_token"]),
        expires_at_ms=int(old["lease_expires_at"]),
    )
    connection = _connect(db)
    try:
        repository = SqliteRoundRepository(connection)
        round_ = repository.get(RoundId(round_id), owner=owner)
        scope = LeaseScope(holder=token.holder, owner=owner)
        scope.bind(token)
        context_token = set_lease_scope(scope)
        try:
            try:
                repository.save(round_, owner=owner)
            except LeaseLost:
                return
            raise SmokeError("旧 token 的 save 没有被 fence（竟然写进去了）")
        finally:
            clear_lease_scope(context_token)
    finally:
        connection.close()


def _check_evidence_materials(
    db: Path,
    *,
    project_id: str,
    expected_evidence: int,
    expected_materials: int,
    expected_external: int,
) -> None:
    evidence = _evidence(db, project_id)
    materials = _scalar(
        db, "SELECT COUNT(*) FROM materials WHERE project_id = ?", (project_id,)
    )
    _check(
        len(evidence) == expected_evidence,
        f"E 条数不对：期望 {expected_evidence}，实际 {len(evidence)}",
    )
    _check(
        materials == expected_materials,
        f"材料数不对：期望 {expected_materials}，实际 {materials}",
    )
    refs = _external_refs(db, project_id)
    _check(
        len(refs) == expected_external,
        f"外部 E 条数不对：期望 {expected_external}，实际 {len(refs)}",
    )
    _check(len(refs) == len(set(refs)), f"外部 E 出现重复 external_ref：{refs}")
    material_ids = _scalar(
        db,
        "SELECT COUNT(DISTINCT c.material_id) FROM material_chunks c"
        " JOIN materials m ON m.material_id = c.material_id"
        " WHERE m.project_id = ?",
        (project_id,),
    )
    _check(
        material_ids == materials,
        f"材料块落了重复 material_id：材料 {materials} 份，不同 id {material_ids} 个",
    )


async def stage_opening(db: Path, script: Path, tmp: Path) -> None:
    print("[1/4] 同轮 opening：一个 200 / 一个 409", flush=True)
    call_log = tmp / "opening-calls.jsonl"
    async with _socket_gate() as (gate, gate_port):
        async with _workers(
            tmp=tmp,
            db=db,
            script=script,
            gate_port=gate_port,
            purpose="seat_review",
            call_log=call_log,
            stage="opening",
        ) as workers:
            project_id = await _create_project(workers.client(0), workers.port(0), "opening 并发")
            round_id = await _start_round(workers.client(0), workers.port(0), project_id)
            opening = asyncio.create_task(_open_round(workers.client(0), workers.port(0), round_id))
            await asyncio.wait_for(gate.arrived.wait(), timeout=30.0)
            _check(not opening.done(), "闸门到达时第一个 opening 不该已经结束")

            busy = await _open_round(workers.client(1), workers.port(1), round_id)
            _check(busy.status_code == 409, f"第二个 opening 应为 409，实际 {busy.status_code}")
            _check(
                busy.json().get("code") == "round_busy",
                f"第二个 opening 的 code 应为 round_busy：{busy.text[:500]}",
            )

            gate.release.set()
            opened = await asyncio.wait_for(opening, timeout=30.0)
            _check(opened.status_code == 200, f"第一个 opening 失败：{opened.text[:500]}")
            _check(opened.json().get("waiting_on") == "draft_card", "opening 后应停在草案卡")

            view_response = await workers.client(1).get(
                _url(workers.port(1), f"/api/rounds/{round_id}")
            )
            view = view_response.json()
            _check(view["step"] == "awaiting_confirm", f"step 不对：{view['step']}")
            _check(len(view["seat_packets"]) == 1, "同轮只该有一个席位包")
            _check(view["seat_packets"][0]["status"] == "completed", "席位包应已完成")
            _check(len(view["draft"]["proposals"]) == 1, "草案应恰好一条提议")

            _check_evidence_materials(
                db,
                project_id=project_id,
                expected_evidence=2,
                expected_materials=0,
                expected_external=0,
            )
            calls = _calls(call_log)
            _check(
                len(_model_calls(calls, purpose="claims", round_id=round_id)) == 1,
                "claims 应只调一次",
            )
            _check(
                len(_model_calls(calls, purpose="seat_review", round_id=round_id)) == 1,
                "seat_review 应只调一次（第二个请求没进图）",
            )
            _check(
                len(_model_calls(calls, purpose="integration", round_id=round_id)) == 1,
                "integration 应只调一次",
            )
            print("      ✓ 409 round_busy；放闸后 200；E=2 / 材料=0；模型调用各 1", flush=True)


async def stage_decision(db: Path, script: Path, tmp: Path) -> None:
    print("[2/4] 同轮 decision：一成一败 + 失败者重试", flush=True)
    call_log = tmp / "decision-calls.jsonl"
    async with _socket_gate() as (gate, gate_port):
        async with _workers(
            tmp=tmp,
            db=db,
            script=script,
            gate_port=gate_port,
            purpose="web_search",
            call_log=call_log,
            stage="decision",
        ) as workers:
            project_id = await _create_project(workers.client(0), workers.port(0), "decision 并发")
            round_id = await _start_round(workers.client(0), workers.port(0), project_id)
            opened = await _open_round(workers.client(0), workers.port(0), round_id)
            _check(opened.status_code == 200, f"opening 失败：{opened.text[:500]}")

            view_response = await workers.client(0).get(
                _url(workers.port(0), f"/api/rounds/{round_id}")
            )
            view = view_response.json()
            requests = [item for item in view["web_requests"] if item["state"] == "proposed"]
            _check(len(requests) == 2, f"应有 2 条 proposed 申请，实际 {len(requests)}")
            first, second = requests[0]["request_id"], requests[1]["request_id"]
            baseline = len(_evidence(db, project_id))

            winner = asyncio.create_task(
                _decide(workers.client(0), workers.port(0), round_id, first)
            )
            await asyncio.wait_for(gate.arrived.wait(), timeout=30.0)
            _check(not winner.done(), "闸门到达时 decision 不该已经结束")

            loser = await _decide(workers.client(1), workers.port(1), round_id, second)
            _check(loser.status_code == 409, f"第二个 decision 应为 409，实际 {loser.status_code}")
            _check(
                loser.json().get("code") == "round_busy",
                f"第二个 decision 的 code 应为 round_busy：{loser.text[:500]}",
            )

            gate.release.set()
            won = await asyncio.wait_for(winner, timeout=30.0)
            _check(won.status_code == 200, f"第一个 decision 失败：{won.text[:500]}")
            retried = await _decide(workers.client(1), workers.port(1), round_id, second)
            _check(retried.status_code == 200, f"失败者重试仍失败：{retried.text[:500]}")

            final_response = await workers.client(0).get(
                _url(workers.port(0), f"/api/rounds/{round_id}")
            )
            final = final_response.json()
            states = {item["request_id"]: item["state"] for item in final["web_requests"]}
            _check(states[first] == "done", f"第一条申请状态应为 done：{states}")
            _check(states[second] == "done", f"重试的那条应 done：{states}")

            calls = _calls(call_log)
            searches = _web_calls(calls)
            _check(len(searches) == 2, f"联网应恰好 2 次，实际 {len(searches)}")
            _check(
                sorted(call["query"] for call in searches) == sorted((QUERY_A, QUERY_B)),
                f"联网查询不对：{[call['query'] for call in searches]}",
            )
            _check_evidence_materials(
                db,
                project_id=project_id,
                expected_evidence=baseline + 2,
                expected_materials=2,
                expected_external=2,
            )
            _check(
                len(_model_calls(calls, purpose="claims", round_id=round_id)) == 1,
                "decision 阶段不该重复调 claims",
            )
            print(
                "      ✓ 409 round_busy；放闸后 200；重试 200；联网各 1；E/材料无重复",
                flush=True,
            )


async def stage_exploration(db: Path, script: Path, tmp: Path) -> None:
    print("[3/4] 同项目 exploration：一趟 200 / 一趟 409", flush=True)
    call_log = tmp / "exploration-calls.jsonl"
    async with _socket_gate() as (gate, gate_port):
        async with _workers(
            tmp=tmp,
            db=db,
            script=script,
            gate_port=gate_port,
            purpose="inquiry",
            call_log=call_log,
            stage="exploration",
        ) as workers:
            project_id = await _create_project(
                workers.client(0), workers.port(0), "exploration 并发"
            )
            edited = await workers.client(0).post(
                _url(workers.port(0), f"/api/projects/{project_id}/edits"),
                json={
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s2"},
                    "text": "整理成文档",
                },
            )
            _check(edited.status_code == 200, f"准备文档内容失败：{edited.text[:500]}")

            body = {"kind": "inquiry", "scope": {"kind": "document"}}
            started = asyncio.create_task(
                workers.client(0).post(
                    _url(workers.port(0), f"/api/projects/{project_id}/explorations"), json=body
                )
            )
            await asyncio.wait_for(gate.arrived.wait(), timeout=30.0)
            _check(not started.done(), "闸门到达时 exploration 不该已经结束")

            busy = await workers.client(1).post(
                _url(workers.port(1), f"/api/projects/{project_id}/explorations"), json=body
            )
            _check(busy.status_code == 409, f"第二个 exploration 应为 409，实际 {busy.status_code}")
            _check(
                busy.json().get("code") == "exploration_busy",
                f"第二个 exploration 的 code 应为 exploration_busy：{busy.text[:500]}",
            )

            gate.release.set()
            done = await asyncio.wait_for(started, timeout=30.0)
            _check(done.status_code == 200, f"exploration 失败：{done.text[:500]}")
            result = done.json()
            _check(result["status"] == "done", f"exploration 状态应为 done：{result}")
            _check(len(result["candidates"]) == 2, "应恰好两条候选（一次调研）")

            rows = _scalar(
                db, "SELECT COUNT(*) FROM explorations WHERE project_id = ?", (project_id,)
            )
            running = _scalar(
                db,
                "SELECT COUNT(*) FROM explorations WHERE project_id = ? AND status = 'running'",
                (project_id,),
            )
            _check(rows == 1, f"同项目应只落一趟 exploration，实际 {rows}")
            _check(running == 0, "跑完后不该留下 running 行")
            calls = _calls(call_log)
            _check(
                len(_model_calls(calls, purpose="inquiry", exploration_id=result["exploration_id"]))
                == 1,
                "inquiry 应只调一次",
            )
            print("      ✓ 409 exploration_busy；放闸后 200；只落一趟；inquiry=1", flush=True)


async def stage_sigkill(db: Path, script: Path, tmp: Path) -> None:
    print("[4/4] SIGKILL 接管 / old token fence / abandon 保持", flush=True)
    call_log = tmp / "sigkill-calls.jsonl"
    async with _socket_gate() as (gate, gate_port):
        async with _workers(
            tmp=tmp,
            db=db,
            script=script,
            gate_port=gate_port,
            purpose="seat_review",
            call_log=call_log,
            stage="sigkill",
            lease_ttl=1.5,
        ) as workers:
            project_id = await _create_project(workers.client(0), workers.port(0), "SIGKILL 接管")
            round_id = await _start_round(workers.client(0), workers.port(0), project_id)
            opening = asyncio.create_task(_open_round(workers.client(0), workers.port(0), round_id))
            await asyncio.wait_for(gate.arrived.wait(), timeout=30.0)
            _check(not opening.done(), "闸门到达时 opening 不该已经结束")

            old = _lease_row(db, round_id)
            _check(old["lease_owner"] is not None, "被杀的 worker 应持有 round lease")
            _check(old["lease_expires_at"] is not None, "被杀的 worker 应写过期时间")

            workers.proc(0).kill()
            await asyncio.to_thread(workers.proc(0).wait, 10)
            _check(
                workers.proc(0).returncode == -signal.SIGKILL,
                f"worker 不是被 SIGKILL 收掉的：returncode={workers.proc(0).returncode}",
            )
            try:
                response = await asyncio.wait_for(opening, timeout=15.0)
            except (TimeoutError, httpx.HTTPError):
                pass
            else:
                raise SmokeError(f"worker 被 SIGKILL 后 opening 仍返回 {response.status_code}")

            expired = await _wait_lease_expired(db, round_id)
            _check(
                int(expired["lease_token"]) == int(old["lease_token"]),
                "过期阶段不该有人换过 token",
            )

            gate.release.set()
            takeover = await workers.client(1).post(
                _url(workers.port(1), f"/api/rounds/{round_id}/retry")
            )
            _check(takeover.status_code == 200, f"接管 retry 失败：{takeover.text[:500]}")
            _check(takeover.json().get("waiting_on") == "draft_card", "接管后应停在草案卡")

            after = _lease_row(db, round_id)
            _check(
                int(after["lease_token"]) > int(old["lease_token"]),
                "接管应换 token（新 worker 拿到更大的 token）",
            )
            _check(
                after["lease_owner"] != old["lease_owner"],
                "接管后 lease_owner 应换人",
            )
            _assert_old_token_fenced(db, round_id, old)

            view_response = await workers.client(1).get(
                _url(workers.port(1), f"/api/rounds/{round_id}")
            )
            view = view_response.json()
            _check(view["step"] == "awaiting_confirm", f"接管后 step 不对：{view['step']}")
            _check(len(view["seat_packets"]) == 1, "接管后应只有一个席位包")
            _check(len(view["draft"]["proposals"]) == 1, "接管后草案应恰好一条提议")
            _check_evidence_materials(
                db,
                project_id=project_id,
                expected_evidence=2,
                expected_materials=0,
                expected_external=0,
            )
            calls = _calls(call_log)
            _check(
                len(_model_calls(calls, purpose="claims", round_id=round_id)) == 1,
                "SIGKILL 阶段 claims 应只调一次",
            )
            _check(
                len(_model_calls(calls, purpose="seat_review", round_id=round_id)) == 2,
                "SIGKILL 阶段 seat_review 应是 2 次尝试（被杀 1 + 接管 1）",
            )

            abandoned = await workers.client(1).post(
                _url(workers.port(1), f"/api/rounds/{round_id}/abandon")
            )
            _check(abandoned.status_code == 200, f"abandon 失败：{abandoned.text[:500]}")
            _check(abandoned.json().get("step") == "abandoned", "abandon 后 step 应为 abandoned")
            retried = await workers.client(1).post(
                _url(workers.port(1), f"/api/rounds/{round_id}/retry")
            )
            _check(retried.status_code == 200, f"abandon 后 retry 失败：{retried.text[:500]}")
            _check(retried.json().get("step") == "abandoned", "retry 把 abandoned 复活了")
            _check(_round_step(db, round_id) == "abandoned", "库里 step 不是 abandoned")
            print(
                "      ✓ SIGKILL 后 TTL 过期接管；旧 token save 被 fence；"
                "E=2 / 材料=0；abandon 保持",
                flush=True,
            )


def _write_script(tmp: Path) -> Path:
    path = tmp / "script.json"
    path.write_text(
        json.dumps(
            {
                "replies": {
                    "claims": CLAIMS_REPLY,
                    "integration": INTEGRATION_REPLY,
                    "inquiry": INQUIRY_REPLY,
                },
                "seat_replies": {SEAT: SEAT_REPLY},
                "web_results": WEB_RESULTS,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


async def run() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pmstudio-p3b-smoke-"))
    db = tmp / "pmstudio.db"
    script = _write_script(tmp)
    print(f"临时目录：{tmp}", flush=True)
    try:
        await stage_opening(db, script, tmp)
        await stage_decision(db, script, tmp)
        await stage_exploration(db, script, tmp)
        await stage_sigkill(db, script, tmp)
    except SmokeError as error:
        print(f"\n❌ {error}", file=sys.stderr)
        return 1
    print("\n✅ P3b 多 worker 并发与 SIGKILL 冒烟全部通过", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
