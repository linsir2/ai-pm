"""`scripts/dev.py` 的 fd 估算（高并发 P5b）。

## 为什么值得单测

原来的提示只算 `workers × 200`（SSE 连接数），**漏掉了固定开销与 Redis 连接池**。
于是那条提示**即使触发也是错的**——它会让你以为 1024 够用，而真实占用差着
两个数量级（登录限流的连接池上界就是 256/worker）。

## 真实的 fd 清单（本机核验出来的，不是估的）

| 项 | 数量 | 出处 |
|---|---|---|
| stdin / stdout / stderr | 3 | — |
| asyncio epoll + self-pipe | 3 | 实测 `loop._selector.fileno()` / `_ssock` / `_csock` |
| uvicorn 监听 socket | 1 | `server.py` `create_server(sock=sock)` |
| **SQLite 一条连接** | **3** | 库文件 + `-wal` + `-shm`（`PRAGMA journal_mode=WAL`） |
| 事件扇出 reader | 1 | `redis_bus` 的订阅连接 |
| 事件扇出 publisher | 1 | `redis_bus` 的 publisher（懒建，用一次占一个） |
| 登录限流 Redis 池 | **0..max_connections** | `redis_limiter` 的连接池上界 |
| 每条在飞 HTTP/SSE 连接 | 1 | — |

**SQLite 每条连接占 3 个 fd 而不是 1 个**——最容易漏的一项，`ls /proc/<pid>/fd`
能直接看到 `.db` / `.db-wal` / `.db-shm` 三个。
"""

from scripts import dev
from scripts.dev import (
    FIXED_FDS_PER_WORKER,
    fd_budget_for,
    fd_pressure,
    wal_reset_blocker,
)


def test_固定开销是_12():
    """R：上面那张表的加总。P：直接读常量；R：3+3+1+3+1+1 = 12。

    其中 SQLite 那 3 个（库/-wal/-shm）是 P4 真跑多 worker 时才暴露的。"""

    assert FIXED_FDS_PER_WORKER == 12


def test_估算把_固定开销_与_连接数_都算进去():
    """R：**改前的估法只算 `workers × 200`，漏了 12 + Redis 池**——
    4 worker 时那是 1072 与 800 的差别，而 1024 正好卡在中间，会得出错误结论。

    I：4 worker、池 256、期望每 worker 200 连接；P：调公式；R：算出 4 × 468。"""

    assert fd_budget_for(workers=4, redis_pool=256, connections_per_worker=200) == 4 * (
        12 + 256 + 200
    )


def test_没配Redis时池算零():
    """R：没配 Redis 就没有登录限流的池。I：redis_pool=0；P：调公式；
    R：4 × (12 + 0 + 200)。"""

    assert fd_budget_for(workers=4, redis_pool=0, connections_per_worker=200) == 4 * 212


def test_单worker也要算固定开销():
    """R：改前 `workers > 1` 才提示，于是**单 worker 的那 12 + 池完全没人看见**——
    而登录限流的池在单 worker 下同样存在。"""

    assert fd_budget_for(workers=1, redis_pool=256, connections_per_worker=200) == 468


def test_软限不够时判定为有压力():
    """R：这是唯一能自动判的事——**保证占用**（固定 + 池），不含未知的同时在飞连接。
    I：soft=1024、4 worker、池 256；P：判定；R：有压力。"""

    assert fd_pressure(soft_limit=1024, workers=4, redis_pool=256) is True


def test_软限够时判定为无压力():
    """R：不制造噪音。I：soft=1048576；P：判定；R：无压力。"""

    assert fd_pressure(soft_limit=1_048_576, workers=4, redis_pool=256) is False


def test_池比连接数大得多_所以判定按池算():
    """R：`redis_max_connections` 默认 256 远大于连接数，所以**池才是主导项**。
    一个"按连接数算"的公式会漏掉它——那正是改前的错误。

    I：soft 刚好够 4×(12+10) 但不够 4×(12+256)；P：两次判定；R：前者无压、后者有压。"""

    assert fd_pressure(soft_limit=4 * (12 + 10) + 10, workers=4, redis_pool=10) is False
    assert fd_pressure(soft_limit=4 * (12 + 10) + 10, workers=4, redis_pool=256) is True


def test_worker为零不该判成有压力():
    """R：worker=0 是配置错误，不是 fd 压力——**别用错误的原因报正确的错**。"""

    assert fd_pressure(soft_limit=10, workers=0, redis_pool=256) is False


def test_多worker遇到受影响的SQLite拒启():
    """R：多 worker = 同库 ≥2 个跨进程连接，正好命中 WAL-reset 三条触发条件之一。
    I：2 worker + 本机 3.51.0；P：门禁；R：给出可执行理由。"""

    reason = wal_reset_blocker(2, "3.51.0")

    assert reason is not None
    assert "PMSTUDIO_WORKERS=2" in reason
    assert "3.51.0" in reason
    assert "PMSTUDIO_WORKERS=1" in reason


def test_单worker不受WAL门禁影响():
    """R：单 worker 不满足跨进程条件，不能拿 WAL 当理由拦。"""

    assert wal_reset_blocker(1, "3.46.1") is None


def test_已修复版本不拦多worker():
    """R：3.51.3 是官方修复版；3.44.6 / 3.50.7 是 backport。"""

    assert wal_reset_blocker(2, "3.51.3") is None
    assert wal_reset_blocker(2, "3.50.7") is None
    assert wal_reset_blocker(2, "3.44.6") is None


def test_dev启动前拦下多worker并返回退出码2(tmp_path, monkeypatch):
    """R：门禁必须在 uvicorn.run 之前生效——否则进程已经起来了才报错。
    I：真 main() + 受影响的 SQLite 版本；P：拦 uvicorn.run；R：返回 2 且没起服务。"""

    monkeypatch.setattr(dev.sqlite3, "sqlite_version", "3.51.0")
    monkeypatch.setenv("PMSTUDIO_MODEL", "openai/test-model")
    monkeypatch.setenv("PMSTUDIO_API_KEY", "test-key")
    monkeypatch.setenv("PMSTUDIO_WORKERS", "2")
    monkeypatch.setenv("PMSTUDIO_DB", str(tmp_path / "dev.db"))
    monkeypatch.setattr("sys.argv", ["dev.py", "--no-reload"])
    started: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: started.append(args))

    assert dev.main() == 2
    assert started == []
