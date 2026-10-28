"""工具调用留样（可观测性 v2）：与 `model_calls` 同一条纪律，但覆盖**工具侧**。

三条口径：
- **成功也记**——只记失败答不出「这个工具慢不慢」（可观测性 v2 §0）；
- 写不进去不许影响这一轮（旁路，与模型留样逐字同款）；
- **没有 `recent()`**——读侧跨两张表 `UNION ALL`，用不上单表读方法。
"""

import inspect
import logging

from pmstudio.harness.tools.recorder import ToolCallRecorder
from pmstudio.infrastructure.postgres.tool_calls import SqliteToolCallRecorder
from tests.support.builders import NOW
from tests.support.postgres import open_direct


def test_the_tool_call_recorder_signature_is_deliberate():
    """守卫：`record` 的参数集钉死——加参数必须是一次自觉的改动。"""

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


def test_the_postgres_recorder_satisfies_the_protocol():
    """`SqliteToolCallRecorder` 满足 `ToolCallRecorder` 协议——换实现不用改调用方。"""

    recorder = SqliteToolCallRecorder(open_direct())

    assert isinstance(recorder, ToolCallRecorder)


def test_recorder_writes_a_row():
    connection = open_direct()
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
    assert row["arguments"] == {"query": "RBAC", "limit": 5}
    assert row["result_note"] == "12 条"
    assert row["duration_ms"] == 812
    assert row["ok"] is False
    assert row["error"] == "Timeout: boom"
    assert row["error_kind"] == "timeout"


def test_recorder_survives_a_broken_write():
    """旁路就是旁路：记录失败不许把这一轮搞失败。"""

    connection = open_direct()
    connection.close()  # 连接按掉：写入必然失败
    recorder = SqliteToolCallRecorder(connection)

    recorder.record(**_record_kwargs())  # 不抛


def test_recorder_survives_arguments_that_cannot_be_serialized():
    """参数里混进不可序列化的对象时只吞掉，不许往上炸（与模型留样同一条分支）。"""

    recorder = SqliteToolCallRecorder(open_direct())

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


def _recorder_on():  # noqa: ANN202
    connection = open_direct()
    connection.execute("SET lock_timeout = '50ms'")
    return SqliteToolCallRecorder(connection)


def _lock_table():
    """第二条连接 `LOCK TABLE ... ACCESS EXCLUSIVE`，让留样写等锁超时（55P03）。"""

    blocker = open_direct()
    blocker.execute("BEGIN")
    blocker.execute("LOCK TABLE tool_calls IN ACCESS EXCLUSIVE MODE")
    return blocker


def _tool_warnings(caplog):  # noqa: ANN202
    """只看**本模块**的告警（caplog 会收别的 logger）。"""

    return [
        record
        for record in caplog.records
        if record.name == "pmstudio.infrastructure.postgres.tool_calls"
    ]


def test_the_recorder_warns_once_per_state_transition(caplog):
    """R：每次都留会把日志刷爆——争用期间是**持续**故障（按状态转换打，不按次数）。"""

    recorder = _recorder_on()
    blocker = _lock_table()
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                recorder.record(**_record_kwargs())
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    warnings = _tool_warnings(caplog)
    assert len(warnings) == 1, f"留了 {len(warnings)} 条痕——会刷屏"


def test_a_successful_write_clears_the_warned_flag(caplog):
    """R：**恢复要清标志**——否则进程跑几天之后那次偶发丢失就再也看不见了。"""

    recorder = _recorder_on()
    with caplog.at_level(logging.WARNING):
        blocker = _lock_table()
        try:
            recorder.record(**_record_kwargs())  # 坏 → 留痕
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        recorder.record(**_record_kwargs())  # 好了 → 清标志

        blocker = _lock_table()
        try:
            recorder.record(**_record_kwargs())  # 又坏 → 再留痕
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

    warnings = _tool_warnings(caplog)
    assert len(warnings) == 2, f"恢复后没清标志，第二次丢失没被看见（共 {len(warnings)} 条）"


def test_the_recorder_has_no_read_method():
    """R：**刻意没有 `recent()`**——读侧跨两张表 UNION，用不上它。"""

    recorder = SqliteToolCallRecorder(open_direct())

    assert not hasattr(recorder, "recent")
