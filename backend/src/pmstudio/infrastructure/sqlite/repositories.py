"""SQLite 仓储。实现 domain 的六个端口 + application 的 WorkspaceIndex。

注意：这个文件**不许 import `DocumentWriter`**（写入能力白名单只有
`domain/services/committer.py`）。这里的 `save()` 只是同名方法，
结构化满足 `DocumentWriter` 协议由守卫与提交点的类型标注保证。
"""

import json
import sqlite3
import time
from datetime import UTC, datetime

from pmstudio.application.dto import (
    OpinionSourceView,
    ProjectSummaryView,
    RoundHeadView,
    RoundOutcomeView,
    round_head_view,
)
from pmstudio.application.errors import (
    ExplorationBusy,
    LeaseLost,
    UnknownDiscussion,
    UnknownExploration,
    UnknownProject,
    UnknownRound,
    UnknownTemplate,
)
from pmstudio.application.lease import current_lease_scope
from pmstudio.application.ports import LeaseStore, TemplateEntry
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
from pmstudio.domain.round.skip import RoundSkip
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import (
    DiscussionId,
    ExplorationId,
    ProjectId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.domain.shared.template import DocumentTemplate, standard_template
from pmstudio.infrastructure.sqlite import serialization as ser
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore
from pmstudio.infrastructure.sqlite.owner_scope import optional_owner, require_owner


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _running_conflict(error: sqlite3.IntegrityError) -> bool:
    """这条完整性错误是不是"同一项目已有 running"的部分唯一索引？"""

    text = str(error)
    return "idx_explorations_running" in text or (
        "UNIQUE constraint failed" in text and "explorations.project_id" in text
    )


def _matching_lease(
    kind: LeaseKind, target_id: str, owner: UserId
) -> LeaseToken | None:
    """当前 contextvar 里那把**属于这一行**的租约；没有/不对就是 `None`。

    只做形状匹配；"是否仍然有效"由仓储的 SQL 条件判（本地标记最多是个快速失败）。
    """

    scope = current_lease_scope()
    if scope is None or scope.lost:
        return None
    token = scope.token
    if token is None:
        return None
    if (
        token.kind is not kind
        or token.target_id != target_id
        or token.user_id != str(owner)
    ):
        return None
    return token


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
    def __init__(
        self, connection: sqlite3.Connection, *, leases: LeaseStore | None = None
    ) -> None:
        self._connection = connection
        self._leases: LeaseStore = leases or SqliteLeaseStore(connection)

    def get(self, round_id: RoundId, *, owner: UserId | None = None) -> Round:
        resolved = require_owner(owner)
        row = self._connection.execute(
            "SELECT * FROM rounds WHERE round_id = ? AND owner_id = ?",
            (str(round_id), str(resolved)),
        ).fetchone()
        if row is None:
            raise UnknownRound(round_id)
        round_ = ser.round_from_payload(
            json.loads(row["payload"]),
            round_id=RoundId(row["round_id"]),
            project_id=ProjectId(row["project_id"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            step=Step(row["step"]),
            waiting_on=WaitingOn(row["waiting_on"]),
        )
        round_.apply_skip_controls(self.skips_for(round_id, owner=resolved))
        return round_

    def skips_for(
        self, round_id: RoundId, *, owner: UserId | None = None
    ) -> tuple[RoundSkip, ...]:
        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT seat_id, pass_index, reason, source, requested_at FROM round_skips"
            " WHERE round_id = ? AND owner_id = ? ORDER BY requested_at, rowid",
            (str(round_id), str(resolved)),
        ).fetchall()
        return tuple(
            RoundSkip(
                seat_id=str(row["seat_id"]),
                pass_index=int(row["pass_index"]),
                reason=str(row["reason"]),
                source=str(row["source"]),
                requested_at_ms=int(row["requested_at"]),
            )
            for row in rows
        )

    def record_skip(
        self, round_id: RoundId, skip: RoundSkip, *, owner: UserId | None = None
    ) -> None:
        """latest-wins：同一 `(round_id, seat_id)` 只留一行，`pass_index` 随新控制写更新。"""

        resolved = require_owner(owner)
        known = self._connection.execute(
            "SELECT 1 FROM rounds WHERE round_id = ? AND owner_id = ?",
            (str(round_id), str(resolved)),
        ).fetchone()
        if known is None:
            raise UnknownRound(round_id)
        self._connection.execute(
            """
            INSERT INTO round_skips
                (round_id, owner_id, seat_id, pass_index, reason, source, requested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(round_id, seat_id) DO UPDATE SET
                pass_index = excluded.pass_index,
                reason = excluded.reason,
                source = excluded.source,
                requested_at = excluded.requested_at
            """,
            (
                str(round_id),
                str(resolved),
                skip.seat_id,
                skip.pass_index,
                skip.reason,
                skip.source,
                skip.requested_at_ms,
            ),
        )

    def save(self, round_: Round, *, owner: UserId | None = None) -> None:
        """写一轮。**existing 行必须有当前上下文里的活租约**（fencing）。

        新 INSERT 允许无租约（建轮那一刻还没出租；Task 2 会在同一 UoW 里补 acquire）。
        existing 行则必须同时满足 `owner_id / lease_owner / lease_token /
        lease_expires_at > now`——旧 worker 在过期被别人接管后的迟到写会抛 `LeaseLost`，
        由外层事务整笔回滚。
        """

        resolved = require_owner(owner)
        target_id = str(round_.round_id)
        payload = json.dumps(ser.round_to_payload(round_), ensure_ascii=False)
        exists = (
            self._connection.execute(
                "SELECT 1 FROM rounds WHERE round_id = ?", (target_id,)
            ).fetchone()
            is not None
        )
        if not exists:
            try:
                self._connection.execute(
                    """
                    INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on,
                                        payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_id,
                        str(round_.project_id),
                        str(resolved),
                        str(round_.step),
                        str(round_.waiting_on),
                        payload,
                        round_.created_at.isoformat(),
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError:
                # 查与插之间被别人建了同一行 → 这一行不是我们的租约，不许覆盖。
                if (
                    self._connection.execute(
                        "SELECT 1 FROM rounds WHERE round_id = ?", (target_id,)
                    ).fetchone()
                    is not None
                ):
                    raise LeaseLost(
                        kind=LeaseKind.ROUND, target_id=target_id
                    ) from None
                raise
            return

        now_ms = _now_ms()
        token = _matching_lease(LeaseKind.ROUND, target_id, resolved)
        if token is None or not self._leases.holds(token, now_ms=now_ms):
            raise LeaseLost(kind=LeaseKind.ROUND, target_id=target_id)
        cursor = self._connection.execute(
            """
            UPDATE rounds
            SET step = ?, waiting_on = ?, payload = ?, updated_at = ?
            WHERE round_id = ? AND owner_id = ?
              AND lease_owner = ? AND lease_token = ?
              AND lease_expires_at > ?
            """,
            (
                str(round_.step),
                str(round_.waiting_on),
                payload,
                _now(),
                target_id,
                str(resolved),
                token.holder,
                token.token,
                now_ms,
            ),
        )
        if cursor.rowcount != 1:
            raise LeaseLost(kind=LeaseKind.ROUND, target_id=target_id)

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

    def __init__(
        self, connection: sqlite3.Connection, *, leases: LeaseStore | None = None
    ) -> None:
        self._connection = connection
        self._leases: LeaseStore = leases or SqliteLeaseStore(connection)

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
        """写一趟探索；existing 行的 fencing 契约与轮次仓储逐字相同。

        INSERT 允许无租约（create 在同一 UoW 紧接着 `acquire` 写 token=1）。
        旧的 running 行（清僵壳 / 完成 / 失败 / 放弃）必须持有租约；**两端都不是
        running** 的短控制写（收下 / 删掉候选）显式豁免——它们只在终态行上发生，
        没有长动作会同时写它。

        项目内唯一 running 的部分索引冲突在这里统一翻成 `ExplorationBusy`：
        create / retry 的调用方不需要知道 sqlite 的约束名。
        """

        resolved = require_owner(owner)
        target_id = str(exploration.exploration_id)
        payload = json.dumps(
            ser.exploration_to_payload(exploration), ensure_ascii=False
        )
        existing = self._connection.execute(
            "SELECT status FROM explorations WHERE exploration_id = ?", (target_id,)
        ).fetchone()
        if existing is None:
            try:
                self._connection.execute(
                    """
                    INSERT INTO explorations (exploration_id, project_id, owner_id, kind,
                                              status, payload, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        target_id,
                        str(exploration.project_id),
                        str(resolved),
                        str(exploration.kind),
                        str(exploration.status),
                        payload,
                        exploration.created_at.isoformat(),
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                if (
                    self._connection.execute(
                        "SELECT 1 FROM explorations WHERE exploration_id = ?",
                        (target_id,),
                    ).fetchone()
                    is not None
                ):
                    raise LeaseLost(
                        kind=LeaseKind.EXPLORATION, target_id=target_id
                    ) from None
                if _running_conflict(error):
                    raise ExplorationBusy(exploration.project_id) from None
                raise
            return

        if (
            str(existing["status"]) != ExplorationStatus.RUNNING.value
            and str(exploration.status) != ExplorationStatus.RUNNING.value
        ):
            cursor = self._connection.execute(
                """
                UPDATE explorations
                SET kind = ?, status = ?, payload = ?, updated_at = ?
                WHERE exploration_id = ? AND owner_id = ?
                """,
                (
                    str(exploration.kind),
                    str(exploration.status),
                    payload,
                    _now(),
                    target_id,
                    str(resolved),
                ),
            )
            if cursor.rowcount != 1:
                raise LeaseLost(kind=LeaseKind.EXPLORATION, target_id=target_id)
            return

        now_ms = _now_ms()
        token = _matching_lease(LeaseKind.EXPLORATION, target_id, resolved)
        if token is None or not self._leases.holds(token, now_ms=now_ms):
            raise LeaseLost(kind=LeaseKind.EXPLORATION, target_id=target_id)
        try:
            cursor = self._connection.execute(
                """
                UPDATE explorations
                SET kind = ?, status = ?, payload = ?, updated_at = ?
                WHERE exploration_id = ? AND owner_id = ?
                  AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > ?
                """,
                (
                    str(exploration.kind),
                    str(exploration.status),
                    payload,
                    _now(),
                    target_id,
                    str(resolved),
                    token.holder,
                    token.token,
                    now_ms,
                ),
            )
        except sqlite3.IntegrityError as error:
            if _running_conflict(error):
                raise ExplorationBusy(exploration.project_id) from None
            raise
        if cursor.rowcount != 1:
            raise LeaseLost(kind=LeaseKind.EXPLORATION, target_id=target_id)

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

    def list_running(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> tuple[Exploration, ...]:
        """这一项目所有 `running` 行，**不带 LIMIT**（清僵壳看全量，不看最近 20 条）。"""

        resolved = require_owner(owner)
        rows = self._connection.execute(
            "SELECT * FROM explorations WHERE project_id = ? AND owner_id = ?"
            " AND status = 'running' ORDER BY created_at, rowid",
            (str(project_id), str(resolved)),
        ).fetchall()
        found: list[Exploration] = []
        for row in rows:
            try:
                found.append(self._from_row(row))
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                continue
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
            if isinstance(materials, list):
                for item in materials:
                    if not isinstance(item, dict):
                        continue
                    material_id = str(item.get("material_id") or "").strip()
                    eid = str(item.get("eid") or "").strip()
                    if not material_id or not eid:
                        continue
                    found.setdefault(material_id, []).append(eid)
            # **手勾 / 手动带料也算引用**（2026-10-24，P2.0）：它们可能还没投影、没有 E 号，
            # 但用户已经明确要它们；不挡住的话，删掉/就地重解析会让下一次投影拿到空包。
            # 哨兵不是 E 号：错误文案里会显示"手勾"而不是"E12"，这是有意的——
            # 它确实还没有编号。
            for material_id in payload.get("pinned_materials") or ():
                key = str(material_id or "").strip()
                if key:
                    found.setdefault(key, []).append("手勾")
            for item in payload.get("manual_chunks") or ():
                if not isinstance(item, dict):
                    continue
                key = str(item.get("material_id") or "").strip()
                if key:
                    found.setdefault(key, []).append("手动带料")
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
        # **不用 `require_owner`**：这条读的契约本来就含"无主也可见"的行——builtin 模板
        # `owner_id` 恒 NULL。而 `build_container` 在组装期就要读它，那会儿还没有身份
        # （`require_user` 只在请求里设 scope），硬要求 owner 会让应用起不来。
        # 有身份 → builtin + 自己的；没身份 → 只回 builtin（绝不回别人的自建模板）。
        resolved = optional_owner(owner)
        if resolved is None:
            rows = self._connection.execute(
                "SELECT spec, builtin FROM templates"
                " WHERE owner_id IS NULL ORDER BY builtin DESC, title"
            ).fetchall()
        else:
            rows = self._connection.execute(
                # 内置在前（画廊默认选 standard，它得排第一）；同组按标题排，不按 id
                # （按 id 的话 light 永在 standard 前面，默认高亮落在第二张卡上）。
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
