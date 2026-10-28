"""L2 观察的 PostgreSQL 仓储：写入 + `recent()`（按时间去重排序）+ 按 owner 隔离。"""

from collections.abc import Sequence

from psycopg import Connection

from pmstudio.domain.memory.observations import (
    ObservationType,
    RoundObservation,
)
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.postgres.owner_scope import require_owner


class SqliteObservationRepository:
    """L2 观察的 PostgreSQL 存储。"""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def save(
        self, observation: RoundObservation, *, owner: UserId | None = None
    ) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            "INSERT INTO observations"
            " (id, project_id, owner_id, round_id, obs_type, title, detail, source_ref,"
            " created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT(id) DO UPDATE SET"
            " title = excluded.title, detail = excluded.detail WHERE observations.owner_id = %s",
            (
                observation.obs_id,
                observation.project_id,
                str(resolved),
                observation.round_id,
                observation.obs_type.value,
                observation.title,
                observation.detail,
                observation.source_ref,
                observation.created_at,
                str(resolved),
            ),
        )

    def save_batch(self, observations: Sequence[RoundObservation]) -> None:
        for obs in observations:
            self.save(obs)

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[RoundObservation, ...]:
        """最新观察，同 `source_ref` 只保留最新一条（`created_at` 较大者）。

        排序键是 `created_at` 而不是 id：id 里带序号，且**不带时间**——真库那
        `obs-9-792a3a` 与 `obs-10-5cf824` 其实是**同一轮**的两条（`obs-<轮内序号>-<6 位
        随机 hex>`，序号每轮从头数），`obs-9` 排前面只是字典序巧合，跨轮谁"最近"全看
        运气：只按 id 排，去重会挑错那一条。第一项把无时间的行沉到最后，`COALESCE` 是
        必需的：pre-v11 的老库那一列可空，而 `(NULL = '')` 求值为 NULL、NULL 在 ASC 里排
        最前，不 COALESCE 就会把"最该沉底的存量"浮到顶上。迁移已把 NULL 统一填成 `''`，
        这里仍然兜住库被手改出 NULL 的情况。

        末位 `seq DESC`：同一轮的观察按设计共用一个 `round_.created_at`，轮内先后只能靠
        这个破平——`seq` 是落库顺序，与 `_SimpleIdGen` 逐条生成、先抽到的先落库一致。

        `limit=None`（默认）= **全部**去重后的：本方法本就全表读进来再去重，给全部不增加 IO，
        而调用方（观察索引）要拿它算出"共 N 条、只带了前 5 条"——截断与报数分开，
        截断在渲染层（`render_observations_index`）。传整数则截断后返回。
        """
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM observations AS o WHERE project_id = %s AND owner_id = %s"
            " ORDER BY (COALESCE(o.created_at, '') = '') ASC, o.created_at DESC,"
            " o.seq DESC",
            (str(project_id), str(resolved)),
        ).fetchall()
        # 同 source_ref 去重：SQL 已经按时间序返回，有序遍历，第一次遇到即最新
        seen: dict[str, dict] = {}
        for row in rows:
            ref = row["source_ref"]
            if ref not in seen:
                seen[ref] = row
        ordered = list(seen.values())
        if limit is not None:
            ordered = ordered[:limit]
        return tuple(self._row_to_obs(row) for row in ordered)

    def _row_to_obs(self, row: dict) -> RoundObservation:
        return RoundObservation(
            obs_id=row["id"],
            round_id=row["round_id"],
            project_id=row["project_id"],
            obs_type=ObservationType(row["obs_type"]),
            title=row["title"],
            detail=row["detail"],
            source_ref=row["source_ref"],
            created_at=row["created_at"],
        )
