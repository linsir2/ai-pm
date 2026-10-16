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
from pmstudio.application.errors import (
    UnknownDiscussion,
    UnknownExploration,
    UnknownProject,
    UnknownRound,
    UnknownTemplate,
)
from pmstudio.application.ports import TemplateEntry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.discussion.thread import Discussion, ThreadStatus
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationKind,
    ExplorationStatus,
)
from pmstudio.domain.memory.memory import ProjectMemory
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import (
    DiscussionId,
    ExplorationId,
    ProjectId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.template import DocumentTemplate, standard_template
from pmstudio.infrastructure.sqlite import serialization as ser
from pmstudio.infrastructure.sqlite.owner_scope import require_owner


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SqliteDocumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, project_id: ProjectId, *, owner: UserId | None = None) -> Document:
        resolved = require_owner(owner)
        row = self._row(project_id, owner=resolved)
        versions = tuple(
            ser.version_from_row(
                {
                    **dict(item),
                    "changed_block_ids": json.loads(item["changed_block_ids"]),
                    "snapshot": json.loads(item["snapshot"]),
                }
            )
            for item in self.version_rows(project_id, owner=resolved)
        )
        return ser.document_from_row(
            {
                **dict(row),
                "blocks": json.loads(row["blocks"]),
                "preference_entries": json.loads(row["preference_entries"]),
                "tombstones": json.loads(row["tombstones"]),
                # 老库补列前存下的行没有 template（ADDED_COLUMNS 给 '{}'）→
                # 当缺失回 STANDARD（`document_from_row` 的 `.get` 回落）。
                # `'{}'` 是 truthy 字符串，必须一并判空，否则 `from_json` 炸 KeyError。
                "template": (
                    json.loads(row["template"]) or None
                    if "template" in row.keys() and row["template"]
                    else None
                ),
            },
            versions,
        )

    def save(self, document: Document, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        row = ser.document_to_row(document, now=datetime.now(UTC))
        self._connection.execute(
            """
            INSERT INTO documents (project_id, owner_id, document_id, title, template, blocks,
                                   preference_entries, tombstones, created_at, updated_at)
            VALUES (:project_id, :owner_id, :document_id, :title, :template, :blocks,
                    :preference_entries, :tombstones, :created_at, :updated_at)
            ON CONFLICT(project_id) DO UPDATE SET
                title = excluded.title,
                template = excluded.template,
                blocks = excluded.blocks,
                preference_entries = excluded.preference_entries,
                tombstones = excluded.tombstones,
                updated_at = excluded.updated_at
            WHERE owner_id = :owner_id
            """,
            {
                **row,
                "owner_id": str(resolved),
                "template": json.dumps(row["template"], ensure_ascii=False),
                "blocks": json.dumps(row["blocks"], ensure_ascii=False),
                "preference_entries": json.dumps(
                    row["preference_entries"], ensure_ascii=False
                ),
                "tombstones": json.dumps(row["tombstones"], ensure_ascii=False),
            },
        )
        # 越权 save 是空操作：不属于本 owner 的行既不更新、也不落版本
        # （`document_versions` 的 INSERT OR IGNORE 若照跑，会往别人项目塞版本行）。
        owned = self._connection.execute(
            "SELECT 1 FROM documents WHERE project_id = ? AND owner_id = ?",
            (str(document.project_id), str(resolved)),
        ).fetchone()
        if owned is None:
            return
        for version in document.versions:
            payload = ser.version_to_row(document.project_id, version)
            self._connection.execute(
                """
                INSERT OR IGNORE INTO document_versions
                    (project_id, owner_id, version_no, trigger, summary, changed_block_ids,
                     snapshot, created_at)
                VALUES (:project_id, :owner_id, :version_no, :trigger, :summary,
                        :changed_block_ids, :snapshot, :created_at)
                """,
                {
                    **payload,
                    "owner_id": str(resolved),
                    "changed_block_ids": json.dumps(
                        payload["changed_block_ids"], ensure_ascii=False
                    ),
                    "snapshot": json.dumps(payload["snapshot"], ensure_ascii=False),
                },
            )

    def version_rows(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> list[sqlite3.Row]:
        resolved = require_owner(owner)
        return list(
            self._connection.execute(
                "SELECT * FROM document_versions WHERE project_id = ? AND owner_id = ?"
                " ORDER BY version_no",
                (str(project_id), str(resolved)),
            )
        )

    def _row(self, project_id: ProjectId, *, owner: UserId) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM documents WHERE project_id = ? AND owner_id = ?",
            (str(project_id), str(owner)),
        ).fetchone()
        if row is None:
            raise UnknownProject(project_id)
        return row


class SqliteRoundRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, round_id: RoundId, *, owner: UserId | None = None) -> Round:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM rounds WHERE round_id = ? AND owner_id = ?",
            (str(round_id), str(resolved)),
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

    def save(self, round_: Round, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        payload = json.dumps(ser.round_to_payload(round_), ensure_ascii=False)
        self._connection.execute(
            """
            INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(round_id) DO UPDATE SET
                step = excluded.step,
                waiting_on = excluded.waiting_on,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            WHERE owner_id = ?
            """,
            (
                str(round_.round_id),
                str(round_.project_id),
                str(resolved),
                str(round_.step),
                str(round_.waiting_on),
                payload,
                round_.created_at.isoformat(),
                _now(),
                str(resolved),
            ),
        )

    def latest_round_id(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> RoundId | None:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT round_id FROM rounds WHERE project_id = ? AND owner_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (str(project_id), str(resolved)),
        ).fetchone()
        return None if row is None else RoundId(row["round_id"])


class SqliteExplorationRepository:
    """探索运行（M8）：读端口 + 写端口都在一个类上（与 Round 仓储同一个形状）。

    两条口径与轮次仓储一致：**坏行跳过不抛**（`list_for_project` 里逐行 try），
    状态与类别是列——"同项目有没有在跑"这类查询不该把整份 payload 读出来。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(
        self, exploration_id: ExplorationId, *, owner: UserId | None = None
    ) -> Exploration:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM explorations WHERE exploration_id = ? AND owner_id = ?",
            (str(exploration_id), str(resolved)),
        ).fetchone()
        if row is None:
            raise UnknownExploration(exploration_id)
        return self._from_row(row)

    def save(self, exploration: Exploration, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        payload = json.dumps(
            ser.exploration_to_payload(exploration), ensure_ascii=False
        )
        self._connection.execute(
            """
            INSERT INTO explorations (exploration_id, project_id, owner_id, kind, status,
                                      payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exploration_id) DO UPDATE SET
                kind = excluded.kind,
                status = excluded.status,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            WHERE owner_id = ?
            """,
            (
                str(exploration.exploration_id),
                str(exploration.project_id),
                str(resolved),
                str(exploration.kind),
                str(exploration.status),
                payload,
                exploration.created_at.isoformat(),
                _now(),
                str(resolved),
            ),
        )

    def list_for_project(
        self, project_id: ProjectId, *, owner: UserId | None = None, limit: int = 20
    ) -> tuple[Exploration, ...]:
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM explorations WHERE project_id = ? AND owner_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (str(project_id), str(resolved), limit),
        ).fetchall()
        found: list[Exploration] = []
        for row in rows:
            try:
                found.append(self._from_row(row))
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                continue  # 坏行跳过不抛（与 list_rounds 同一条口径）
        return tuple(found)

    def _from_row(self, row: sqlite3.Row) -> Exploration:
        return ser.exploration_from_payload(
            json.loads(row["payload"]),
            exploration_id=ExplorationId(row["exploration_id"]),
            project_id=ProjectId(row["project_id"]),
            kind=ExplorationKind(row["kind"]),
            status=ExplorationStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteDiscussionRepository:
    """讨论线程（M9）：读端口 + 写端口都在一个类上（与探索仓储同一个形状）。

    同一条口径：**坏行跳过不抛**（`list_for_project` 里逐行 try），
    状态是列——"这个项目有没有开着的讨论"这类查询不该把整份 payload 读出来。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(
        self, discussion_id: DiscussionId, *, owner: UserId | None = None
    ) -> Discussion:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM discussions WHERE discussion_id = ? AND owner_id = ?",
            (str(discussion_id), str(resolved)),
        ).fetchone()
        if row is None:
            raise UnknownDiscussion(discussion_id)
        return self._from_row(row)

    def save(self, discussion: Discussion, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        payload = json.dumps(ser.discussion_to_payload(discussion), ensure_ascii=False)
        self._connection.execute(
            """
            INSERT INTO discussions (discussion_id, project_id, owner_id, status, payload,
                                     created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discussion_id) DO UPDATE SET
                status = excluded.status,
                payload = excluded.payload,
                updated_at = excluded.updated_at
            WHERE owner_id = ?
            """,
            (
                str(discussion.discussion_id),
                str(discussion.project_id),
                str(resolved),
                str(discussion.status),
                payload,
                discussion.created_at.isoformat(),
                _now(),
                str(resolved),
            ),
        )

    def list_for_project(
        self, project_id: ProjectId, *, owner: UserId | None = None, limit: int = 20
    ) -> tuple[Discussion, ...]:
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM discussions WHERE project_id = ? AND owner_id = ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (str(project_id), str(resolved), limit),
        ).fetchall()
        found: list[Discussion] = []
        for row in rows:
            try:
                found.append(self._from_row(row))
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                continue  # 坏行跳过不抛（与 explorations 同一条口径）
        return tuple(found)

    def _from_row(self, row: sqlite3.Row) -> Discussion:
        return ser.discussion_from_payload(
            json.loads(row["payload"]),
            discussion_id=DiscussionId(row["discussion_id"]),
            project_id=ProjectId(row["project_id"]),
            status=ThreadStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class SqliteEvidenceRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(self, project_id: ProjectId, *, owner: UserId | None = None) -> EvidenceLedger:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT payload FROM evidence_ledgers WHERE project_id = ? AND owner_id = ?",
            (str(project_id), str(resolved)),
        ).fetchone()
        if row is None:
            raise UnknownProject(project_id)
        return ser.ledger_from_json(project_id, json.loads(row["payload"]))

    def save(self, ledger: EvidenceLedger, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            """
            INSERT INTO evidence_ledgers (project_id, owner_id, payload, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                payload = excluded.payload,
                updated_at = excluded.updated_at
            WHERE owner_id = ?
            """,
            (
                str(ledger.project_id),
                str(resolved),
                json.dumps(ser.ledger_to_json(ledger), ensure_ascii=False),
                _now(),
                str(resolved),
            ),
        )


