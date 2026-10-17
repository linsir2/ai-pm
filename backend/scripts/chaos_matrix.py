#!/usr/bin/env python3
"""混沌矩阵（高并发 P5）：六种故障，一条命令，一张表。

## 它是什么、不是什么

**是**：把"每种故障下按设计降级"钉成可重复跑的东西。每一行都是一次**真注入**
（真 HTTP 5xx、真超时、真断 TCP、真锁库、真 ENOSPC），断言的都是设计文档里
写过的行为——不是"没崩"，是"降级的方式对"。

**不是**：单元测试的替代品。分类口径（`classify`）、熔断时机、退避公式那些
纯逻辑，`tests/harness/` 与 `tests/infrastructure/` 已经盖住了；
这里只验**跨层接起来之后**的那几条缝。

## 六行分别是什么

| 行 | 故障 | 注入 | 按设计降级 |
|---|---|---|---|
| A | 上游 5xx | 桩 server 全回 500 | 计入熔断 → 开闸 → `ModelShed` → 恢复后闭合 |
| B | 上游慢响应 | 桩 server 睡 10s（网关 timeout 2s） | 超时计入 → 开闸 → 卸载 → 恢复后闭合 |
| C | Redis 强断 | TCP 代理掐掉已建立连接 | 降级可见 + 本机兜底 → 恢复 → 回到共享扇出 |
| D | SQLite 写锁 | 第二连接 `BEGIN EXCLUSIVE` | 503 + `Retry-After`（复用已有测试，不复制） |
| E | 磁盘满 | 4M tmpfs 写满 | 507 + 账本不裂 + 可恢复（复用已有测试，不复制） |

D/E **不复制**：`tests/api/test_database_busy.py` 与 `tests/e2e/test_disk_full.py`
已经是真注入（真锁/真 tmpfs），矩阵直接跑它们，挂了就是那一行红——
同一份注入写两遍，总有一天两边漂移。

## 跑法

    PMSTUDIO_TEST_REDIS_URL=redis://127.0.0.1:6379/9 \\
        python3 scripts/chaos_matrix.py

C 行要真 Redis（pub/sub 语义只在真库上成立）；没有就整表跳过并说明原因，
**不假装通过**。A/B 行是真 `LiteLLMGateway` + 真 `CircuitBreaker` 打桩 server
（`openai/stub` + `api_base`，阈值调小只为跑得快，路径是全的）。

退出码：0 = 全绿；1 = 有红；2 = 环境跑不起来。
最后一行是 JSON（`tests/e2e/test_chaos_matrix.py` 只读它），前面的表是给人看的。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

BACKEND = Path(__file__).resolve().parents[1]
SRC = BACKEND / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(BACKEND))

REDIS_URL = os.environ.get("PMSTUDIO_TEST_REDIS_URL", "")

#: 网关超时（秒）。B 行桩睡 10s，2s 掐断——每次尝试恰好 2 秒，整行约 10 秒。
GATEWAY_TIMEOUT = 2.0
#: 桩慢模式睡多久。必须远大于 `GATEWAY_TIMEOUT`，否则掐不断，测的是别的。
STUB_SLOW_SLEEP = 10.0
#: 熔断阈值与恢复窗口（调小只为跑得快）。`minimum_samples=3`：3 次依赖失败开闸。
BREAKER_SAMPLES = 3
BREAKER_RECOVERY = 2.0


# ── 桩上游 ──────────────────────────────────────────────────────────────────
# 一个"会坏的上游"。三种模式运行时可切：全 500 / 睡死 / 正常。
# 只实现 `POST /v1/chat/completions` 够 litellm 走完——它要的只是一段能解析
# 成 `choices[0].message.content` 的 body（500 时连这个都不用给）。


class _StubState:
    def __init__(self) -> None:
        self.mode = "ok"
        self.hits = 0
        self._lock = threading.Lock()

    def hit(self) -> str:
        with self._lock:
            self.hits += 1
            return self.mode


_OK_BODY = json.dumps(
    {
        "id": "stub-1",
        "object": "chat.completion",
        "created": 1,
        "model": "stub",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
).encode()

_500_BODY = json.dumps(
    {"error": {"message": "upstream exploded", "type": "server_error"}}
).encode()


def _stub_server(state: _StubState) -> tuple[ThreadingHTTPServer, int]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # 安静
            pass

        def do_POST(self) -> None:  # noqa: N802
            # 网关 2s 掐断后桩还在睡（10s），醒来写的是已关闭的连接——
            # `BrokenPipeError` 是预料之中的，不刷屏。
            try:
                self._do_post()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _do_post(self) -> None:
            mode = state.hit()
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            if mode == "5xx":
                body = _500_BODY
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif mode == "slow":
                time.sleep(STUB_SLOW_SLEEP)
                self._ok()
            else:
                self._ok()

        def _ok(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(_OK_BODY)))
            self.end_headers()
            self.wfile.write(_OK_BODY)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


# ── TCP 代理（C 行）──────────────────────────────────────────────────────────
# 监听一个端口，原样转发到真 Redis。`kill()` 掐掉监听 + 所有已建立连接
# （这正是 P5 手工验证的那一下：不是"连不上"，是"连上了再断"）；
# `revive()` 在**同一个端口**上重新监听。


class _TcpProxy:
    def __init__(self, upstream_host: str, upstream_port: int) -> None:
        self._upstream = (upstream_host, upstream_port)
        self._server: asyncio.AbstractServer | None = None
        self._live: set[asyncio.Task] = set()
        #: 存活连接的写端。掐连接 = 关它们（`StreamWriter.close()` 会连 transport
        #: 一起关）。不碰 `get_extra_info("socket")`——3.13 给的是 `TransportSocket`
        #: 包装，没有 `close()`，调了就 `AttributeError`（实测）。
        self._writers: set[asyncio.StreamWriter] = set()
        self.port = 0

    async def start(self) -> int:
        await self.revive()
        return self.port

    async def revive(self) -> None:
        self._server = await asyncio.start_server(
            self._forward, "127.0.0.1", self.port or 0
        )
        sockets = self._server.sockets or []
        self.port = sockets[0].getsockname()[1]

    async def kill(self) -> None:
        # **先掐连接，再关监听**——顺序反了会卡死：`Server.wait_closed()` 要等
        # 存活连接全部结束（实测，有一条活连接就 hang），而存活连接正等着我们掐。
        # 谁等谁，死锁。所以这里根本不等 `wait_closed`——`close()` 同步关掉
        # 监听 socket 就够了（`revive()` 重新 bind，`reuse_address` 本来就开着）。
        for task in list(self._live):
            task.cancel()
        for stream in list(self._writers):
            with contextlib.suppress(Exception):  # noqa: BLE001
                stream.close()
        self._writers.clear()
        if self._live:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*self._live, return_exceptions=True), 5.0
                )
            self._live.clear()
        if self._server is not None:
            self._server.close()
            self._server = None

    async def close(self) -> None:
        await self.kill()

    async def _forward(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._live.add(task)
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                *self._upstream
            )
        except OSError:
            writer.close()
            return
        for stream in (writer, upstream_writer):
            self._writers.add(stream)
        try:
            await asyncio.gather(
                _pipe(reader, upstream_writer), _pipe(upstream_reader, writer)
            )
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            if task is not None:
                self._live.discard(task)
            for stream in (writer, upstream_writer):
                self._writers.discard(stream)
                with contextlib.suppress(Exception):  # noqa: BLE001
                    stream.close()


async def _pipe(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        with contextlib.suppress(Exception):  # noqa: BLE001
            writer.close()


# ── 行 A/B：模型故障 → 熔断 ──────────────────────────────────────────────────


def _gateway(port: int, breaker):  # noqa: ANN001, ANN202
    from pmstudio.harness.model.providers.litellm import LiteLLMGateway  # noqa: PLC0415
    from pmstudio.harness.model.resilience import BreakerConfig  # noqa: PLC0415

    return LiteLLMGateway(
        model="openai/stub",
        api_key="test-key",
        api_base=f"http://127.0.0.1:{port}/v1",
        timeout=GATEWAY_TIMEOUT,
        max_attempts=1,  # 每次调用恰好一次上游尝试——行内重试是另一组单测的事
        breaker=breaker,
        breaker_config=BreakerConfig(
            minimum_samples=BREAKER_SAMPLES, recovery_timeout=BREAKER_RECOVERY
        ),
    )


def _model_request():  # noqa: ANN202
    from pmstudio.harness.model.contracts import (  # noqa: PLC0415
        ModelMessage,
        ModelRequest,
    )

    return ModelRequest(
        purpose="chaos",
        messages=(ModelMessage(role="user", content="hi"),),
        schema_name="chaos",
        response_schema={"type": "object"},
    )


async def _row_model_fault(mode: str, label: str) -> dict:  # noqa: ANN201
    """A/B 行的公共形状：灌注 → 开闸 → 卸载 → 恢复 → 闭合。"""

    from pmstudio.harness.errors import ModelCallFailed, ModelShed  # noqa: PLC0415
    from pmstudio.harness.model.resilience import CircuitBreaker  # noqa: PLC0415

    state = _StubState()
    state.mode = mode
    server, port = _stub_server(state)
    breaker = CircuitBreaker.__new__(CircuitBreaker)
    # 走构造而不用默认阈值：默认 `minimum_samples=5, recovery_timeout=30`，
    # 那样这一行要跑半分钟——阈值是速度，路径是全的。
    from pmstudio.harness.model.resilience import (  # noqa: PLC0415
        BreakerConfig,
    )

    breaker.__init__(BreakerConfig(
        minimum_samples=BREAKER_SAMPLES, recovery_timeout=BREAKER_RECOVERY
    ))
    gateway = _gateway(port, breaker)
    detail: dict = {"mode": mode}
    try:
        # ── 灌注：每次都该是上游错（不是卸载），且计数涨 ──
        for _ in range(BREAKER_SAMPLES):
            try:
                await gateway.complete(_model_request())
                return _fail(label, detail, "灌注期调用居然成功了，上游没坏透")
            except ModelShed as shed:
                return _fail(label, detail, f"第几次就卸载了：{shed}")
            except ModelCallFailed:
                pass
        detail["failures"] = breaker.failures
        if breaker.failures != BREAKER_SAMPLES:
            return _fail(
                label, detail, f"计数是 {breaker.failures}，不是 {BREAKER_SAMPLES}"
            )
        if not breaker.is_open:
            return _fail(label, detail, "到阈值了还没开闸")
        hits_when_open = state.hits

        # ── 开闸：上游一次都不许再碰 ──
        try:
            await gateway.complete(_model_request())
            return _fail(label, detail, "开闸后调用居然成功了")
        except ModelShed:
            pass
        except ModelCallFailed as failed:
            return _fail(label, detail, f"开闸后漏过去了，没被卸载：{failed}")
        if state.hits != hits_when_open:
            return _fail(label, detail, "开闸后还在发上游请求——熔断在做无用功")
        detail["shed_without_upstream_hit"] = True

        # ── 恢复：上游好了，等闭合 ──
        # **轮询而不是睡一次就赌**：恢复窗口是"至少等这么久"，不是"准时开闸"。
        # 睡 `recovery+0.5` 然后只打一次，机器一抖就把时序当 bug 报。
        # 真正的判据是"上游健康之后，有限时间内闸会闭合、调用会成功"。
        state.mode = "ok"
        deadline = time.monotonic() + 15.0
        recovered = False
        while time.monotonic() < deadline:
            try:
                reply = await gateway.complete(_model_request())
                recovered = True
                break
            except ModelShed:
                await asyncio.sleep(0.5)
            except ModelCallFailed as failed:
                return _fail(label, detail, f"上游已经好了，调用还是失败：{failed}")
        if not recovered:
            detail["breaker_still_open"] = breaker.is_open
            detail["breaker_failures"] = breaker.failures
            return _fail(label, detail, "上游好了 15 秒，闸还没闭合")
        if breaker.is_open:
            return _fail(label, detail, "调用成功了但闸还开着——状态与行为不一致")
        detail["recovered_reply_ok"] = bool(reply.text)
        return _pass(label, detail)
    finally:
        server.shutdown()


def _fail(label: str, detail: dict, why: str) -> dict:  # noqa: ANN201
    return {"row": label, "status": "FAIL", "why": why, "detail": detail}


def _pass(label: str, detail: dict) -> dict:  # noqa: ANN201
    return {"row": label, "status": "PASS", "detail": detail}


# ── C 行：Redis 强断 → 降级 → 恢复 ────────────────────────────────────────────


def _split_redis_url(url: str) -> tuple[str, int, int]:
    """`redis://host:port/db` 拆三件。代理只认 host/port，db 照旧透传。"""

    rest = url.split("://", 1)[1]
    hostport, _, db = rest.partition("/")
    host, _, port = hostport.partition(":")
    return host or "127.0.0.1", int(port or 6379), int(db or 0)


async def _collect(bus, count: int, timeout: float):  # noqa: ANN001, ANN202
    """取 count 条，取不到就返回已有的——**由调用方判**，这里不替它失败。"""

    got = []
    iterator = bus.subscribe()
    try:
        async def pump() -> None:
            async for event in iterator:
                got.append(event)
                if len(got) >= count:
                    return

        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(pump(), timeout)
    finally:
        await iterator.aclose()
    return got


async def _wait_for(predicate, timeout: float, step: float = 0.2) -> bool:  # noqa: ANN001, ANN201
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(step)
    return predicate()


async def _row_redis_kill() -> dict:  # noqa: ANN201
    from pmstudio.harness.events import Event  # noqa: PLC0415
    from pmstudio.infrastructure.redis_bus import RedisFanoutEventBus  # noqa: PLC0415

    label = "C redis强断"
    host, port, db = _split_redis_url(REDIS_URL)
    proxy = _TcpProxy(host, port)
    await proxy.start()
    channel = f"pmstudio:chaos:{uuid4().hex[:8]}"
    url = f"redis://127.0.0.1:{proxy.port}/{db}"
    detail: dict = {}
    bus_a = RedisFanoutEventBus(
        url=url, channel=channel, probe_base_seconds=0.2, probe_max_seconds=2.0
    )
    bus_b = RedisFanoutEventBus(
        url=url, channel=channel, probe_base_seconds=0.2, probe_max_seconds=2.0
    )
    try:
        await bus_a.start()
        await bus_b.start()

        def event(tag: str) -> Event:
            return Event(
                name="doc.changed",
                project_id="chaos",
                occurred_at=datetime.now(UTC),
                owner_id="u1",
                payload={"tag": tag},
            )

        # ── 基线：代理通的时候跨 bus 可达 ──
        baseline = asyncio.create_task(_collect(bus_b, 1, timeout=5.0))
        await asyncio.sleep(0.3)
        bus_a.publish(event("before"))
        if len(await baseline) != 1:
            return _fail(label, detail, "基线就不通：代理通的时候跨 bus 收不到")
        detail["baseline_ok"] = True

        # ── 强断：掐掉已建立连接（不是"连不上"，是"连上了再断"） ──
        await proxy.kill()
        if not await _wait_for(lambda: bus_a.degraded or bus_b.degraded, timeout=10.0):
            return _fail(label, detail, "掐了 10 秒，两边都没报降级——静默了")
        detail["degraded_visible"] = True

        # ── 降级期：本机兜底还通（照抄 `test_redis断了降级到进程内` 的判据） ──
        local = asyncio.create_task(_collect(bus_a, 1, timeout=5.0))
        await asyncio.sleep(0.3)
        bus_a.publish(event("during"))
        if len(await local) != 1:
            return _fail(label, detail, "降级期本机事件都投不出去——兜底断了")
        detail["local_fallback_ok"] = True

        # ── 恢复：代理回来，回到共享扇出 ──
        # **先发一条垫背消息，再等全绿**——顺序不能反：drainer 腿只在"下一条
        # 消息"上自愈（见 `_drain_loop` 的注释：无消息就不重试），干等的话
        # drainer 永远是 down，那不是故障，是设计。垫背的那条也会被跨 bus
        # 投递（同 channel 保序），所以后面一次收两条并验顺序。
        await proxy.revive()
        await asyncio.sleep(0.3)
        # 收集器先挂上：heal 发出时对端 reader 可能还没重连上，丢了是
        # at-most-once 的设计行为（"降级期间漏掉的不补发"）——断言只认 after，
        # heal 到了是 bonus。
        after = asyncio.create_task(_collect(bus_b, 2, timeout=15.0))
        bus_a.publish(event("heal"))
        if not await _wait_for(
            lambda: not bus_a.degraded and not bus_b.degraded, timeout=25.0
        ):
            after.cancel()
            return _fail(label, detail, "代理回来了 25 秒，两边还没报恢复")
        detail["recovered"] = True
        await asyncio.sleep(0.3)
        bus_a.publish(event("after"))
        got = await after
        tags = [e.payload.get("tag") for e in got]
        if not tags or tags[-1] != "after":
            return _fail(label, detail, f"报了恢复但跨 bus 还是不通：{tags}——假恢复")
        detail["cross_bus_ok"] = True
        detail["heal_also_arrived"] = tags == ["heal", "after"]
        return _pass(label, detail)
    finally:
        await bus_a.stop()
        await bus_b.stop()
        await proxy.close()


# ── D/E 行：复用已有真注入测试 ───────────────────────────────────────────────


def _row_pytest(label: str, path: str) -> dict:  # noqa: ANN201
    """跑一份已有的真注入测试文件。挂了就是那一行红——不复制它们的注入。"""

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            path,
            "-q",
            "-p",
            "no:cacheprovider",
            "-x",
        ],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=BACKEND,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    tail = (completed.stdout + completed.stderr).strip().splitlines()[-3:]
    if completed.returncode == 0:
        return _pass(label, {"pytest": " ".join(tail)})
    if "skipped" in " ".join(tail).lower() and "failed" not in " ".join(tail).lower():
        return {"row": label, "status": "SKIP", "detail": {"pytest": " ".join(tail)}}
    return _fail(label, {}, f"pytest 退出码 {completed.returncode}：{' / '.join(tail)}")


# ── 主 ──────────────────────────────────────────────────────────────────────


async def _main() -> dict:  # noqa: ANN201
    rows = []
    stub_a = await _row_model_fault("5xx", "A 上游5xx")
    rows.append(stub_a)
    print(_line(stub_a))
    stub_b = await _row_model_fault("slow", "B 上游慢响应")
    rows.append(stub_b)
    print(_line(stub_b), flush=True)
    redis_row = await _row_redis_kill()
    rows.append(redis_row)
    print(_line(redis_row), flush=True)
    lock_row = _row_pytest("D 写锁争用", "tests/api/test_database_busy.py")
    rows.append(lock_row)
    print(_line(lock_row), flush=True)
    disk_row = _row_pytest("E 磁盘满", "tests/e2e/test_disk_full.py")
    rows.append(disk_row)
    print(_line(disk_row), flush=True)
    return {"matrix": "chaos", "rows": rows}


def _line(row: dict) -> str:  # noqa: ANN201
    mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️"}.get(row["status"], "?")
    extra = f" —— {row['why']}" if row.get("why") else ""
    return f"{mark} {row['row']}: {row['status']}{extra}"


def main() -> int:
    if not REDIS_URL:
        print(
            "⏭️ 整表跳过：没有 PMSTUDIO_TEST_REDIS_URL（C 行要真 Redis，"
            "pub/sub 语义只在真库上成立）。不假装通过。"
        )
        print(json.dumps({"matrix": "chaos", "rows": [], "skipped": "no-test-redis"}))
        return 2
    try:
        result = asyncio.run(_main())
    except Exception as error:  # noqa: BLE001 —— 矩阵自己崩了也是一行事实
        import traceback  # noqa: PLC0415

        result = {
            "matrix": "chaos",
            "rows": [],
            "crashed": f"{type(error).__name__}: {error}",
            "traceback": traceback.format_exc()[-2000:],
        }
        print(f"❌ 矩阵自己崩了：{error}")
        print(json.dumps(result, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    bad = [r for r in result["rows"] if r["status"] == "FAIL"]
    return 1 if bad or result.get("crashed") else 0


if __name__ == "__main__":
    raise SystemExit(main())