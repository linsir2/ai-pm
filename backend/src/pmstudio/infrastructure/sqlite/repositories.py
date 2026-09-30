"""SQLite 仓储。实现 domain 的六个端口 + application 的 WorkspaceIndex。

注意：这个文件**不许 import `DocumentWriter`**（写入能力白名单只有
`domain/services/committer.py`）。这里的 `save()` 只是同名方法，
结构化满足 `DocumentWriter` 协议由守卫与提交点的类型标注保证。
"""

import json
import sqlite3
from datetime import UTC, datetime

from pmstudio.application.dto import (
    OpinionSourceView,
    ProjectSummaryView,
    RoundHeadView,
    RoundOutcomeView,
    round_head_view,
)
from pmstudio.application.errors import UnknownProject, UnknownRound
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import ProjectId, RoundId
from pmstudio.infrastructure.sqlite import serialization as ser


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteDocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, project_id: ProjectId) -> Document:
        row = self._row(project_id)
        versions = tuple(
            ser.version_from_row(
                {
                    **dict(item),
                    "changed_block_ids": json.loads(item["changed_block_ids"]),
                    "snapshot": json.loads(item["snapshot"]),
                }
            )
            for item in self.version_rows(project_id)
        )
        return ser.document_from_row(
            {
                **dict(row),
                "blocks": json.loads(row["blocks"]),
                "preference_entries": json.loads(row["preference_entries"]),
                "tombstones": json.loads(row["tombstones"]),
            },
            versions,
        )

    def save(self, document: Document) -> None:
        row = ser.document_to_row(document, now=datetime.now(UTC))
        self._connection.execute(
            """
            INSERT INTO documents (project_id, document_id, title, blocks,
                                   preference_entries, tombstones, created_at, updated_at)
            VALUES (:project_id, :document_id, :title, :blocks, :preference_entries,
                    :tombstones, :created_at, :updated_at)
            ON CONFLICT(project_id) DO UPDATE SET
                title = excluded.title,
                blocks = excluded.blocks,
                preference_entries = excluded.preference_entries,
                tombstones = excluded.tombstones,
                updated_at = excluded.updated_at
            """,
            {
                **row,
                "blocks": json.dumps(row["blocks"], ensure_ascii=False),
                "preference_entries": json.dumps(
                    row["preference_entries"], ensure_ascii=False
                ),
                "tombstones": json.dumps(row["tombstones"], ensure_ascii=False),
            },
        )
        for version in document.versions:
            payload = ser.version_to_row(document.project_id, version)
            self._connection.execute(
                """
                INSERT OR IGNORE INTO document_versions
                    (project_id, version_no, trigger, summary, changed_block_ids,
                     snapshot, created_at)
                VALUES (:project_id, :version_no, :trigger, :summary, :changed_block_ids,
                        :snapshot, :created_at)
                """,
                {
                    **payload,
                    "changed_block_ids": json.dumps(
                        payload["changed_block_ids"], ensure_ascii=False
                    ),
                    "snapshot": json.dumps(payload["snapshot"], ensure_ascii=False),
                },
            )

    def version_rows(self, project_id: ProjectId) -> list[sqlite3.Row]:
        return list(
            self._connection.execute(
                "SELECT * FROM document_versions WHERE project_id = ? ORDER BY version_no",
                (str(project_id),),
            )
        )

    def _row(self, project_id: ProjectId) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE project_id = ?", (str(project_id),)
        ).fetchone()
        if row is None:
            raise UnknownProject(project_id)
        return row


class SqliteRoundRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, round_id: RoundId) -> Round:
        row = self._connection.execute(
            "SELECT * FROM rounds WHERE round_id = ?", (str(round_id),)
        ).fetchone()
        if row is None:
            raise UnknownRound(round_id)
        return ser.round_from_payload(
            json.loads(row["payload"]),
            round_id=RoundId(row["round_id"]),
            project_id=ProjectId(row["project_id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            step=Step(row["step"]),
            waiting_on=WaitingOn(row["waiting_on"]),
        )

    def save(self, round_: Round) -> None:
        payload = json.dumps(ser.round_to_payload(round_), ensure_ascii=False)
        self._connection.execute(
            """
            INSERT INTO rounds (round_id, project_id, step, waiting_on, payload,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(round_id) DO UPDATE SET
                step = excluded.step,
                waiting_on = excluded.waiting_on,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                str(round_.round_id),
                str(round_.project_id),
                str(round_.step),
                str(round_.waiting_on),
                payload,
                round_.created_at.isoformat(),
                _now(),
            ),
        )

    def latest_round_id(self, project_id: ProjectId) -> RoundId | None:
        row = self._connection.execute(
            "SELECT round_id FROM rounds WHERE project_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (str(project_id),),
        ).fetchone()
        return None if row is None else RoundId(row["round_id"])


class SqliteEvidenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, project_id: ProjectId) -> EvidenceLedger:
        row = self._connection.execute(
            "SELECT payload FROM evidence_ledgers WHERE project_id = ?", (str(project_id),)
        ).fetchone()
        if row is None:
            raise UnknownProject(project_id)
        return ser.ledger_from_json(project_id, json.loads(row["payload"]))

    def save(self, ledger: EvidenceLedger) -> None:
        self._connection.execute(
            """
            INSERT INTO evidence_ledgers (project_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            """,
            (
                str(ledger.project_id),
                json.dumps(ser.ledger_to_json(ledger), ensure_ascii=False),
                _now(),
            ),
        )


class SqliteWorkspaceIndex:
    """只读查询投影：摘要、列表、最新一轮。不产生第二份真相源。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def summary(self, project_id: ProjectId) -> ProjectSummaryView:
        row = self._connection.execute(
            """
            SELECT d.project_id AS project_id, d.title AS title,
                   MAX(v.version_no) AS version_no, d.updated_at AS updated_at
            FROM documents d
            LEFT JOIN document_versions v ON v.project_id = d.project_id
            WHERE d.project_id = ?
            GROUP BY d.project_id, d.title, d.updated_at
            """,
            (str(project_id),),
        ).fetchone()
        if row is None:
            raise UnknownProject(project_id)
        return ProjectSummaryView(
            project_id=row["project_id"],
            title=row["title"],
            version_no=int(row["version_no"] or 1),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_summaries(self) -> tuple[ProjectSummaryView, ...]:
        rows = self._connection.execute(
            """
            SELECT d.project_id AS project_id, d.title AS title,
                   MAX(v.version_no) AS version_no, d.updated_at AS updated_at
            FROM documents d
            LEFT JOIN document_versions v ON v.project_id = d.project_id
            GROUP BY d.project_id, d.title, d.updated_at
            ORDER BY d.updated_at DESC
            """
        ).fetchall()
        return tuple(
            ProjectSummaryView(
                project_id=row["project_id"],
                title=row["title"],
                version_no=int(row["version_no"] or 1),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        )

    def latest_round_id(self, project_id: ProjectId) -> RoundId | None:
        return SqliteRoundRepository(self._connection).latest_round_id(project_id)

    def recent_rounds(
        self, project_id: ProjectId, *, limit: int = 5
    ) -> tuple[RoundOutcomeView, ...]:
        """最近几轮的**结果投影**（C4：连续几轮没采用席位提议）。

        只读 payload 里那几个键——查历史不该把整轮重建出来。倒序排 `created_at`，
        同刻的按 `round_id` 倒序（测试里两轮常在同一时刻）。
        """

        rows = self._connection.execute(
            """
            SELECT round_id, step, payload
            FROM rounds
            WHERE project_id = ?
            ORDER BY created_at DESC, round_id DESC
            LIMIT ?
            """,
            (str(project_id), limit),
        ).fetchall()
        outcomes: list[RoundOutcomeView] = []
        for row in rows:
            payload = json.loads(row["payload"])
            outcomes.append(
                RoundOutcomeView(
                    round_id=row["round_id"],
                    step=row["step"],
                    proposed_offered=payload.get("proposed_offered"),
                    proposed_accepted=payload.get("proposed_accepted"),
                )
            )
        return tuple(outcomes)

    def list_rounds(
        self, project_id: ProjectId, *, limit: int = 20
    ) -> tuple[RoundHeadView, ...]:
        """这个项目走过的轮次，**最新在前**。与 `recent_rounds` 同一个形状，但给人看。

        两条只读规矩（`02` §11.0.10）：
        ① **不建表、不缓存**——轮次的真相源是 `rounds` 表本身，这里只是查询入口；
        ② **坏行跳过，不抛**——老库或手工改坏的 payload 不该让整个项目打不开。
        """

        rows = self._connection.execute(
            """
            SELECT round_id, step, payload, created_at
            FROM rounds
            WHERE project_id = ?
            ORDER BY created_at DESC, round_id DESC
            LIMIT ?
            """,
            (str(project_id), limit),
        ).fetchall()
        heads: list[RoundHeadView] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            heads.append(
                round_head_view(
                    round_id=row["round_id"],
                    step=row["step"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    payload=payload,
                )
            )
        return tuple(heads)

    def recent_opinions(
        self, project_id: ProjectId, *, limit_rounds: int = 10
    ) -> tuple[OpinionSourceView, ...]:
        """最近几轮的席位发言（F6 的搜索源之一）。**每一趟都收**（`seat_reviews` 里全在）。

        与 `list_rounds` 同一条只读口径：不建表、不缓存；坏行跳过不抛。
        """

        rows = self._connection.execute(
            """
            SELECT round_id, payload
            FROM rounds
            WHERE project_id = ?
            ORDER BY created_at DESC, round_id DESC
            LIMIT ?
            """,
            (str(project_id), limit_rounds),
        ).fetchall()
        found: list[OpinionSourceView] = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            reviews = payload.get("seat_reviews")
            if not isinstance(reviews, list):
                continue
            for review in reviews:
                if not isinstance(review, dict):
                    continue
                seat_id = str(review.get("seat_id") or "")
                opinions = review.get("opinions")
                if not isinstance(opinions, list):
                    continue
                for opinion in opinions:
                    if not isinstance(opinion, dict):
                        continue
                    claim = str(opinion.get("claim") or "").strip()
                    if claim:
                        found.append(
                            OpinionSourceView(
                                round_id=row["round_id"], seat_id=seat_id, claim=claim
                            )
                        )
        return tuple(found)
