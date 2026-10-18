"""L4 轮次摘要的 SQLite 仓储：**只追加** + 读。

`save` 只插不改（`07 §7.14`）：同一个 round 的摘要写第二次会得到
`DigestAlreadyWritten`，而不是把原来那份覆盖掉。
"""

import json
import sqlite3

from pmstudio.application.errors import DigestAlreadyWritten
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite.owner_scope import require_owner


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


class SqliteDigestRepository:
    """L4 轮次摘要的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, digest: RoundDigest, *, owner: UserId | None = None) -> None:
        """写入一条轮次摘要。**只追加**：同一个 round 写第二次会明确报错。

        不用 `INSERT OR IGNORE`：静默忽略会让"我以为写进去了"和"其实没写"长得一样，
        而这正是本项目不许的那类假装成功。
        """

        resolved = require_owner(owner)
        try:
            self._connection.execute(
                "INSERT INTO round_digests"
                " (id, project_id, owner_id, round_id, summary, sections, features,"
                " outcomes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"digest-{digest.round_id}",
                    digest.project_id,
                    str(resolved),
                    digest.round_id,
                    digest.summary,
                    json.dumps(digest.sections_touched, ensure_ascii=False),
                    json.dumps(digest.features_addressed, ensure_ascii=False),
                    json.dumps(digest.key_outcomes, ensure_ascii=False),
                    _now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise DigestAlreadyWritten(digest.round_id) from error

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[RoundDigest, ...]:
        """最近若干轮摘要（`limit=None` = 全部），按 `created_at` 倒序。

        排序键是 `created_at` 而不是 id：摘要 id 是 `digest-r-<8 位随机 hex>`（生产 id
        生成器就是 `f"{prefix}-{uuid4().hex[:8]}"`），**里面没有时间**，`ORDER BY id DESC`
        给的是随机序——"只带最近 3 轮摘要"会取到任意 3 轮。时间相同的行按 `rowid DESC`
        破平，即按**落库先后**（先写的在前），不按 id 字典序——这一项**取代**了原来的
        `id DESC`：`id` 是 PRIMARY KEY、彼此不同，留在前面就永远压过 `rowid`，破平等于
        没写。

        第一项把无时间的行沉到最后，判据用 `COALESCE(created_at, '') = ''` 而不是
        `created_at IS NULL`：这一列是 `NOT NULL`（`schema.sql` 与 `PRAGMA table_info`
        都确认，无默认值），`IS NULL` 在任何按现形状建的库上恒为假、等于白写；
        而 `''` 是唯一真能出现的"无时间"（这两张表的 `created_at` 不在 `ADDED_COLUMNS` 里，
        老库也是 `NOT NULL`，只有手改库/外部导入才可能留下空串），这条判据照样兜住。

        `limit=None`（默认）= **全部**：调用方（`render_digests`）要拿总数算出
        "只带最近 N 轮"与超水位时的"更早的 N−M 轮待复核"——截断与报数分开，
        截断在渲染层。`LIMIT -1` 即 SQLite 的不限行。传整数则截断后返回。
        """
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM round_digests WHERE project_id = ? AND owner_id = ?"
            " ORDER BY (COALESCE(created_at, '') = '') ASC, created_at DESC,"
            " rowid DESC"
            " LIMIT ?",
            (str(project_id), str(resolved), -1 if limit is None else limit),
        ).fetchall()
        return tuple(self._row_to_digest(row) for row in rows)

    def _row_to_digest(self, row) -> RoundDigest:
        return RoundDigest(
            round_id=row["round_id"],
            project_id=row["project_id"],
            summary=row["summary"],
            sections_touched=tuple(json.loads(row["sections"])),
            features_addressed=tuple(json.loads(row["features"])),
            key_outcomes=tuple(json.loads(row["outcomes"])),
        )
