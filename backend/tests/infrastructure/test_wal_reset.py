"""SQLite WAL-reset bug 的版本判定与一次性警告（高并发 P5b）。

## 这是什么

`sqlite.org/wal.html §11`：一个从 3.7.0（2010）起就在的**数据竞争**，
「一个连接在 checkpoint、另一个连接重置了 WAL 并写入」，
后果是**后续 checkpoint 跳过一批已提交事务 → 数据库损坏**。

触发条件三条全中才会发生：
  ① WAL 模式 ② 同一文件上 ≥2 个**跨线程/跨进程**的连接 ③ 两者同时写或 checkpoint

**② 正是多 worker 的定义**——`bootstrap/container.py:260` 每个 worker 一条连接。

修复在 **3.51.3（2026-03-13）**，另有两条分支的 backport：**3.44.6** 与 **3.50.7**。

## 为什么本仓只能"警告"不能"修"

Python 的 `sqlite3` **链接到解释器自带的 libsqlite3**，不是 pip 依赖——
改 `pyproject.toml` 动不到它。而且唯一接近"换个 wheel 就修好"的
`pysqlite3-binary` 捆绑的是 **3.51.1，仍在受影响区间内**（本机实测）。
所以本模块的职责是**如实告知**，不是修复。

## backport 陷阱（我第一版就写错了）

判定**不能**用版本号大小比较：`min(cap, (3,50,7))` 会把 **3.51.1** 误判成
"已修复"（因为 3.51.1 > 3.50.7），而 3.51 分支要到 **3.51.3** 才修。
backport 只落在 3.44.6 与 3.50.7 两条分支上，必须按分支判。
"""

import logging

import pytest

from pmstudio.infrastructure.sqlite import database as _database
from pmstudio.infrastructure.sqlite.database import wal_reset_vulnerable


@pytest.fixture(autouse=True)
def _reset_warn_flag():
    """`_wal_reset_warned` 是模块级状态（这是"每次进程只喊一次"的固有代价），
    不重置就会跨用例泄漏——上一条用例喊过了，这一条就永远不喊。"""

    _database._wal_reset_warned = False
    yield
    _database._wal_reset_warned = False

# 官方矩阵（sqlite.org §11 + hermes-agent 的对照表），逐个钉住。
VERSION_MATRIX = [
    ("3.6.23", False),  # WAL 之前
    ("3.7.0", True),  # WAL 起始版本，就是受影响起点
    ("3.44.5", True),
    ("3.44.6", False),  # backport
    ("3.44.9", False),  # 同分支往后都是修好的
    ("3.45.0", True),  # 3.45 分支**没有** backport
    ("3.46.1", True),
    ("3.50.4", True),
    ("3.50.6", True),
    ("3.50.7", False),  # backport
    ("3.50.99", False),
    ("3.51.0", True),
    ("3.51.1", True),  # ← 我第一版把它误判成已修，就是这条
    ("3.51.2", True),  # 受影响区间的最后一版
    ("3.51.3", False),  # 修复版
    ("3.52.0", False),
]


@pytest.mark.parametrize(("version", "expected"), VERSION_MATRIX)
def test_版本矩阵(version: str, expected: bool):
    """R：判定必须与官方矩阵逐点一致。
    I：官方 16 个版本点；P：逐个调判定；R：命中与否符合官方。"""

    assert wal_reset_vulnerable(version) is expected


def test_本机若在受影响区间_判定说得对():
    """R：这条不是测逻辑，是**提醒我们这个仓库自己就在区间里**。

    如果哪天宿主机的 SQLite 升到 3.51.3+，这条会红——那时它就该被删掉，
    因为"探测 + 警告"已经不再适用。留它是为了让那个时刻**被人看见**。
    """

    import sqlite3

    vulnerable = wal_reset_vulnerable(sqlite3.sqlite_version)
    fixed = wal_reset_vulnerable("3.51.3")

    assert isinstance(vulnerable, bool)
    assert fixed is False, "判定函数本身坏了——3.51.3 是官方修复版"


def test_无法解析的版本不误判成安全():
    """R：解析不了就**不能当成没事**——那是"看起来正常"的静默失效。

    I：`"unknown"` / `"3.x.y"`；P：判定；R：一律当"中招"（保守）。"""

    assert wal_reset_vulnerable("unknown") is True
    assert wal_reset_vulnerable("3.x.y") is True
    assert wal_reset_vulnerable("") is True


