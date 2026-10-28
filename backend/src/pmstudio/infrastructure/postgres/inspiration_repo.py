"""L3 灵感档案的 PostgreSQL 仓储：写入 + 最近 N 条（多用户改造后按 owner 隔离）。

`recent()` 按 `created_at` 倒序而不是 id——灵感 id 里没有时间，按 id 排等于乱排。
`created_at` 是设计里保留的 TEXT + `''` 哨兵列，不换 timestamptz。
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from psycopg import Connection

from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.postgres.owner_scope import require_owner


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SqliteInspirationRepository:
    """L3 灵感档案的 PostgreSQL 存储。"""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def save(self, item: InspirationItem, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            "INSERT INTO inspirations"
            " (id, project_id, owner_id, origin, statement, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT(id) DO UPDATE SET"
            " statement = excluded.statement WHERE inspirations.owner_id = %s",
            (
                item.item_id,
                item.project_id,
                str(resolved),
                item.origin.value,
                item.statement,
                _now(),
                str(resolved),
            ),
        )

    def save_batch(self, items: Sequence[InspirationItem]) -> None:
        for item in items:
            self.save(item)

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[InspirationItem, ...]:
        """最近若干条灵感（`limit=None` = 全部），按 `created_at` 倒序。

        排序键是 `created_at` 而不是 id：灵感 id 是 `ins-<来源>-<卡 id>-<6 位随机 hex>`，
        **里面没有时间**，`ORDER BY id DESC` 等于按卡的序号加随机数排——跨轮谁"最近"
        全看运气。时间相同的行按 `seq DESC` 破平，即按**落库先后**（先收下的在前）。

        第一项把无时间的行沉到最后，判据用 `COALESCE(created_at, '') = ''` 而不是
        `created_at IS NULL`：这一列是 `NOT NULL`，`IS NULL` 在任何按现形状建的库上恒为
        假、等于白写；而 `''` 是唯一真能出现的"无时间"。

        `limit=None`（默认）= **全部**：调用方（`render_inspirations_index`）要拿总数算出
        "共 N 条"与超水位时的"更早的 N−M 条待复核"——截断在渲染层。传整数则在 SQL 里
        截断后返回。
        """
        resolved = require_owner(owner)
        sql = (
            "SELECT * FROM inspirations WHERE project_id = %s AND owner_id = %s"
            " ORDER BY (COALESCE(created_at, '') = '') ASC, created_at DESC,"
            " seq DESC"
        )
        params: tuple[object, ...] = (str(project_id), str(resolved))
        if limit is not None:
            sql += " LIMIT %s"
            params = (*params, limit)
        rows = self._connection.execute(sql, params).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def _row_to_item(self, row: dict) -> InspirationItem:
        return InspirationItem(
            item_id=row["id"],
            project_id=row["project_id"],
            origin=InspirationOrigin(row["origin"]),
            statement=row["statement"],
        )
