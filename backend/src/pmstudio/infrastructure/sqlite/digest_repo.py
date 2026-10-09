"""L4 轮次摘要的 SQLite 仓储：CRUD + 最近 N 条。"""

import json
import sqlite3

from pmstudio.domain.memory.digest import RoundDigest


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


class SqliteDigestRepository:
    """L4 轮次摘要的 SQLite 存储。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(self, digest: RoundDigest) -> None:
        self._connection.execute(
            "INSERT INTO round_digests"
            " (id, project_id, round_id, summary, sections, features, outcomes, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET"
            " summary = excluded.summary, sections = excluded.sections,"
            " features = excluded.features, outcomes = excluded.outcomes",
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
