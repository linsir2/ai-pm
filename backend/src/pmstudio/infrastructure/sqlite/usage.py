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


class UserActivityRow:
    """一个用户的活动读模型（管理后台那一屏用；`06` §2.4）。"""

    __slots__ = (
        "user_id",
        "projects",
        "rounds",
        "calls",
        "failed",
        "avg_duration_ms",
        "total_tokens",
        "active_sessions",
        "last_seen_at",
        "created_at",
        "approved_at",
        "approved_by",
        "status",
        "role",
    )

    def __init__(self, **kwargs: object) -> None:
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))


class SqliteAdminStatsRepository:
    """每个用户一行的统计：`/api/auth/stats` 的数据源。

    **为什么全用相关子查询、绝不 join 三张表**：`rounds` / `model_calls` / `sessions`
    都挂在同一个 owner 上，一次 join 会把行数乘起来——183 条调用配 19 个 round 就变成
    3477 行，"调用数"直接放大十几倍。相关子查询各自独立求值，没有笛卡尔积。
    `test_stats_counts_failures_and_tokens_without_fanout` 钉死这件事。

    口径：
    - `projects` / `rounds`：owner 直连计数；
    - `calls` / `failed`：只算 `round_id` 能落到某条 `rounds` 行上的调用
      （探索的 `x-*` 与 `NULL` 自然落不进 join，不计入任何用户）；
    - `total_tokens`：`usage_json.total_tokens` 求和，`usage_json` 为空/NULL 记 0；
    - `active_sessions`：`revoked_at IS NULL AND expires_at > now`；
    - `last_seen_at`：该用户**所有**会话（含已吊销）的 `max(last_seen_at)`——
      吊销的会话说明"他来过又走了"，那也是活跃痕迹。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def rows(self, *, now: str) -> list[UserActivityRow]:
        raw = self._connection.execute(
            """
            SELECT u.user_id, u.status, u.role, u.created_at,
                   u.approved_at, u.approved_by,
                   (SELECT COUNT(*) FROM documents d WHERE d.owner_id = u.user_id) AS projects,
                   (SELECT COUNT(*) FROM rounds r     WHERE r.owner_id = u.user_id) AS rounds,
                   (SELECT COUNT(*) FROM model_calls mc
                      JOIN rounds r ON r.round_id = mc.round_id
                     WHERE r.owner_id = u.user_id) AS calls,
                   (SELECT COUNT(*) FROM model_calls mc
                      JOIN rounds r ON r.round_id = mc.round_id
                     WHERE r.owner_id = u.user_id AND mc.ok = 0) AS failed,
                   (SELECT AVG(mc.duration_ms) FROM model_calls mc
                      JOIN rounds r ON r.round_id = mc.round_id
                     WHERE r.owner_id = u.user_id) AS avg_duration_ms,
                   (SELECT SUM(json_extract(mc.usage_json, '$.total_tokens'))
                      FROM model_calls mc
                      JOIN rounds r ON r.round_id = mc.round_id
                     WHERE r.owner_id = u.user_id) AS total_tokens,
                   (SELECT COUNT(*) FROM sessions s
                     WHERE s.user_id = u.user_id
                       AND s.revoked_at IS NULL AND s.expires_at > ?) AS active_sessions,
                   (SELECT MAX(s.last_seen_at) FROM sessions s
                     WHERE s.user_id = u.user_id) AS last_seen_at
            FROM users u
            ORDER BY u.created_at ASC
            """,
            (now,),
        ).fetchall()
        return [
            UserActivityRow(
                user_id=row["user_id"],
                status=row["status"],
                role=row["role"],
                created_at=row["created_at"],
                approved_at=row["approved_at"],
                approved_by=row["approved_by"],
                projects=int(row["projects"]),
                rounds=int(row["rounds"]),
                calls=int(row["calls"]),
                failed=int(row["failed"]),
                avg_duration_ms=(
                    round(float(row["avg_duration_ms"]))
                    if row["avg_duration_ms"] is not None
                    else None
                ),
                total_tokens=(
                    int(row["total_tokens"]) if row["total_tokens"] is not None else 0
                ),
                active_sessions=int(row["active_sessions"]),
                last_seen_at=row["last_seen_at"],
            )
            for row in raw
        ]
