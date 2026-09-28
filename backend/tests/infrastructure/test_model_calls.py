"""模型留样（`02` §5.3）：出问题时能复现（P4 的 `O1` 就是靠重放才定性的）。

它**不参与业务**：写不进去也不许影响这一轮（记录是旁路），所以 recorder 出错只吞不抛。
提示词存**原文**（截断）——只存 sha 复现不了，而复现是这张表唯一的目的。
"""

from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder
from tests.support.builders import NOW


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

    assert len(recorder.recent(round_id="r1")[0].prompt_text) == 20_000


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
