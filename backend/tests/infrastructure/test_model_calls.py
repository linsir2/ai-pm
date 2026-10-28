"""模型留样（`02` §5.3）：出问题时能复现（P4 的 `O1` 就是靠重放才定性的）。

它**不参与业务**：写不进去也不许影响这一轮（记录是旁路），所以 recorder 出错只吞不抛。
提示词存**原文**（截断）——只存 sha 复现不了，而复现是这张表唯一的目的。
"""

import logging

from pmstudio.harness.model.prompt_log import PROMPT_LIMIT
from pmstudio.infrastructure.postgres.model_calls import SqliteModelCallRecorder
from tests.support.builders import NOW
from tests.support.postgres import open_direct


def test_recorder_writes_and_reads_back():
    connection = open_direct()
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
    assert rows[0].ok is True


def test_recorder_filters_by_round_and_keeps_the_newest_first():
    connection = open_direct()
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


def test_recorder_truncates_a_huge_prompt():
    connection = open_direct()
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
    assert text.startswith("很长")
    assert text.endswith("很长")
    assert "略去" in text
    assert len(text) <= PROMPT_LIMIT + 100


def test_recorder_survives_a_broken_write():
    """旁路就是旁路：记录失败不许把这一轮搞失败。"""

    connection = open_direct()
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


def test_recorder_survives_usage_that_cannot_be_serialized():
    """usage 里混进不可序列化的对象时只吞掉，不许往上炸（真模型踩过）。"""

    connection = open_direct()
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


def test_the_new_attempt_columns_roundtrip():
    """每次尝试一条留样：哪个尝试、成没成、失败原因（报告 §四建议 ①）。"""

    connection = open_direct()
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


def _recorder_on():  # noqa: ANN202
    connection = open_direct()
    connection.execute("SET lock_timeout = '50ms'")
    return SqliteModelCallRecorder(connection)


def _lock_table():
    """第二条连接 `LOCK TABLE ... ACCESS EXCLUSIVE`，让留样写等锁超时（55P03）。"""

    blocker = open_direct()
    blocker.execute("BEGIN")
    blocker.execute("LOCK TABLE model_calls IN ACCESS EXCLUSIVE MODE")
    return blocker


def test_留样被锁住时留痕且说清是容量问题(caplog):
    """R：锁超时（55P03）是**容量问题**（多 worker 写争用），
    和"序列化失败"必须能分开——前者要扩容/减并发，后者要改代码。"""

    recorder = _recorder_on()
    blocker = _lock_table()
    try:
        with caplog.at_level(logging.WARNING):
            recorder.record(**_record_kwargs())
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "留样被吞掉了，一句痕都没留"
    assert any("争用" in r.getMessage() for r in warnings), (
        f"文案没说清是写争用：{[r.getMessage() for r in warnings]}"
    )


def test_序列化失败时留痕且说清是代码问题(caplog):
    """R：另一类事故也要有痕，且措辞要能让人一眼分出是**代码 bug**。"""

    recorder = SqliteModelCallRecorder(open_direct())
    with caplog.at_level(logging.WARNING):
        recorder.record(**_record_kwargs(usage={"bad": object()}))

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "序列化失败也静默吞掉了——真模型踩过一次该 bug"
    assert any("序列化" in r.getMessage() or "json" in r.getMessage().lower()
               for r in warnings), [r.getMessage() for r in warnings]


def test_留样失败仍然不抛_旁路语义不许变():
    """R：**行为不变**——记不上不许把这一轮搞失败。"""

    recorder = _recorder_on()
    blocker = _lock_table()
    try:
        recorder.record(**_record_kwargs())  # 不抛
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_一次故障只留一次痕(caplog):
    """R：每次都留会把日志刷爆——争用期间是**持续**故障，不是几十次独立事件。"""

    recorder = _recorder_on()
    blocker = _lock_table()
    try:
        with caplog.at_level(logging.WARNING):
            for _ in range(20):
                recorder.record(**_record_kwargs())
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, f"留了 {len(warnings)} 条痕——会刷屏"


def test_恢复正常之后_下次再犯能重新留痕(caplog):
    """R：**恢复要清"已留痕"标志**，否则进程跑几天之后那次偶发丢失就再也看不见了。"""

    recorder = _recorder_on()
    with caplog.at_level(logging.WARNING):
        blocker = _lock_table()
        try:
            recorder.record(**_record_kwargs())      # 坏 → 留痕
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        recorder.record(**_record_kwargs())          # 好了 → 清标志

        blocker = _lock_table()
        try:
            recorder.record(**_record_kwargs())      # 又坏 → 再留痕
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 2, f"恢复后没清标志，第二次丢失没被看见（共 {len(warnings)} 条）"


def test_没有计数器_多worker下每worker一个会主动误导():
    """R：**刻意不加计数器**。计数住在 recorder 里，而 recorder 每 worker 一个——
    `/api/auth/stats` 只会读到**那一个 worker** 的丢弃数，却被当成全站数字。
    多 worker 下那是**主动误导**，比没有更坏。日志才是真的消费方。"""

    recorder = SqliteModelCallRecorder(open_direct())

    assert not hasattr(recorder, "dropped")
    assert not hasattr(recorder, "dropped_count")
