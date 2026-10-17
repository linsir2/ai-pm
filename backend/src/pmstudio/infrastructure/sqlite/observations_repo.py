"""L2 观察的 SQLite 仓储：CRUD + LIKE 搜索 + 去重（多用户改造后按 owner 隔离）。"""

import sqlite3
from collections.abc import Sequence

from pmstudio.domain.memory.observations import (
    ObservationType,
    RoundObservation,
)
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite.owner_scope import require_owner


class SqliteObservationRepository:
    """L2 观察的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(
        self, observation: RoundObservation, *, owner: UserId | None = None
    ) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            "INSERT INTO observations"
            " (id, project_id, owner_id, round_id, obs_type, title, detail, source_ref)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " title = excluded.title, detail = excluded.detail WHERE owner_id = ?",
            (
                observation.obs_id,
                observation.project_id,
                str(resolved),
                observation.round_id,
                observation.obs_type.value,
                observation.title,
                observation.detail,
                observation.source_ref,
                str(resolved),
            ),
        )

    def save_batch(self, observations: Sequence[RoundObservation]) -> None:
        for obs in observations:
            self.save(obs)

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[RoundObservation, ...]:
        """最新观察，同 `source_ref` 只保留最新一条（id 最大）。

        `limit=None`（默认）= **全部**去重后的：本方法本就全表读进来再去重，给全部不增加 IO，
        而调用方（观察索引）要拿它算出"共 N 条、只带了前 5 条"——截断与报数分开，
        截断在渲染层（`render_observations_index`）。传整数则截断后返回。
        """
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM observations WHERE project_id = ? AND owner_id = ?"
            " ORDER BY id DESC",
            (str(project_id), str(resolved)),
        ).fetchall()
        # 同 source_ref 去重：有序遍历，第一次遇到即最新
        seen: dict[str, sqlite3.Row] = {}
        for row in rows:
            ref = row["source_ref"]
            if ref not in seen:
                seen[ref] = row
        ordered = sorted(seen.values(), key=lambda r: r["id"], reverse=True)
        if limit is not None:
            ordered = ordered[:limit]
        return tuple(self._row_to_obs(row) for row in ordered)

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