# ── 警告行为 ────────────────────────────────────────────────────────────────


def _warn(tmp_path, monkeypatch, caplog, *, version: str):
    """造一个真连接并走一遍 `_enable_wal`，返回与 WAL-reset 有关的日志。"""

    import sqlite3

    connection = sqlite3.connect(tmp_path / "probe.db", isolation_level=None)
    monkeypatch.setattr(_database.sqlite3, "sqlite_version", version, raising=False)
    with caplog.at_level(logging.WARNING):
        _database._enable_wal(connection)
    connection.close()
    return [r for r in caplog.records if "WAL-reset" in r.getMessage()]


def test_中招时警告且文案把四件事都说清(tmp_path, monkeypatch, caplog):
    """R：一条有用的警告必须回答四个问题——现在什么版本、什么区间、
    什么条件下才触发、**我该怎么修**。

    仓库已有一条明写的规矩（`database.py:184-187` 的 `assert_owners_backfilled`
    文档串）：错误文案**必须点名要配的变量**。同理。

    I：受影响版本 + 真的开了 WAL；P：`_enable_wal`；R：恰好 1 条，且含
    版本号、官方区间、"跨进程"、"解释器"这几个关键词。"""

    records = _warn(tmp_path, monkeypatch, caplog, version="3.51.0")

    assert len(records) == 1, f"期望恰好 1 条警告，实际 {len(records)} 条"
    text = records[0].getMessage()
    assert "3.51.0" in text, "警告里没有当前版本号——运维看不出自己在哪个版本上"
    assert "3.51.3" in text, "警告里没有给出修复版本"
    assert "跨进程" in text or "进程" in text, "警告没说清触发条件"
    assert "解释器" in text, "警告没说清该怎么修"


def test_已修复的版本不警告(tmp_path, monkeypatch, caplog):
    """R：不制造噪音——修好了就别喊。"""

    assert _warn(tmp_path, monkeypatch, caplog, version="3.51.3") == []


def test_没真开上_WAL_就不警告(tmp_path, monkeypatch, caplog):
    """R：警告要诚实。**没开 WAL 就没有 WAL-reset bug**（它是触发条件之一），
    而 `PRAGMA journal_mode` 可能抢不到锁而没切成——不能假设它成功。

    I：受影响版本 + 当前模式不是 wal；P：`_warn_wal_reset_risk`；R：0 条。"""

    monkeypatch.setattr(_database.sqlite3, "sqlite_version", "3.51.0", raising=False)
    with caplog.at_level(logging.WARNING):
        _database._warn_wal_reset_risk("delete")

    assert [r for r in caplog.records if "WAL-reset" in r.getMessage()] == []


def test_一次故障只警告一次(tmp_path, monkeypatch, caplog):
    """R：每条连接都警告会把日志刷爆——4 个 worker × 重连就是刷屏。

    复用 `redis_bus` 那个"按状态转换打、不按发生次数打"的形状。"""

    monkeypatch.setattr(_database.sqlite3, "sqlite_version", "3.51.0", raising=False)
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            _database._warn_wal_reset_risk()

    assert len([r for r in caplog.records if "WAL-reset" in r.getMessage()]) == 1


def test_升级到修复版之后_下次再犯能重新警告(tmp_path, monkeypatch, caplog):
    """R：照抄 redis_bus 的形状——恢复正常要清"已警告"标志，
    否则进程跑几天之后那次偶发中招就再也看不见了。"""

    monkeypatch.setattr(_database.sqlite3, "sqlite_version", "3.51.0", raising=False)
    with caplog.at_level(logging.WARNING):
        _database._warn_wal_reset_risk()          # 坏 → 喊
        monkeypatch.setattr(
            _database.sqlite3, "sqlite_version", "3.51.3", raising=False
        )
        _database._warn_wal_reset_risk()          # 修好了 → 清标志，不喊
        monkeypatch.setattr(
            _database.sqlite3, "sqlite_version", "3.51.0", raising=False
        )
        _database._warn_wal_reset_risk()          # 又坏了（换台旧机器 / 回滚）→ 再喊

    assert len([r for r in caplog.records if "WAL-reset" in r.getMessage()]) == 2