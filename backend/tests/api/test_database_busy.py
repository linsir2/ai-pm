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

## 磁盘满是第三类：507，不带 `Retry-After`

`OperationalError("database or disk is full")` 既不是忙也不是 bug，是**容量耗尽**。
它不能进 503 ——**判据是 `Retry-After` 那个头**能不能兑现：写锁 3 秒后大概率解开，
我们**兑现得起**；磁盘满要等有人在删文件，**兑现不起**，给 `Retry-After: 3`
就是教客户端在一个永远不会好的结果上重试。所以走 RFC 4918 的 **507**。
"""

import sqlite3
from pathlib import Path
from sqlite3 import OperationalError

import pytest

from pmstudio.api.errors import is_busy_error, is_storage_full_error, status_for
from pmstudio.harness.errors import DatabaseBusy, StorageFull
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


# ── 磁盘满 → 507（真 tmpfs 注入，见 scripts/disk_full_probe.py）──────────────
# 下面是**纯判据**的断言（不挂 tmpfs）；真 ENOSPC 的那一条在
# tests/e2e/test_disk_full.py ——它要挂载，所以单列。


def test_磁盘满判据认_sqlite_的原话():
    """R：认的是 SQLite 真报的那句，不是猜的。

    真跑（4M tmpfs 填满）拿到的原话就是 `database or disk is full`，
    所以判据照抄这句——写成 "full" 会把 `unable to open database file` 之类的
    无关错也吞进来。"""

    assert is_storage_full_error(OperationalError("database or disk is full")) is True


def test_磁盘满判据认_out_of_disk_space():
    """R：换个说法也要认，否则换个平台就漏判。"""

    assert is_storage_full_error(OperationalError("out of disk space")) is True


def test_等锁超时不算磁盘满():
    """R：两者**必须**分开——等锁回 503 + 一个我们兑现得起的 `Retry-After`，
    磁盘满回 507（重试无用，得有人腾空间）。混了就等于给了个兑现不了的承诺。"""

    assert is_storage_full_error(OperationalError("database is locked")) is False


def test_读不了库不算磁盘满():
    """R：`unable to open database file` 是路径/权限/文件不存在，
    归它 507 等于告诉运维"去腾磁盘"——**指错了方向**。"""

    assert is_storage_full_error(OperationalError("unable to open database file")) is False


def test_只读库不算磁盘满():
    """R：只读库是部署错（挂载选项/文件属主），回 507 也是指错方向。"""

    assert is_storage_full_error(OperationalError("attempt to write a readonly database")) is False


def test_507这个码登记在册():
    """R：`status_for` 靠 `CODE_STATUS` 查表，新增码必须登记——
    没登记就落到默认的 500，**静默变成我们要修的那个 bug**。"""

    assert status_for(StorageFull("磁盘满了")) == 507


def test_磁盘满的码就是_storage_full():
    """R：码本身是**给运维看的**（前端只透传 message，但日志/告警按 code 聚合）。
    `insufficient_storage` 与 HTTP 语义对齐，`storage_full` 更直白——取后者。"""

    assert StorageFull("磁盘满了").code == "storage_full"


def test_磁盘满不带RetryAfter():
    """R：**刻意不给 `Retry-After`**。判据：那个头是"3 秒后再来"的**承诺**，
    写锁我们兑现得起，磁盘满兑现不起——得有人在删文件。
    给了就是教客户端在一个永远不会好的结果上重试。"""

    error = StorageFull("磁盘满了")

    assert error.retry_after is None
