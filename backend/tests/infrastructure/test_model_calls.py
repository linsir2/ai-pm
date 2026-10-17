"""模型留样（`02` §5.3）：出问题时能复现（P4 的 `O1` 就是靠重放才定性的）。

它**不参与业务**：写不进去也不许影响这一轮（记录是旁路），所以 recorder 出错只吞不抛。
提示词存**原文**（截断）——只存 sha 复现不了，而复现是这张表唯一的目的。
"""

import logging
import sqlite3

from pmstudio.harness.model.prompt_log import PROMPT_LIMIT
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder
from tests.support.builders import NOW

#: 加 attempt/ok/error 之前的建表语句（用来验迁移）
OLD_MODEL_CALLS_DDL = """
CREATE TABLE model_calls (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  round_id    TEXT,
  purpose     TEXT NOT NULL,
  model       TEXT NOT NULL,
  prompt_sha  TEXT NOT NULL,
  prompt_text TEXT NOT NULL,
  reply_text  TEXT NOT NULL,
  usage_json  TEXT NOT NULL,
  duration_ms INTEGER NOT NULL,
  created_at  TEXT NOT NULL
);
"""


def test_recorder_writes_and_reads_back(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    recorder = SqliteModelCallRecorder(connection)

    recorder.record(
        trace_id="r1",
        purpose="seat_review",
        model="m",
        prompt_sha="abc",
        prompt_text="提示词全文",
        reply_text="{}",
        usage={"total_tokens": 12},
        duration_ms=1234,
        created_at=NOW,
    )

    rows = recorder.recent(round_id="r1")
    assert [(row.purpose, row.model, row.prompt_sha, row.duration_ms) for row in rows] == [
        ("seat_review", "m", "abc", 1234)
    ]
    assert rows[0].prompt_text == "提示词全文"
    assert rows[0].usage == {"total_tokens": 12}


def test_recorder_filters_by_round_and_keeps_the_newest_first(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    recorder = SqliteModelCallRecorder(connection)
    for index, trace_id in enumerate(("r1", "r2", "r1"), start=1):
        recorder.record(
            trace_id=trace_id,
            purpose="claims",
            model="m",
            prompt_sha=str(index),
            prompt_text="p",
            reply_text="{}",
            usage={},
            duration_ms=index,
            created_at=NOW,
        )

    rows = recorder.recent(round_id="r1")
    assert [row.prompt_sha for row in rows] == ["3", "1"]  # 新的在前


def test_recorder_truncates_a_huge_prompt(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    recorder = SqliteModelCallRecorder(connection)

    recorder.record(
        trace_id="r1",
        purpose="claims",
        model="m",
        prompt_sha="x",
        prompt_text="很长" * 20_000,
        reply_text="{}",
        usage={},
        duration_ms=1,
        created_at=NOW,
    )

    text = recorder.recent(round_id="r1")[0].prompt_text
    # 2026-09-29：改**保留头尾**——原来切掉的是提示词末尾的硬规矩与输出 schema，
    # 那正是复盘"模型为什么这么答"最想看的一段。
    assert text.startswith("很长")
    assert text.endswith("很长")
    assert "略去" in text
    assert len(text) <= PROMPT_LIMIT + 100


def test_recorder_survives_a_broken_write(tmp_path):
    """旁路就是旁路：记录失败不许把这一轮搞失败。"""

    connection = database.connect(tmp_path / "pm.db")
    connection.close()  # 连接按掉：写入必然失败
    recorder = SqliteModelCallRecorder(connection)

    recorder.record(
        trace_id="r1",
        purpose="claims",
        model="m",
        prompt_sha="x",
        prompt_text="p",
        reply_text="{}",
        usage={},
        duration_ms=1,
        created_at=NOW,
    )  # 不抛


def test_recorder_survives_usage_that_cannot_be_serialized(tmp_path):
    """usage 里混进不可序列化的对象时只吞掉，不许往上炸（真模型踩过）。"""

    connection = database.connect(tmp_path / "pm.db")
    recorder = SqliteModelCallRecorder(connection)

    class _Weird:
        pass

    recorder.record(
        trace_id="r1",
        purpose="claims",
        model="m",
        prompt_sha="x",
        prompt_text="p",
        reply_text="{}",
        usage={"details": _Weird()},
        duration_ms=1,
        created_at=NOW,
    )  # 不抛

    assert recorder.recent(round_id="r1") == ()


def test_schema_creates_the_table_on_an_old_db(tmp_path):
    """老库不用迁移：`connect()` 每次都跑 `schema.sql`（全是 CREATE TABLE IF NOT EXISTS）。"""

    path = tmp_path / "old.db"
    database.connect(path).close()
    connection = database.connect(path)
    names = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "model_calls" in names


def test_the_new_attempt_columns_roundtrip(tmp_path):
    """每次尝试一条留样：哪个尝试、成没成、失败原因（报告 §四建议 ①）。"""

    connection = database.connect(tmp_path / "pm.db")
    recorder = SqliteModelCallRecorder(connection)

    recorder.record(
        trace_id="r1",
        purpose="integration",
        model="m",
        prompt_sha="x",
        prompt_text="p",
        reply_text="",
        usage={},
        duration_ms=90001,
        created_at=NOW,
        attempt=1,
        ok=False,
        error="Timeout: 90s",
    )

    row = recorder.recent(round_id="r1")[0]
    assert (row.attempt, row.ok, row.error) == (1, False, "Timeout: 90s")


def test_an_old_db_gets_the_new_columns_without_losing_rows(tmp_path):
    """老库不重来：`connect()` 幂等补列，老行照旧读得回来（默认 1 / ok / 无原因）。"""

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(OLD_MODEL_CALLS_DDL)
    old.execute(
        "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
        " reply_text, usage_json, duration_ms, created_at) VALUES"
        " ('r1', 'claims', 'm', 'x', '老提示词', '{}', '{}', 12, '2026-09-01T00:00:00+00:00')"
    )
    old.commit()
    old.close()

    connection = database.connect(path)
    names = {row["name"] for row in connection.execute("PRAGMA table_info(model_calls)")}
    assert {"attempt", "ok", "error"} <= names

    row = SqliteModelCallRecorder(connection).recent(round_id="r1")[0]
    assert (row.attempt, row.ok, row.error) == (1, True, None)
    assert row.prompt_text == "老提示词"
    connection.close()

    database.connect(path).close()  # 幂等：再连一次不许炸


def _record_kwargs(**overrides: object) -> dict:  # noqa: ANN205
    """`record()` 的全套参数，省得每条用例抄一遍。"""

    base = {
        "trace_id": "r1",
        "purpose": "claims",
        "model": "m",
        "prompt_sha": "x",
        "prompt_text": "p",
        "reply_text": "{}",
        "usage": {"total_tokens": 1},
        "duration_ms": 1,
        "created_at": NOW,
    }
    base.update(overrides)
    return base


# ── 失败要看得见（P5b）────────────────────────────────────────────────────────
# 旁路（fail soft）是对的、**不改**：它是遥测不是审计，OpenBao/K8s 那种
# "审计失败就让请求失败"的强度用在这里是反的。真正的问题是"静默"——
# 现在 `except (sqlite3.Error, TypeError, ValueError): return` 把两类
# **完全不同的事故**混成了一团，而从外面看都是"没记录"：


def _recorder_on(tmp_path) -> SqliteModelCallRecorder:  # noqa: ANN205
    return SqliteModelCallRecorder(database.connect(tmp_path / "live.db"))


def _take_write_lock(tmp_path):  # noqa: ANN202
    """第二条连接 `BEGIN EXCLUSIVE`，把库真锁住。

    **关掉连接模拟不出"锁"**：那样抛的是 `ProgrammingError(Cannot operate on
    a closed database)`，不是 `OperationalError`——那是另一类事故。"""

    blocker = sqlite3.connect(tmp_path / "live.db", isolation_level=None, timeout=0.05)
    blocker.execute("BEGIN EXCLUSIVE")
    return blocker


def test_留样被锁住时留痕且说清是容量问题(tmp_path, caplog):
    """R：`database is locked` 是**容量问题**（多 worker 写争用），
    和"序列化失败"必须能分开——前者要扩容/减并发，后者要改代码。

    I：连接按掉 → `OperationalError`；P：`record()`；R：有 warning，
    且文案说的是锁/容量。"""

    recorder = _recorder_on(tmp_path)
    blocker = _take_write_lock(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            recorder.record(**_record_kwargs())
    finally:
        blocker.rollback()
        blocker.close()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "留样被吞掉了，一句痕都没留"
    assert any("争用" in r.getMessage() for r in warnings), (
        f"文案没说清是写争用：{[r.getMessage() for r in warnings]}"
    )


def test_序列化失败时留痕且说清是代码问题(tmp_path, caplog):
    """R：另一类事故也要有痕，且措辞要能让人一眼分出是**代码 bug**。"""

    recorder = SqliteModelCallRecorder(database.connect(tmp_path / "m.db"))
    with caplog.at_level(logging.WARNING):
        recorder.record(**_record_kwargs(usage={"bad": object()}))

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "序列化失败也静默吞掉了——真模型踩过一次该 bug"
    assert any("序列化" in r.getMessage() or "json" in r.getMessage().lower()
               for r in warnings), [r.getMessage() for r in warnings]


def test_留样失败仍然不抛_旁路语义不许变(tmp_path):
    """R：**行为不变**——记不上不许把这一轮搞失败。这是仓库明写的规矩，
    本轮只改"看不看得见"，不改"要不要抛"。"""

    recorder = _recorder_on(tmp_path)
    blocker = _take_write_lock(tmp_path)
    try:
        recorder.record(**_record_kwargs())  # 不抛
    finally:
        blocker.rollback()
        blocker.close()


def test_一次故障只留一次痕(tmp_path, caplog):
    """R：每次都留会把日志刷爆——争用期间是**持续**故障，不是几十次独立事件。
    形状照抄 `redis_bus._mark_leg_down`（按状态转换打，不按发生次数打）。"""

    recorder = _recorder_on(tmp_path)
    blocker = _take_write_lock(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                recorder.record(**_record_kwargs())
    finally:
        blocker.rollback()
        blocker.close()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, f"留了 {len(warnings)} 条痕——会刷屏"


def test_恢复正常之后_下次再犯能重新留痕(tmp_path, caplog):
    """R：**恢复要清"已留痕"标志**，否则进程跑几天之后那次偶发丢失就再也看不见了。"""

    # **必须是同一个 recorder**：标志是"这个 worker 里的这个留样器"。
    # 换成另一个对象模拟"恢复"是不成立的——那只是另一个还没留过痕的新对象。
    recorder = _recorder_on(tmp_path)
    with caplog.at_level(logging.WARNING):
        blocker = _take_write_lock(tmp_path)
        try:
            recorder.record(**_record_kwargs())      # 坏 → 留痕
        finally:
            blocker.rollback()
            blocker.close()
        recorder.record(**_record_kwargs())          # 好了 → 清标志

        blocker = _take_write_lock(tmp_path)
        try:
            recorder.record(**_record_kwargs())      # 又坏 → 再留痕
        finally:
            blocker.rollback()
            blocker.close()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2, f"恢复后没清标志，第二次丢失没被看见（共 {len(warnings)} 条）"


def test_没有计数器_多worker下每worker一个会主动误导(tmp_path):
    """R：**刻意不加计数器**。计数住在 recorder 里，而 recorder 每 worker 一个——
    `/api/auth/stats` 只会读到**那一个 worker** 的丢弃数，却被当成全站数字。
    多 worker 下那是**主动误导**，比没有更坏。日志才是真的消费方。"""

    recorder = SqliteModelCallRecorder(database.connect(tmp_path / "m.db"))

    assert not hasattr(recorder, "dropped")
    assert not hasattr(recorder, "dropped_count")
