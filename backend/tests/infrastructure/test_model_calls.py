"""模型留样（`02` §5.3）：出问题时能复现（P4 的 `O1` 就是靠重放才定性的）。

它**不参与业务**：写不进去也不许影响这一轮（记录是旁路），所以 recorder 出错只吞不抛。
提示词存**原文**（截断）——只存 sha 复现不了，而复现是这张表唯一的目的。
"""

import sqlite3

from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.model_calls import (
    PROMPT_LIMIT,
    SqliteModelCallRecorder,
)
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
        round_id="r1",
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
    for index, round_id in enumerate(("r1", "r2", "r1"), start=1):
        recorder.record(
            round_id=round_id,
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
        round_id="r1",
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
        round_id="r1",
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
        round_id="r1",
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
        round_id="r1",
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
