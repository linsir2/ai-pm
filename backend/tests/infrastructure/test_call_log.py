"""调用轨迹读模型（可观测性 v2）：模型侧与工具侧**按时间序**并成一份。

`UNION ALL` + `ORDER BY at, kind`：分开读再合并就是两个真相源。
**模型侧 `error_kind` 恒为 NULL**——只有 `error` 文本，重算分类要造假异常（不硬凑）。
"""

from pmstudio.infrastructure.sqlite.call_log import SqliteRoundCallLog
from tests.support.postgres import open_direct


def _model_call(connection, *, at: str, purpose: str, ok: bool, duration_ms: int) -> None:
    connection.execute(
        "INSERT INTO model_calls (round_id, purpose, model, prompt_sha, prompt_text,"
        " reply_text, usage_json, duration_ms, created_at, ok) VALUES"
        " ('r1', %s, 'm', 'sha', 'p', 'r', '{}', %s, %s, %s)",
        (purpose, duration_ms, at, ok),
    )


def _tool_call(
    connection, *, at: str, tool_id: str, ok: bool, duration_ms: int, error_kind: str | None
) -> None:
    connection.execute(
        "INSERT INTO tool_calls (round_id, tool_id, arguments, result_note, duration_ms,"
        " created_at, ok, error_kind) VALUES ('r1', %s, '{}', 'x', %s, %s, %s, %s)",
        (tool_id, duration_ms, at, ok, error_kind),
    )


def test_round_calls_merges_model_and_tool_in_time_order():
    connection = open_direct()
    _model_call(
        connection, at="2026-10-23T10:00:00+00:00", purpose="claims", ok=True, duration_ms=10
    )
    _tool_call(
        connection, at="2026-10-23T10:00:01+00:00", tool_id="web_search", ok=False,
        duration_ms=900, error_kind="timeout",
    )
    _model_call(
        connection, at="2026-10-23T10:00:02+00:00", purpose="integration", ok=False, duration_ms=20
    )

    entries = SqliteRoundCallLog(connection).round_calls("r1")

    assert [(entry.kind, entry.name, entry.ok) for entry in entries] == [
        ("model", "claims", True),
        ("tool", "web_search", False),
        ("model", "integration", False),
    ]
    # 模型侧没有分类（不硬凑）；工具侧有
    assert [entry.error_kind for entry in entries] == [None, "timeout", None]
    assert [entry.duration_ms for entry in entries] == [10, 900, 20]


def test_round_calls_ignores_other_rounds():
    connection = open_direct()
    _tool_call(
        connection, at="2026-10-23T10:00:01+00:00", tool_id="web_search", ok=True,
        duration_ms=1, error_kind=None,
    )
    connection.execute(
        "INSERT INTO tool_calls (round_id, tool_id, arguments, result_note, duration_ms,"
        " created_at, ok) VALUES ('r2', 'web_search', '{}', 'x', 1,"
        " '2026-10-23T10:00:02+00:00', TRUE)"
    )

    entries = SqliteRoundCallLog(connection).round_calls("r1")

    assert [entry.kind for entry in entries] == ["tool"]


def test_a_round_with_no_calls_is_empty():
    connection = open_direct()

    assert SqliteRoundCallLog(connection).round_calls("r-none") == ()
