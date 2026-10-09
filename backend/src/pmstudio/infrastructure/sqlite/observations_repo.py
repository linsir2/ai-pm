"""L2 观察的 SQLite 仓储：CRUD + LIKE 搜索 + 去重。"""

import sqlite3
from collections.abc import Sequence

from pmstudio.domain.memory.observations import (
    ObservationType,
    RoundObservation,
)


class SqliteObservationRepository:
    """L2 观察的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, observation: RoundObservation) -> None:
        self._connection.execute(
            "INSERT INTO observations"
            " (id, project_id, round_id, obs_type, title, detail, source_ref)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " title = excluded.title, detail = excluded.detail",
            (
                observation.obs_id,
                observation.project_id,
                observation.round_id,
                observation.obs_type.value,
                observation.title,
                observation.detail,
                observation.source_ref,
            ),
        )

    def save_batch(self, observations: Sequence[RoundObservation]) -> None:
        for obs in observations:
            self.save(obs)

    def get(self, obs_id: str) -> RoundObservation | None:
        row = self._connection.execute(
            "SELECT * FROM observations WHERE id = ?", (obs_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_obs(row)

    def recent(self, project_id: str, limit: int = 5) -> tuple[RoundObservation, ...]:
        """最新观察，同 source_ref 只保留最新一条（id 最大）。"""
        rows = self._connection.execute(
            "SELECT * FROM observations WHERE project_id = ? ORDER BY id DESC",
            (str(project_id),),
        ).fetchall()
        # 同 source_ref 去重：有序遍历，第一次遇到即最新
        seen: dict[str, sqlite3.Row] = {}
        for row in rows:
            ref = row["source_ref"]
            if ref not in seen:
                seen[ref] = row
        deduped = sorted(seen.values(), key=lambda r: r["id"], reverse=True)[:limit]
        return tuple(self._row_to_obs(row) for row in deduped)

    def search(self, project_id: str, query: str, limit: int = 10) -> tuple[RoundObservation, ...]:
        """搜索 title + detail（LIKE，支持中文）。"""
        if not query.strip():
            return ()
        pattern = f"%{query}%"
        rows = self._connection.execute(
            "SELECT * FROM observations WHERE project_id = ?"
            " AND (title LIKE ? OR detail LIKE ?)"
            " ORDER BY id DESC LIMIT ?",
            (str(project_id), pattern, pattern, limit),
        ).fetchall()
        return tuple(self._row_to_obs(row) for row in rows)

    def _row_to_obs(self, row) -> RoundObservation:
        return RoundObservation(
            obs_id=row["id"],
            round_id=row["round_id"],
            project_id=row["project_id"],
            obs_type=ObservationType(row["obs_type"]),
            title=row["title"],
            detail=row["detail"],
            source_ref=row["source_ref"],
        )
