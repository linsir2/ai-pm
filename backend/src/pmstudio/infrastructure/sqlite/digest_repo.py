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

    def get(self, round_id: str, *, owner: UserId | None = None) -> RoundDigest | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM round_digests WHERE id = ? AND owner_id = ?",
            (f"digest-{round_id}", str(resolved)),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_digest(row)

    def recent(
        self, project_id: str, limit: int | None = None, *, owner: UserId | None = None
    ) -> tuple[RoundDigest, ...]:
        """最近若干轮摘要（`limit=None` = 全部）。

        默认给全部，与 L2 `SqliteObservationRepository.recent` 同一口径：调用方
        （`render_digests`）要拿总数算出"只带最近 N 轮"与超水位时的"更早的 N−M 轮
        待复核"——截断与报数分开，截断在渲染层。`LIMIT -1` 即 SQLite 的不限行。
        传整数则截断后返回。
        """
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM round_digests WHERE project_id = ? AND owner_id = ?"
            " ORDER BY id DESC LIMIT ?",
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
