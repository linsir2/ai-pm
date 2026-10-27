"""L4 轮次摘要的 PostgreSQL 仓储：**只追加** + 读。

`save` 只插不改（`07 §7.14`）：同一个 round 的摘要写第二次会得到
`DigestAlreadyWritten`，而不是把原来那份覆盖掉。

`created_at` 是设计里保留的 TEXT + `''` 哨兵列，不换 timestamptz。
"""

from datetime import UTC, datetime

from psycopg import Connection
from psycopg.errors import UniqueViolation

from pmstudio.application.errors import DigestAlreadyWritten
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite.owner_scope import require_owner


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SqliteDigestRepository:
    """L4 轮次摘要的 PostgreSQL 存储。"""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def save(self, digest: RoundDigest, *, owner: UserId | None = None) -> None:
        """写入一条轮次摘要。**只追加**：同一个 round 写第二次会明确报错。

        不用 `ON CONFLICT DO NOTHING`：静默忽略会让"我以为写进去了"和"其实没写"长得一样，
        而这正是本项目不许的那类假装成功。
        """

        resolved = require_owner(owner)
        try:
            self._connection.execute(
                "INSERT INTO round_digests"
                " (id, project_id, owner_id, round_id, summary, sections, features,"
                " outcomes, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"digest-{digest.round_id}",
                    digest.project_id,
                    str(resolved),
                    digest.round_id,
                    digest.summary,
                    list(digest.sections_touched),
                    list(digest.features_addressed),
                    list(digest.key_outcomes),
                    _now(),
                ),
            )
        except UniqueViolation as error:
            raise DigestAlreadyWritten(digest.round_id) from error

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[RoundDigest, ...]:
        """最近若干轮摘要（`limit=None` = 全部），按 `created_at` 倒序。

        排序键是 `created_at` 而不是 id：摘要 id 是 `digest-r-<8 位随机 hex>`（生产 id
        生成器就是 `f"{prefix}-{uuid4().hex[:8]}"`），**里面没有时间**，`ORDER BY id DESC`
        给的是随机序——"只带最近 3 轮摘要"会取到任意 3 轮。时间相同的行按 `seq DESC`
        破平，即按**落库先后**（先写的在前）。

        第一项把无时间的行沉到最后，判据用 `COALESCE(created_at, '') = ''` 而不是
        `created_at IS NULL`：这一列是 `NOT NULL`，`IS NULL` 在任何按现形状建的库上恒为
        假、等于白写；而 `''` 是唯一真能出现的"无时间"。

        `limit=None`（默认）= **全部**：调用方（`render_digests`）要拿总数算出
        "只带最近 N 轮"与超水位时的"更早的 N−M 轮待复核"——截断与报数分开，
        截断在渲染层。传整数则在 SQL 里截断后返回。
        """
        resolved = require_owner(owner)
        sql = (
            "SELECT * FROM round_digests WHERE project_id = %s AND owner_id = %s"
            " ORDER BY (COALESCE(created_at, '') = '') ASC, created_at DESC,"
            " seq DESC"
        )
        params: tuple[object, ...] = (str(project_id), str(resolved))
        if limit is not None:
            sql += " LIMIT %s"
            params = (*params, limit)
        rows = self._connection.execute(sql, params).fetchall()
        return tuple(self._row_to_digest(row) for row in rows)

    def _row_to_digest(self, row: dict) -> RoundDigest:
        return RoundDigest(
            round_id=row["round_id"],
            project_id=row["project_id"],
            summary=row["summary"],
            sections_touched=tuple(row["sections"]),
            features_addressed=tuple(row["features"]),
            key_outcomes=tuple(row["outcomes"]),
        )
