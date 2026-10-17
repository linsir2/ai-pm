#!/usr/bin/env python3
"""真多 worker 端到端冒烟（高并发 P4）——**两个真 uvicorn 进程**。

单元测试用两个 bus 实例模拟两个 worker，验的是机制；这一份验的是**进程边界本身**：
TCP、event loop、`contextvars`（owner_scope 是 contextvar，跨进程语义完全不同）、
以及"发布方随机落在哪个 worker 上"这个真实情况。

判据只有一条：**一个项目的事件，要推到连在任意 worker 上的浏览器**。
所以它数的是「每个 worker 的 SSE 连接各自收到了几条」，而不是总数。

跑法：

    PMSTUDIO_TEST_REDIS_URL=redis://127.0.0.1:6379/9 \\
        python3 scripts/multiworker_smoke.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src"
sys.path.insert(0, str(SRC))

#: cookie 名字**从源头导入**，不硬编码——我第一版写死成 `pmstudio_session`，
#: 真名是 `pm_session`，整个脚本在 401 上白跑一轮。
from pmstudio.api.auth import SESSION_COOKIE  # noqa: E402

WORKERS = 2
TRIGGERS = 12
USER = "smoker"
PASSWORD = "smoke-password-123"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict | None = None,
    cookie: str | None = None,
) -> tuple[int, dict, str]:
    """裸 TCP 发一个 HTTP/1.1 请求。**不用 httpx**：SSE 要读长响应，
    而 aiohttp/httpx 在这里只会多一层我们要验的东西。"""

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    payload = json.dumps(body).encode() if body is not None else b""
    lines = [
        f"{method} {path} HTTP/1.1",
        f"Host: 127.0.0.1:{port}",
        "Content-Type: application/json",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    if cookie:
        lines.append(f"Cookie: {SESSION_COOKIE}={cookie}")
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode() + payload)
    await writer.drain()
    raw = await reader.read()
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()
    head, _, tail = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1]) if head else 0
    try:
        parsed = json.loads(tail or b"{}")
    except json.JSONDecodeError:
        parsed = {}
    return status, parsed, raw.decode("utf-8", "replace")


def _cookie_from(headers: str) -> str:
    for line in headers.splitlines():
        if line.lower().startswith("set-cookie:"):
            return line.split("=", 1)[1].split(";", 1)[0].strip()
    return ""


async def _sse(port: int, cookie: str, out: list[dict]) -> None:
    """连一条 SSE，把收到的业务事件记进 out。断了就安静退。"""

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        (
            "GET /api/events HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Cookie: {SESSION_COOKIE}={cookie}\r\n"
            "Accept: text/event-stream\r\n\r\n"
        ).encode()
    )
    await writer.drain()

    buffer = ""
    while True:
        chunk = await reader.read(4096)
        if not chunk:
            return
        buffer += chunk.decode("utf-8", "replace")
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            for line in frame.splitlines():
                if line.startswith("data: "):
                    with contextlib.suppress(json.JSONDecodeError):
                        out.append(json.loads(line[6:]))


async def _wait_healthy(port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, _ = await _request(port, "GET", "/api/health")
            if status == 200:
                return True
        except OSError:
            pass
        await asyncio.sleep(0.2)
    return False


def _spawn(port: int, db: Path, redis_url: str, log: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
        "PMSTUDIO_MODEL": "scripted",
        "PMSTUDIO_API_KEY": "smoke",
        "PMSTUDIO_DB": str(db),
        "PMSTUDIO_REDIS_URL": redis_url,
        "PMSTUDIO_FIRST_ADMIN_USER": USER,
        "PMSTUDIO_FIRST_ADMIN_PASSWORD": PASSWORD,
        "PMSTUDIO_SESSION_TTL_DAYS": "1",
    }
    return subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "pmstudio.bootstrap.container:build_app", "--factory",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(BACKEND),
        env=env,
        stdout=log.open("w"),
        stderr=subprocess.STDOUT,
    )


def _pick(ports: list[int]) -> int:
    """故意随机挑——真实发布方落在哪个 worker 上不可控，这正是要验的分布。"""

    return ports[time.monotonic_ns() % len(ports)]


async def run() -> int:
    redis_url = os.environ.get("PMSTUDIO_TEST_REDIS_URL")
    if not redis_url:
        print("需要 PMSTUDIO_TEST_REDIS_URL（指向独立测试库）", file=sys.stderr)
        return 2

    ports = [_free_port() for _ in range(WORKERS)]
    tmp = Path(tempfile.mkdtemp(prefix="pmstudio-p4-"))
    logs = [tmp / f"worker{i}.log" for i in range(WORKERS)]
    procs = [
        _spawn(ports[i], tmp / "p.db", redis_url, logs[i]) for i in range(WORKERS)
    ]

    try:
        for index, port in enumerate(ports):
            if not await _wait_healthy(port):
                print(f"worker{index} 没起来：\n{logs[index].read_text()[-1500:]}")
                return 1
        print(f"[1/4] {WORKERS} 个 worker 都健康：{ports}")

        status, body, headers = await _request(
            ports[0], "POST", "/api/auth/login",
            body={"username": USER, "password": PASSWORD},
        )
        if status != 200:
            print(f"登录失败 {status}: {body}")
            return 1
        cookie = _cookie_from(headers)
        if os.environ.get("PMSTUDIO_SMOKE_DEBUG"):
            print("--- 登录响应头 ---")
            print(headers.split("\r\n\r\n")[0][:600])
        print(f"[2/4] 登录成功（会话 {cookie[:8]}…）")

        status, body, headers = await _request(
            ports[0], "POST", "/api/projects", body={"title": "P4 冒烟"}, cookie=cookie
        )
        if os.environ.get("PMSTUDIO_SMOKE_DEBUG"):
            print(f"--- 建项目请求：cookie={cookie!r}")
            print(headers.split("\r\n\r\n")[0][:400])
        if status not in (200, 201):
            print(f"建项目失败 {status}: {body}")
            return 1
        project = str(body["project_id"])
        print(f"[3/4] 项目 {project} 已建")

        received: list[list[dict]] = [[] for _ in ports]
        streams = [
            asyncio.create_task(_sse(ports[i], cookie, received[i])) for i in range(WORKERS)
        ]
        await asyncio.sleep(1.2)

        codes: dict[int, int] = {}
        for index in range(TRIGGERS):
            port = _pick(ports)
            status, _, _ = await _request(
                port, "POST", f"/api/projects/{project}/rounds",
                body={"input": f"冒烟 {index}"}, cookie=cookie
            )
            codes[status] = codes.get(status, 0) + 1
        await asyncio.sleep(3.0)

        for task in streams:
            task.cancel()
        await asyncio.gather(*streams, return_exceptions=True)

        counts = [len(r) for r in received]
        print(f"[4/4] 触发 {TRIGGERS} 次（响应码分布 {codes}）")
        print(f"      每个 worker 的 SSE 收到：{dict(zip(range(WORKERS), counts, strict=True))}")

        empty = [i for i, n in enumerate(counts) if n == 0]
        if empty:
            print(f"\n❌ worker {empty} 一条都没收到 —— 跨进程扇出没生效")
            for index, log in enumerate(logs):
                text = log.read_text()
                if "降级" in text:
                    print(f"   worker{index} 有降级告警：{text[-400:]}")
            return 1
        print(f"\n✅ 两个 worker 的 SSE 都收到了事件：{counts}")
        print("   条数不等于触发次数是正常的：每次触发发多个事件，"
              "且 SSE 订阅队列满会丢最旧。")
        return 0
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=10)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))