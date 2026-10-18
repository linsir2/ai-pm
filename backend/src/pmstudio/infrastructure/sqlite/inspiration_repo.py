"""L3 灵感档案的 SQLite 仓储：CRUD + LIKE 搜索 + 最近 N 条（多用户改造后按 owner 隔离）。"""

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

    def get(self, item_id: str, *, owner: UserId | None = None) -> InspirationItem | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM inspirations WHERE id = ? AND owner_id = ?",
            (item_id, str(resolved)),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    def search(
        self, project_id: str, query: str, limit: int = 10, *, owner: UserId | None = None
    ) -> tuple[InspirationItem, ...]:
        """LIKE 搜索 statement（支持中文）。"""
        resolved = require_owner(owner)
        if not query.strip():
            return ()
        pattern = f"%{query}%"
        rows = self._connection.execute(
            "SELECT * FROM inspirations WHERE project_id = ? AND owner_id = ?"
            " AND statement LIKE ?"
            " ORDER BY id DESC LIMIT ?",
            (str(project_id), str(resolved), pattern, limit),
        ).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[InspirationItem, ...]:
        """最近若干条灵感（`limit=None` = 全部）。

        默认给全部，与 L2 `SqliteObservationRepository.recent` 同一口径：调用方
        （`render_inspirations_index`）要拿总数算出"共 N 条"与超水位时的
        "更早的 N−M 条待复核"——截断在渲染层。`LIMIT -1` 即 SQLite 的不限行。
        """
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM inspirations WHERE project_id = ? AND owner_id = ?"
            " ORDER BY id DESC LIMIT ?",
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
