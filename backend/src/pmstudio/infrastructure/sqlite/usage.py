"""管理端用量读模型（多用户改造 Task 8 / Ruling 7）。

口径：**按 owner 聚合其项目下轮次的 `model_calls` 行数**。`model_calls` 表**不加 owner 列**
（`§0.3`），所以这里 join 查：`model_calls.round_id → rounds.round_id → rounds.owner_id`。

- 只算 `round_id` 能落到某条 `rounds` 行上的调用（探索的 `x-*` 与 `NULL` 自然落不进 join，
  不计入任何用户）；
- 返回**只有调用数 > 0 的 owner**；没有调用的用户由用例侧补 0（它才是知道"有哪些用户"的人）。
"""

import sqlite3

from pmstudio.domain.shared.ids import UserId


class SqliteUsageRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def count_model_calls_by_owner(self) -> dict[UserId, int]:
        rows = self._connection.execute(
            """
            SELECT r.owner_id AS owner_id, COUNT(mc.id) AS calls
            FROM rounds r
            JOIN model_calls mc ON mc.round_id = r.round_id
            WHERE r.owner_id IS NOT NULL
            GROUP BY r.owner_id
            """
        ).fetchall()
        return {UserId(row["owner_id"]): int(row["calls"]) for row in rows}
