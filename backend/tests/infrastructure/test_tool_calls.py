"""工具调用留样（可观测性 v2）：与 `model_calls` 同一条纪律，但覆盖**工具侧**。

三条口径：
- **成功也记**——只记失败答不出「这个工具慢不慢」（可观测性 v2 §0）；
- 写不进去不许影响这一轮（旁路，与模型留样逐字同款）；
- **没有 `recent()`**——读侧跨两张表 `UNION ALL`，用不上单表读方法（留一个零生产
  消费者的读方法正是本仓明令反对的「加了没人用」，可观测性 v2 §3）。
"""

import inspect
import logging
import sqlite3

from pmstudio.harness.tools.recorder import ToolCallRecorder
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.tool_calls import SqliteToolCallRecorder
from tests.support.builders import NOW


def test_the_tool_call_recorder_signature_is_deliberate():
    """守卫：`record` 的参数集钉死——加参数必须是一次自觉的改动。

    照 `tests/harness/test_registry.py::test_the_field_set_is_deliberate` 的手法
    （用 `inspect.signature` 钉参数集）。
    """

    parameters = set(inspect.signature(ToolCallRecorder.record).parameters)
    assert parameters == {
        "self",
        "trace_id",
        "tool_id",
        "arguments",
        "result_note",
        "duration_ms",
        "created_at",
        "ok",
        "error",
        "error_kind",
    }


def test_the_sqlite_recorder_satisfies_the_protocol(tmp_path):
    """`SqliteToolCallRecorder` 满足 `ToolCallRecorder` 协议——换实现不用改调用方。"""

    recorder = SqliteToolCallRecorder(database.connect(tmp_path / "pm.db"))

    assert isinstance(recorder, ToolCallRecorder)


def test_recorder_writes_a_row(tmp_path):
    connection = database.connect(tmp_path / "pm.db")
    recorder = SqliteToolCallRecorder(connection)

    recorder.record(
        trace_id="r1",
        tool_id="web_search",
        arguments={"query": "RBAC", "limit": 5},
        result_note="12 条",
        duration_ms=812,
        created_at=NOW,
        ok=False,
        error="Timeout: boom",
        error_kind="timeout",
    )

    row = connection.execute(
        "SELECT round_id, tool_id, arguments, result_note, duration_ms, ok,"
        " error, error_kind FROM tool_calls WHERE round_id = 'r1'"
    ).fetchone()
    assert row["tool_id"] == "web_search"
    assert row["arguments"] == '{"query": "RBAC", "limit": 5}'
    assert row["result_note"] == "12 条"
    assert row["duration_ms"] == 812
    assert row["ok"] == 0
    assert row["error"] == "Timeout: boom"
    assert row["error_kind"] == "timeout"


def test_schema_creates_the_table_on_an_old_db(tmp_path):
    """老库不用迁移：`connect()` 每次都跑 `schema.sql`（全是 CREATE TABLE IF NOT EXISTS）。"""

    path = tmp_path / "old.db"
    database.connect(path).close()
    connection = database.connect(path)
    names = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }

    assert "tool_calls" in names


def test_user_version_is_16(tmp_path):
    """`tool_calls` 是 v16 引入的（`CREATE TABLE IF NOT EXISTS` 自建，不动 ADDED_COLUMNS）。"""

    connection = database.connect(tmp_path / "pm.db")

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 16


def test_recorder_survives_a_broken_write(tmp_path):
    """旁路就是旁路：记录失败不许把这一轮搞失败。"""

    connection = database.connect(tmp_path / "pm.db")
    connection.close()  # 连接按掉：写入必然失败
    recorder = SqliteToolCallRecorder(connection)

    recorder.record(**_record_kwargs())  # 不抛


def test_recorder_survives_arguments_that_cannot_be_serialized(tmp_path):
    """参数里混进不可序列化的对象时只吞掉，不许往上炸（与模型留样同一条分支）。"""

    recorder = SqliteToolCallRecorder(database.connect(tmp_path / "pm.db"))

    recorder.record(**_record_kwargs(arguments={"bad": object()}))  # 不抛


def _record_kwargs(**overrides: object) -> dict:  # noqa: ANN205
    """`record()` 的全套参数，省得每条用例抄一遍。"""

    base = {
        "trace_id": "r1",
        "tool_id": "web_search",
        "arguments": {"query": "RBAC"},
        "result_note": "12 条",
        "duration_ms": 812,
        "created_at": NOW,
    }
    base.update(overrides)
    return base


def _take_write_lock(tmp_path):  # noqa: ANN202
    """第二条连接 `BEGIN EXCLUSIVE`，把库真锁住（照抄 `test_model_calls.py`）。"""

    blocker = sqlite3.connect(tmp_path / "live.db", isolation_level=None, timeout=0.05)
    blocker.execute("BEGIN EXCLUSIVE")
    return blocker


def _tool_warnings(caplog):  # noqa: ANN202
    """只看**本模块**的告警。

    **必须按 logger 名过滤**：`database.connect()` 在向量扩展载不了时会打一条 warning
    （`sqlite-vec 载入失败`），它与留样无关却会被 `caplog` 一起收进来——不过滤的话
    「一次故障只留一次痕」会数出多余的条数（`test_model_calls.py` 里那两条就是踩了这个）。
    """

    return [
        record
        for record in caplog.records
        if record.name == "pmstudio.infrastructure.sqlite.tool_calls"
    ]


def test_the_recorder_warns_once_per_state_transition(tmp_path, caplog):
    """R：每次都留会把日志刷爆——争用期间是**持续**故障（按状态转换打，不按次数）。"""

    recorder = SqliteToolCallRecorder(database.connect(tmp_path / "live.db"))
    blocker = _take_write_lock(tmp_path)
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                recorder.record(**_record_kwargs())
    finally:
        blocker.rollback()
        blocker.close()

    warnings = _tool_warnings(caplog)
    assert len(warnings) == 1, f"留了 {len(warnings)} 条痕——会刷屏"


def test_a_successful_write_clears_the_warned_flag(tmp_path, caplog):
    """R：**恢复要清标志**——否则进程跑几天之后那次偶发丢失就再也看不见了。

    **必须是同一个 recorder**：标志是「这个 worker 里的这个留样器」。
    """

    recorder = SqliteToolCallRecorder(database.connect(tmp_path / "live.db"))
    with caplog.at_level(logging.WARNING):
        blocker = _take_write_lock(tmp_path)
        try:
            recorder.record(**_record_kwargs())  # 坏 → 留痕
        finally:
            blocker.rollback()
            blocker.close()
        recorder.record(**_record_kwargs())  # 好了 → 清标志

        blocker = _take_write_lock(tmp_path)
        try:
            recorder.record(**_record_kwargs())  # 又坏 → 再留痕
        finally:
            blocker.rollback()
            blocker.close()

    warnings = _tool_warnings(caplog)
    assert len(warnings) == 2, f"恢复后没清标志，第二次丢失没被看见（共 {len(warnings)} 条）"


def test_the_recorder_has_no_read_method(tmp_path):
    """R：**刻意没有 `recent()`**——读侧跨两张表 UNION，用不上它；
    留一个零生产消费者的读方法正是本仓明令反对的「加了没人用」。"""

    recorder = SqliteToolCallRecorder(database.connect(tmp_path / "m.db"))

    assert not hasattr(recorder, "recent")