def _template_ref(template_raw: object) -> tuple[str, str]:
    """行快照 → (template_id, template_title)。缺/坏 → STANDARD。

    缺：老库补列前存下的行（ADDED_COLUMNS 给 `'{}'`，truthy 字符串，判空不够——
    `test_sqlite_templates.py` 钉住这一坑）。坏：JSON 炸（memory repo 同款容错：
    读投影不该被一行坏数据打崩整个列表）。
    """

    try:
        data = json.loads(template_raw) if template_raw else {}
        template_id = str(data.get("template_id") or "")
        title = str(data.get("title") or "")
        if template_id and title:
            return template_id, title
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    standard = standard_template()
    return standard.template_id, standard.title


class SqliteWorkspaceIndex:
    """只读查询投影：摘要、列表、最新一轮。不产生第二份真相源。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
    def summary(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> ProjectSummaryView:
        resolved = require_owner(owner)
        row = self._connection.execute(
            """
            SELECT d.project_id AS project_id, d.title AS title,
                   MAX(v.version_no) AS version_no, d.updated_at AS updated_at,
                   d.template AS template
            FROM documents d
            LEFT JOIN document_versions v ON v.project_id = d.project_id
            WHERE d.project_id = ? AND d.owner_id = ?
            GROUP BY d.project_id, d.title, d.updated_at, d.template
            """,
            (str(project_id), str(resolved)),
        ).fetchone()
        if row is None:
            raise UnknownProject(project_id)
        template_id, template_title = _template_ref(row["template"])
        return ProjectSummaryView(
            project_id=row["project_id"],
            title=row["title"],
            version_no=int(row["version_no"] or 1),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            template_id=template_id,
            template_title=template_title,
        )

    def list_summaries(self, *, owner: UserId | None = None) -> tuple[ProjectSummaryView, ...]:
        resolved = require_owner(owner)
        rows = self._connection.execute(
            """
            SELECT d.project_id AS project_id, d.title AS title,
                   MAX(v.version_no) AS version_no, d.updated_at AS updated_at,
                   d.template AS template
            FROM documents d
            LEFT JOIN document_versions v ON v.project_id = d.project_id
            WHERE d.owner_id = ?
            GROUP BY d.project_id, d.title, d.updated_at, d.template
            ORDER BY d.updated_at DESC
            """,
            (str(resolved),),
        ).fetchall()
        summaries: list[ProjectSummaryView] = []
        for row in rows:
            template_id, template_title = _template_ref(row["template"])
            summaries.append(
                ProjectSummaryView(
                    project_id=row["project_id"],
                    title=row["title"],
                    version_no=int(row["version_no"] or 1),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    template_id=template_id,
                    template_title=template_title,
                )
            )
        return tuple(summaries)

    def latest_round_id(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> RoundId | None:
        return SqliteRoundRepository(self._connection).latest_round_id(
            project_id, owner=owner
        )

    def recent_rounds(
        self, project_id: ProjectId, *, owner: UserId | None = None, limit: int = 5
    ) -> tuple[RoundOutcomeView, ...]:
        """最近几轮的**结果投影**（C4：连续几轮没采用席位提议）。

        只读 payload 里那几个键——查历史不该把整轮重建出来。倒序排 `created_at`，
        同刻的按 `round_id` 倒序（测试里两轮常在同一时刻）。
        """

        resolved = require_owner(owner)
        rows = self._connection.execute(
            """
            SELECT round_id, step, payload
            FROM rounds
            WHERE project_id = ? AND owner_id = ?
            ORDER BY created_at DESC, round_id DESC
            LIMIT ?
            """,
            (str(project_id), str(resolved), limit),
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
        self, project_id: ProjectId, *, owner: UserId | None = None, limit: int = 20
    ) -> tuple[RoundHeadView, ...]:
        """这个项目走过的轮次，**最新在前**。与 `recent_rounds` 同一个形状，但给人看。

        两条只读规矩（`02` §11.0.10）：
        ① **不建表、不缓存**——轮次的真相源是 `rounds` 表本身，这里只是查询入口；
        ② **坏行跳过，不抛**——老库或手工改坏的 payload 不该让整个项目打不开。
        """

        resolved = require_owner(owner)
        rows = self._connection.execute(
            """
            SELECT round_id, step, payload, created_at
            FROM rounds
            WHERE project_id = ? AND owner_id = ?
            ORDER BY created_at DESC, round_id DESC
            LIMIT ?
            """,
            (str(project_id), str(resolved), limit),
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
        self, project_id: ProjectId, *, owner: UserId | None = None, limit_rounds: int = 10
    ) -> tuple[OpinionSourceView, ...]:
        """最近几轮的席位发言（F6 的搜索源之一）。**每一趟都收**（`seat_reviews` 里全在）。

        与 `list_rounds` 同一条只读口径：不建表、不缓存；坏行跳过不抛。
        """

        resolved = require_owner(owner)
        rows = self._connection.execute(
            """
            SELECT round_id, payload
            FROM rounds
            WHERE project_id = ? AND owner_id = ?
            ORDER BY created_at DESC, round_id DESC
            LIMIT ?
            """,
            (str(project_id), str(resolved), limit_rounds),
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

    def material_references(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> dict[str, tuple[str, ...]]:
        """这份材料被哪些编号引用过：`{material_id: (E4, E9)}`（`P19` 的冻结判据）。

        三件事说清楚：

        ① **真相源是轮次**（`rounds.payload.materials[]` 的 `material_id` + `eid`）——
           账本里的 `external_ref` 是按标题拼的（`attachment:<标题>#cNN`），两份同名文件会撞；
        ② 与 `list_rounds` / `recent_opinions` 同一条只读口径：**不建表、不缓存**，
           坏行**跳过不抛**（手工改坏的 payload 不该让整个项目打不开）；
        ③ **不限轮数**（不是"最近 N 轮"）：冻结判据必须看到全部历史，漏一轮就会误删。
        """

        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT payload FROM rounds WHERE project_id = ? AND owner_id = ?"
            " ORDER BY created_at, round_id",
            (str(project_id), str(resolved)),
        ).fetchall()
        found: dict[str, list[str]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                continue
            materials = payload.get("materials")
            if not isinstance(materials, list):
                continue
            for item in materials:
                if not isinstance(item, dict):
                    continue
                material_id = str(item.get("material_id") or "").strip()
                eid = str(item.get("eid") or "").strip()
                if not material_id or not eid:
                    continue
                found.setdefault(material_id, []).append(eid)
        return {material_id: tuple(eids) for material_id, eids in found.items()}


class SqliteMemoryRepository:
    """跨轮记忆（M6）：读端口 + 写端口，都在一个类上（与文档仓储同一个形状）。

    两条口径：

    ① **没有该项目的行 = 空记忆**（不是错误）——老项目、刚建的项目都是这个状态；
    ② 坏 payload **跳过不抛**（`serialization.memory_from_payload` 的语义）：
       手工改坏的记忆不该让整个工作台打不开。

    写入是**整份覆写**（一个项目一行）：记忆 ≤60 条，payload 就是全部状态，
    没有并发（单用户单进程），所以不需要增量更新。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def get(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> ProjectMemory:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT payload FROM project_memory WHERE project_id = ? AND owner_id = ?",
            (str(project_id), str(resolved)),
        ).fetchone()
        if row is None:
            return ProjectMemory.empty(project_id)
        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            return ProjectMemory.empty(project_id)
        return ser.memory_from_payload(payload, project_id=project_id)

    def save(self, memory: ProjectMemory, *, owner: UserId | None = None) -> None:
        resolved = require_owner(owner)
        self._connection.execute(
            "INSERT INTO project_memory (project_id, owner_id, payload, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(project_id) DO UPDATE SET payload = excluded.payload,"
            " updated_at = excluded.updated_at WHERE owner_id = ?",
            (
                str(memory.project_id),
                str(resolved),
                json.dumps(ser.memory_to_payload(memory), ensure_ascii=False),
                _now(),
                str(resolved),
            ),
        )


