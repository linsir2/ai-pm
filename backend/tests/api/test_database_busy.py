"""数据库写争用 → 503 + `Retry-After`（高并发 P5b）。

## 为什么这件事值得单独一个模块

多 worker 把 SQLite 的写并发提上来之后，`BEGIN IMMEDIATE` 等锁 10 秒
（`database.py` 的 `timeout=10.0`）拿不到锁就抛 `sqlite3.OperationalError`。
而 `api/app.py` 原本**只注册了 `DomainError` / `ApplicationError` 两个 handler**
——`OperationalError` 直接落到 FastAPI 默认处理，成一个**裸 500**。

裸 500 传达的信息是错的：它让客户端以为是自己操作错了、或者数据坏了，
于是重试也重试（还是 500）。而等锁超时是**暂时性**的——持锁者是个短事务，
多半早就结束了。标准答案就是 **503 + `Retry-After`**，让客户端知道"等一下再来"。

## 这组是真端到端，不打桩

做法：把 busy timeout 调短（默认 10 秒太长，测试等不起），再用**第二条真连接**
`BEGIN EXCLUSIVE` 把库真锁住，然后走完整 HTTP 链路发一个会写库的请求。
走的是真的 `OperationalError`、真的 handler、真的响应头。

## 另一类 sqlite 错误不许被伪装成"忙"

`OperationalError("no such table: xxx")` 是**代码 bug**，不是忙。
把它也回 503 等于告诉客户端"等一下就好"——而它永远不会好。
"""

import sqlite3
from pathlib import Path

import pytest

from pmstudio.api.errors import is_busy_error
from pmstudio.harness.errors import DatabaseBusy
from pmstudio.infrastructure.sqlite import database as database_module
from tests.e2e.conftest import build_app
from tests.e2e.test_isolation_matrix import _seed_active_user  # noqa: E402
from tests.support.api_client import async_client

PW = "alice-s3cret"

#: 把 busy timeout 调到这么短：测试等不起 10 秒，而**等锁超时这个事实不变**。
SHORT_TIMEOUT = 0.05


class _ShortTimeoutSqlite:
    """代理 `sqlite3`，只把 `connect` 的 `timeout` 调短。

    **不在测试里复制 `connect()` 的逻辑**——复制了就等于测的是那份复制品，
    不是生产代码路径。生产仍然跑 `database.connect()` 原函数，只是它拿到的
    `timeout` 被换成 0.05 秒，**"等锁超时"这个事实本身完全不变**。
    """

    def __getattr__(self, name: str):  # noqa: ANN204
        return getattr(sqlite3, name)

    def connect(self, *args: object, **kwargs: object):  # noqa: ANN202
        kwargs["timeout"] = SHORT_TIMEOUT
        return sqlite3.connect(*args, **kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def short_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database_module, "sqlite3", _ShortTimeoutSqlite())


@pytest.fixture()
def locked_app(tmp_path: Path, short_timeout):
    app, _ = build_app(tmp_path, replies={}, autologin=False, first_admin=True)
    return app


def _take_write_lock(tmp_path: Path) -> sqlite3.Connection:
    """开一条真连接并 `BEGIN EXCLUSIVE`，把库真锁住。

    **是函数不是 fixture**：锁必须在"用户与项目都准备好之后"才取。
    做成 fixture 的话它先于测试体执行，于是 `_seed_active_user` 的 INSERT
    先撞上锁——失败发生在 setup 而不是被测的那次写。
    """

    blocker = sqlite3.connect(
        tmp_path / "pmstudio.db", isolation_level=None, timeout=SHORT_TIMEOUT
    )
    blocker.execute("BEGIN EXCLUSIVE")
    return blocker


# ── 判据本身 ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("database is locked", True),
        ("database table is locked", True),
        ("database is busy", True),
        ("no such table: rounds", False),
        ("attempt to write a readonly database", False),
        ("disk I/O error", False),
    ],
)
def test_只把等锁超时认成忙(message: str, expected: bool):
    """R：`no such table` / `readonly database` 是**代码 bug 或部署错**，不是忙。
    把它们也回 503 等于让客户端干等一个永远不会好的结果。

    I：六种真实 sqlite 消息；P：逐个判；R：只有 locked/busy 算忙。"""

    assert is_busy_error(sqlite3.OperationalError(message)) is expected


def test_错误对象自带_retry_after():
    """R：客户端要的是"等多久"，不是一个空的 503。"""

    busy = DatabaseBusy("数据库正忙", retry_after=3)

    assert busy.retry_after == 3
    assert busy.code == "database_busy"


# ── 端到端 ──────────────────────────────────────────────────────────────────


async def test_库被锁住时写请求回_503_带_RetryAfter(locked_app, tmp_path):
    """**本组存在的理由**。改前这是裸 500。

    I：第二条连接 `BEGIN EXCLUSIVE` 真锁住库；P：走 HTTP 发一个会写库的请求；
    R：503、`Retry-After` 为正整数、body `code == "database_busy"`。"""

    async with async_client(locked_app) as client:
        _seed_active_user(locked_app, username="alice", password=PW)
        await client.post(
            "/api/auth/login", json={"username": "alice", "password": PW}
        )
        created = await client.post("/api/projects", json={"title": "锁测试"})
        project_id = created.json()["project_id"]

        blocker = _take_write_lock(tmp_path)
        try:
            response = await client.post(
                f"/api/projects/{project_id}/edits",
                json={
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": "x",
                },
            )
        finally:
            blocker.rollback()
            blocker.close()

    assert response.status_code == 503, f"等锁超时回了 {response.status_code}"
    assert response.json()["code"] == "database_busy"
    assert int(response.headers["Retry-After"]) > 0


async def test_没有争用时写请求正常(locked_app):
    """R：加 handler 不能误伤健康路径。"""

    async with async_client(locked_app) as client:
        _seed_active_user(locked_app, username="alice", password=PW)
        await client.post(
            "/api/auth/login", json={"username": "alice", "password": PW}
        )
        created = await client.post("/api/projects", json={"title": "正常写"})
        project_id = created.json()["project_id"]

        response = await client.post(
            f"/api/projects/{project_id}/edits",
            json={"op": "add", "target": {"zone": "body", "section_id": "s1"}, "text": "x"},
        )

    assert response.status_code == 200, response.text