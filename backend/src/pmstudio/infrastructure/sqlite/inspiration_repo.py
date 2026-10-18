"""L3 灵感档案的 SQLite 仓储：写入 + 最近 N 条（多用户改造后按 owner 隔离）。

`recent()` 按 `created_at` 倒序而不是 id——灵感 id 里没有时间，按 id 排等于乱排。
"""

import sqlite3
from collections.abc import Sequence

from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite.owner_scope import require_owner


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


class SqliteInspirationRepository:
    """L3 灵感档案的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, item: InspirationItem, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            "INSERT INTO inspirations"
            " (id, project_id, owner_id, origin, statement, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " statement = excluded.statement WHERE owner_id = ?",
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
        全看运气。时间相同的行（同一次落库、秒级 `_now()`）按 `rowid DESC` 破平，即按
        **落库先后**（先收下的在前），不按 id 字典序——这一项**取代**了原来的 `id DESC`：
        `id` 是 PRIMARY KEY、彼此不同，留在前面就永远压过 `rowid`，破平等于没写。

        第一项把无时间的行沉到最后，判据用 `COALESCE(created_at, '') = ''` 而不是
        `created_at IS NULL`：这一列是 `NOT NULL`（`schema.sql` 与 `PRAGMA table_info`
        都确认，无默认值），`IS NULL` 在任何按现形状建的库上恒为假、等于白写；
        而 `''` 是唯一真能出现的"无时间"（这两张表的 `created_at` 不在 `ADDED_COLUMNS` 里，
        老库也是 `NOT NULL`，只有手改库/外部导入才可能留下空串），这条判据照样兜住。

        `limit=None`（默认）= **全部**：调用方（`render_inspirations_index`）要拿总数算出
        "共 N 条"与超水位时的"更早的 N−M 条待复核"——截断在渲染层。`LIMIT -1` 即
        SQLite 的不限行。
        """
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM inspirations WHERE project_id = ? AND owner_id = ?"
            " ORDER BY (COALESCE(created_at, '') = '') ASC, created_at DESC,"
            " rowid DESC"
            " LIMIT ?",
            (str(project_id), str(resolved), -1 if limit is None else limit),
        ).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def _row_to_item(self, row) -> InspirationItem:
        return InspirationItem(
            item_id=row["id"],
            project_id=row["project_id"],
            origin=InspirationOrigin(row["origin"]),
            statement=row["statement"],
        )
