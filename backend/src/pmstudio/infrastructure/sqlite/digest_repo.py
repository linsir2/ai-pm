"""L4 轮次摘要的 SQLite 仓储：**只追加** + 读。

`save` 只插不改（`07 §7.14`）：同一个 round 的摘要写第二次会得到
`DigestAlreadyWritten`，而不是把原来那份覆盖掉。
"""

import json
import sqlite3

from pmstudio.application.errors import DigestAlreadyWritten
from pmstudio.domain.memory.digest import RoundDigest


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


class SqliteDigestRepository:
    """L4 轮次摘要的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, digest: RoundDigest) -> None:
        """写入一条轮次摘要。**只追加**：同一个 round 写第二次会明确报错。

        不用 `INSERT OR IGNORE`：静默忽略会让"我以为写进去了"和"其实没写"长得一样，
        而这正是本项目不许的那类假装成功。
        """

        try:
            self._connection.execute(
                "INSERT INTO round_digests"
                " (id, project_id, round_id, summary, sections, features, outcomes, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"digest-{digest.round_id}",
                    digest.project_id,
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

    def get(self, round_id: str) -> RoundDigest | None:
        row = self._connection.execute(
            "SELECT * FROM round_digests WHERE id = ?", (f"digest-{round_id}",)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_digest(row)

    def recent(self, project_id: str, limit: int = 3) -> tuple[RoundDigest, ...]:
        rows = self._connection.execute(
            "SELECT * FROM round_digests WHERE project_id = ?"
            " ORDER BY id DESC LIMIT ?",
            (str(project_id), limit),
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
