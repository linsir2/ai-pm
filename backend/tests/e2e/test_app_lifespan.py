"""应用 lifespan 的守卫（Redis 启动修复，2026-10-18）。

## 为什么这个文件必须存在

`tests/` 原本**零处** `with TestClient(...)`——lifespan 从不运行。于是"不配 Redis
时后端起不来"（`container` 把没有 `start` 的 `InProcessEventBus` 当后台资源交给
lifespan，`await background.start()` 抛 `AttributeError`）在整套 e2e 全绿的情况下
逃过了。本文件补回**这一条**路径的覆盖：真进 `with`、真跑 lifespan。

## RIPR

- #1 不配 Redis 能起（主需求，真跑 lifespan）；
- #2 进程内总线**不是** `BackgroundResource`（钉住 B 方案：不给它加空 start/stop）；
- #3 Redis 总线**是** `BackgroundResource`（防守卫把 Redis 生命周期一起关掉）；
- #4 真传 background 时 start/stop 都被调（lifespan 回归锚）；
- #5 不带 Redis 起服务时照实说（`scripts/dev.py` 的空分支给了提示）。
"""

import sys

import uvicorn
from fastapi.testclient import TestClient
from scripts import dev

from pmstudio.api.app import BackgroundResource, create_app
from pmstudio.harness.events import InProcessEventBus
from pmstudio.infrastructure.redis_bus import RedisFanoutEventBus


class _RecordingBackground:
    """记录 lifespan 有没有碰它的假后台资源。"""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_不配Redis时应用能起(make_app):
    """R：不配 Redis（`Settings.redis_url` 默认 `""`）后端必须起得来。
    I：`make_app(replies={})`（`build_app` 内部就是默认空 redis_url）。
    P：`with TestClient(app)` 真跑 lifespan + 带 autologin cookie 请求 `/api/projects`。
    R：不抛 `AttributeError`、请求 200。

    改前这里抛 `'InProcessEventBus' object has no attribute 'start'`。"""

    app, _ = make_app(replies={})

    with TestClient(app) as client:
        client.cookies.update(app.state.test_cookie)
        response = client.get("/api/projects")

    assert response.status_code == 200


def test_进程内总线不是后台资源():
    """R：进程内总线没有可关的东西，"谁创建谁负责关"不适用于它——它**不是**
    `BackgroundResource`。这条若红，说明有人给 `InProcessEventBus` 加了空
    `start/stop`（那是被否决的方案 A：建空壳）。"""

    assert isinstance(InProcessEventBus(), BackgroundResource) is False


def test_Redis总线是后台资源():
    """R：`RedisFanoutEventBus` 有真得关的 reader/drainer——守卫不能把它也挡掉，
    否则多 worker 扇出的生命周期会被一起关掉。I：只构造、不 start；
    R：`isinstance` 为真。"""

    bus = RedisFanoutEventBus(url="redis://127.0.0.1:6379/9")

    assert isinstance(bus, BackgroundResource) is True


def test_传了后台资源时lifespan调用start与stop(make_app):
    """R：真传了 background 就要照旧 start/stop（lifespan 回归锚）。
    I：记录调用的假 `BackgroundResource` + 从应用取真 `services`。
    P：`create_app(services, background=fake)` + `with TestClient(...)`。
    R：进 `with` 后 started、出 `with` 后 stopped。"""

    app, _ = make_app(replies={})
    fake = _RecordingBackground()

    wired = create_app(app.state.services, background=fake)
    with TestClient(wired):
        assert fake.started is True
    assert fake.stopped is True


def test_不带Redis起服务时照实说(monkeypatch, capsys):
    """R：不配 Redis 起服务时，`dev.py` 要照实说走的是进程内总线（单 worker）。
    I：清掉 `PMSTUDIO_REDIS_URL`、补齐必填项、把 `uvicorn.run` 换掉不真起服务。
    P：调 `dev.main()`。R：输出含「事件：进程内总线」且不含「Redis 共享计数」。"""

    monkeypatch.setattr(dev, "load_env_file", lambda path: 0)
    monkeypatch.setenv("PMSTUDIO_MODEL", "probe-model")
    monkeypatch.setenv("PMSTUDIO_API_KEY", "probe-key")
    monkeypatch.setenv("PMSTUDIO_WORKERS", "1")
    monkeypatch.delenv("PMSTUDIO_REDIS_URL", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["dev.py", "--no-reload"])

    assert dev.main() == 0

    out = capsys.readouterr().out
    assert "事件：进程内总线（单 worker，不跨进程扇出）" in out
    assert "Redis 共享计数" not in out