class SqliteTemplateRepository:
    """模板库表：定义时来源。读只给 `DocumentTemplate`，不做 validate
    （入库前调用方已断过；行内快照的 validate 在 `Document` 构造里）。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def save(
        self, template: DocumentTemplate, *, builtin: bool, owner: UserId | None = None
    ) -> None:
        # builtin 行是出厂模板：owner 留 NULL（人人可见）；自建模板必须盖章。
        owner_id = None if builtin else str(require_owner(owner))
        self._connection.execute(
            "INSERT INTO templates (template_id, owner_id, title, spec, builtin, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(template_id) DO UPDATE SET title = excluded.title,"
            " spec = excluded.spec, builtin = excluded.builtin,"
            " updated_at = excluded.updated_at WHERE owner_id IS ?",
            (
                template.template_id,
                owner_id,
                template.title,
                json.dumps(ser.template_to_json(template), ensure_ascii=False),
                1 if builtin else 0,
                _now(),
                _now(),
                owner_id,
            ),
        )

    def list(self, *, owner: UserId | None = None) -> tuple[TemplateEntry, ...]:
        resolved = require_owner(owner)
        rows = self._connection.execute(
            # 内置在前（画廊默认选 standard，它得排第一）；同组按标题排，不按 id
            # （按 id 的话 light 永在 standard 前面，默认高亮落在第二张卡上）。
            # builtin 行 owner 为 NULL——人人可见（`owner_id IS NULL`）。
            "SELECT spec, builtin FROM templates"
            " WHERE owner_id IS NULL OR owner_id = ? ORDER BY builtin DESC, title",
            (str(resolved),),
        ).fetchall()
        return tuple(
            TemplateEntry(
                template=ser.template_from_json(json.loads(row["spec"])),
                builtin=bool(row["builtin"]),
            )
            for row in rows
        )

    def get(self, template_id: str, *, owner: UserId | None = None) -> TemplateEntry:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT spec, builtin FROM templates"
            " WHERE template_id = ? AND (owner_id IS NULL OR owner_id = ?)",
            (template_id, str(resolved)),
        ).fetchone()
        if row is None:
            raise UnknownTemplate(template_id)
        return TemplateEntry(
            template=ser.template_from_json(json.loads(row["spec"])),
            builtin=bool(row["builtin"]),
        )

    def delete(self, template_id: str, *, owner: UserId | None = None) -> None:
        """删一行。删不掉就是"这个 id 不在库里"——抛出去，不静默成功。

        不碰 `documents`：项目行里存的是模板的**全文 JSON 快照**（`template_to_json`），
        不是 id 引用，两张表没有外键，所以这里不需要级联、也不需要引用检查
        （`02` §11.0.70）。

        **按 owner 收窄 + `builtin = 0`**（多用户改造 fix round）：builtin 行
        （owner NULL、人人可见）不由此路删——`builtin = 0` 与 `owner_id = ?` 各挡一道；
        用例层 `BuiltinTemplateProtected` 是主防线，这里是物理边界。
        """

        resolved = require_owner(owner)
        cursor = self._connection.execute(
            "DELETE FROM templates WHERE template_id = ? AND owner_id = ? AND builtin = 0",
            (template_id, str(resolved)),
        )
        if cursor.rowcount == 0:
            raise UnknownTemplate(template_id)
