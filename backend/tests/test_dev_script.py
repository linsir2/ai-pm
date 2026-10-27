"""`scripts/dev.py` 的 fd 估算与 PG 启动前检查。

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
| **PostgreSQL 一条连接** | **1** | 每 worker 一条 psycopg（PgBouncer 在另一侧） |
| 事件扇出 reader | 1 | `redis_bus` 的订阅连接 |
| 事件扇出 publisher | 1 | `redis_bus` 的 publisher（懒建，用一次占一个） |
| 登录限流 Redis 池 | **0..max_connections** | `redis_limiter` 的连接池上界 |
| 每条在飞 HTTP/SSE 连接 | 1 | — |

SQLite 时代一个连接占 3 个 fd（库 / `-wal` / `-shm`）；切 PG 后是 1 个。
"""

from scripts import dev
from scripts.dev import FIXED_FDS_PER_WORKER, fd_budget_for, fd_pressure


def test_固定开销是_10():
    """R：上面那张表的加总。P：直接读常量；R：3+3+1+1+1+1 = 10。"""

    assert FIXED_FDS_PER_WORKER == 10


def test_估算把_固定开销_与_连接数_都算进去():
    """R：**改前的估法只算 `workers × 200`，漏了 10 + Redis 池**。"""

    assert fd_budget_for(workers=4, redis_pool=256, connections_per_worker=200) == 4 * (
        10 + 256 + 200
    )


def test_没配Redis时池算零():
    """R：没配 Redis 就没有登录限流的池。I：redis_pool=0；P：调公式；
    R：4 × (10 + 0 + 200)。"""

    assert fd_budget_for(workers=4, redis_pool=0, connections_per_worker=200) == 4 * 210


def test_单worker也要算固定开销():
    """R：改前 `workers > 1` 才提示，于是**单 worker 的固定开销完全没人看见**。"""

    assert fd_budget_for(workers=1, redis_pool=256, connections_per_worker=200) == 466


def test_软限不够时判定为有压力():
    """R：这是唯一能自动判的事——**保证占用**（固定 + 池），不含未知的同时在飞连接。"""

    assert fd_pressure(soft_limit=1024, workers=4, redis_pool=256) is True


def test_软限够时判定为无压力():
    """R：不制造噪音。I：soft=1048576；P：判定；R：无压力。"""

    assert fd_pressure(soft_limit=1_048_576, workers=4, redis_pool=256) is False


def test_池比连接数大得多_所以判定按池算():
    """R：`redis_max_connections` 默认 256 远大于连接数，所以**池才是主导项**。"""

    assert fd_pressure(soft_limit=4 * (10 + 10) + 10, workers=4, redis_pool=10) is False
    assert fd_pressure(soft_limit=4 * (10 + 10) + 10, workers=4, redis_pool=256) is True


def test_worker为零不该判成有压力():
    """R：worker=0 是配置错误，不是 fd 压力——**别用错误的原因报正确的错**。"""

    assert fd_pressure(soft_limit=10, workers=0, redis_pool=256) is False


def test_dev启动前缺数据库URL直接返回2(monkeypatch, capsys):
    """R：backend 只认 app URL；缺了必须在起 uvicorn 之前失败。"""

    monkeypatch.setattr(dev, "load_env_file", lambda path: 0)
    monkeypatch.setenv("PMSTUDIO_MODEL", "openai/test-model")
    monkeypatch.setenv("PMSTUDIO_API_KEY", "test-key")
    monkeypatch.delenv("PMSTUDIO_DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["dev.py", "--no-reload"])
    started: list[object] = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: started.append(args))

    assert dev.main() == 2
    assert started == []
    assert "PMSTUDIO_DATABASE_URL" in capsys.readouterr().err


def test_dev打印库地址时不泄露口令(monkeypatch, capsys):
    """R：配置里有密码，启动日志里不许出现。"""

    monkeypatch.setattr(dev, "load_env_file", lambda path: 0)
    monkeypatch.setenv("PMSTUDIO_MODEL", "openai/test-model")
    monkeypatch.setenv("PMSTUDIO_API_KEY", "test-key")
    monkeypatch.setenv(
        "PMSTUDIO_DATABASE_URL",
        "postgresql://pmstudio_app:super-secret@pgbouncer:6432/pmstudio",
    )
    monkeypatch.setattr("sys.argv", ["dev.py", "--no-reload"])
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: None)

    assert dev.main() == 0
    out = capsys.readouterr().out
    assert "super-secret" not in out
    assert "pgbouncer:6432" in out
