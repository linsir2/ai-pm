"""L3 灵感档案的 SQLite 仓储：CRUD + LIKE 搜索 + 最近 N 条。"""

import sqlite3
from collections.abc import Sequence

from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


class SqliteInspirationRepository:
    """L3 灵感档案的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, item: InspirationItem) -> None:
        self._connection.execute(
            "INSERT INTO inspirations"
            " (id, project_id, origin, statement, context, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " statement = excluded.statement, context = excluded.context",
            (
                item.item_id,
                item.project_id,
                item.origin.value,
                item.statement,
                item.context,
                _now(),
            ),
        )

    def save_batch(self, items: Sequence[InspirationItem]) -> None:
        for item in items:
            self.save(item)

    def get(self, item_id: str) -> InspirationItem | None:
        row = self._connection.execute(
            "SELECT * FROM inspirations WHERE id = ?", (item_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_item(row)

    def search(self, project_id: str, query: str, limit: int = 10) -> tuple[InspirationItem, ...]:
        """LIKE 搜索 statement + context（支持中文）。"""
        if not query.strip():
            return ()
        pattern = f"%{query}%"
        rows = self._connection.execute(
            "SELECT * FROM inspirations WHERE project_id = ?"
            " AND (statement LIKE ? OR context LIKE ?)"
            " ORDER BY id DESC LIMIT ?",
            (str(project_id), pattern, pattern, limit),
        ).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def recent(self, project_id: str, limit: int = 5) -> tuple[InspirationItem, ...]:
        rows = self._connection.execute(
            "SELECT * FROM inspirations WHERE project_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (str(project_id), limit),
        ).fetchall()
        return tuple(self._row_to_item(row) for row in rows)

    def _row_to_item(self, row) -> InspirationItem:
        return InspirationItem(
            item_id=row["id"],
            project_id=row["project_id"],
            origin=InspirationOrigin(row["origin"]),
            statement=row["statement"],
            context=row["context"] or None,
        )
