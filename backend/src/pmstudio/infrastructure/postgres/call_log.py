"""一轮的外部调用轨迹：模型侧与工具侧按时间序并成一份。

**为什么一张 `UNION ALL`**：调用分在两张表（`model_calls` / `tool_calls`），
而「这一轮做了什么」要的是**一条时间线**——分开读再合并就是两个真相源。
这里一次查完，按 `created_at, kind` 排序（`kind` 让同刻的两侧有确定序）。

**模型侧 `error_kind` 恒为 NULL**：模型调用只有 `error` 文本，重算分类要造一个
异常对象（造出来的是假的）→ **不硬凑**（可观测性 v2 §9 取舍）。
"""

from psycopg import Connection

from pmstudio.application.dto import CallEntry


class SqliteRoundCallLog:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def round_calls(self, trace_id: str) -> tuple[CallEntry, ...]:
        rows = self._connection.execute(
            """
            SELECT 'model' AS kind, purpose AS name, duration_ms, ok,
                   NULL AS error_kind, created_at AS at
              FROM model_calls WHERE round_id = %s
            UNION ALL
            SELECT 'tool', tool_id, duration_ms, ok, error_kind, created_at
              FROM tool_calls WHERE round_id = %s
            ORDER BY at, kind
            """,
            (trace_id, trace_id),
        ).fetchall()
        return tuple(
            CallEntry(
                kind=row["kind"],
                name=row["name"],
                duration_ms=int(row["duration_ms"]),
                ok=row["ok"],
                error_kind=row["error_kind"],
                at=row["at"],
            )
            for row in rows
        )
