"""真 PostgreSQL：T4 核心仓储（documents / versions / rounds / explorations /
discussions / evidence / memory / projects）与 round 行锁。

这些用例直接打 0001 schema：JSONB / timestamptz / text[] / seq / ON CONFLICT /
SELECT ... FOR UPDATE 与 psycopg `%s` 一并验证。旧 SQLite 用例留给 T8 统一改。
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

import pytest
from psycopg.types.json import Jsonb
from scripts.migrate import apply_migrations

from pmstudio.application.errors import (
    ExplorationBusy,
    LeaseLost,
    UnknownDiscussion,
    UnknownExploration,
    UnknownProject,
    UnknownRound,
    WebExecutionLimitReached,
)
from pmstudio.application.lease import (
    LeaseScope,
    clear_lease_scope,
    set_lease_scope,
)
from pmstudio.application.web_research import claim_web_request
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.discussion.thread import Discussion, Participant
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.memory.memory import MemoryEntry, ProjectMemory
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.opinion import SeatReview
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.skip import RoundSkip, user_skip_reason
from pmstudio.domain.shared.ids import (
    DiscussionId,
    EntryId,
    EvidenceId,
    ExplorationId,
    ProjectId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.domain.shared.memory import MemoryKind
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.postgres import serialization as ser
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.owner_scope import set_owner_scope
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.support.builders import NOW, PROJECT, SEAT, make_document, make_round
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
)

OWNER = UserId("u-owner-scope-test")
OTHER = UserId("u-other")
ROUND = RoundId("r-core")
EXPLORATION = ExplorationId("e-core")
DISCUSSION = DiscussionId("d-core")


@dataclass(frozen=True, slots=True)
class _Database:
    pg: PgTestDatabase


@pytest.fixture(scope="module")
def database() -> _Database:
    pg = load_environment()
    prepare(pg)
    apply_migrations(pg.direct_url)
    return _Database(pg)


@pytest.fixture()
def conn(database: _Database) -> Iterator[object]:
    truncate_data_tables(database.pg)
    with connect(database.pg.direct_url) as connection:
        yield connection
    truncate_data_tables(database.pg)


def _second(database: _Database):
    return connect(database.pg.direct_url)


class _Holds:
    """T4 不迁 lease 时钟（T6）：只验证仓储的 fencing 条件本身。"""

    def holds(self, token):  # type: ignore[no-untyped-def]
        return True


def _grant_lease(connection, round_id: RoundId, *, owner: UserId = OWNER) -> LeaseToken:
    holder = "t4-holder"
    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id=str(round_id),
        user_id=str(owner),
        holder=holder,
        token=1,
        expires_at_ms=2**62,
    )
    connection.execute(
        "UPDATE rounds SET lease_owner = %s, lease_token = %s, lease_expires_at = %s"
        " WHERE round_id = %s AND owner_id = %s",
        (holder, token.token, token.expires_at_ms, str(round_id), str(owner)),
    )
    return token


def _bind(token: LeaseToken) -> tuple[object, object]:  # type: ignore[no-untyped-def]
    owner_token = set_owner_scope(UserId(token.user_id))
    scope = LeaseScope()
    scope.bind(token)
    lease_token = set_lease_scope(scope)
    return owner_token, lease_token


def _unbind(tokens: tuple[object, object]) -> None:  # type: ignore[no-untyped-def]
    clear_lease_scope(tokens[1])  # type: ignore[arg-type]
    from pmstudio.infrastructure.postgres.owner_scope import clear_owner_scope

    clear_owner_scope(tokens[0])  # type: ignore[arg-type]


# ── documents / versions ───────────────────────────────────────────────────


def test_document_and_versions_round_trip_jsonb_and_timestamptz(conn) -> None:
    documents = SqliteDocumentRepository(conn)
    document = make_document()
    documents.save(document)

    loaded = documents.get(PROJECT)
    assert loaded.title == document.title
    assert [str(block.block_id) for block in loaded.blocks] == [
        str(block.block_id) for block in document.blocks
    ]
    assert loaded.current_version.version_no == 1
    assert loaded.current_version.created_at.tzinfo is not None
    assert loaded.preference_entries == document.preference_entries

    loaded.apply(
        [
            WriteInstruction(
                instruction_id="i9",
                entry_id=EntryId("r1#99"),
                op=WriteOp.ADD,
                target=Target.body("s1"),
                text="背景：运营每天手工导表",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="第二版",
    )
    documents.save(loaded)

    rows = documents.version_rows(PROJECT)
    assert [row["version_no"] for row in rows] == [1, 2]
    assert rows[0]["summary"] == "初始化 13 章骨架"
    assert isinstance(rows[1]["changed_block_ids"], list)
    assert isinstance(rows[1]["snapshot"], dict)
    assert rows[1]["created_at"].tzinfo is not None

    with pytest.raises(UnknownProject):
        documents.get(PROJECT, owner=OTHER)


def test_foreign_owner_save_is_a_noop_and_does_not_write_versions(conn) -> None:
    documents = SqliteDocumentRepository(conn)
    document = make_document()
    documents.save(document)

    intruder = make_document(title="别人的标题")
    documents.save(intruder, owner=OTHER)

    loaded = documents.get(PROJECT)
    assert loaded.title == document.title
    assert [row["version_no"] for row in documents.version_rows(PROJECT)] == [1]
    assert documents.version_rows(PROJECT, owner=OTHER) == []


# ── rounds / skips ─────────────────────────────────────────────────────────


def test_project_factory_creates_empty_document_and_ledger_in_one_transaction(
    conn,
) -> None:
    from pmstudio.domain.shared.template import standard_template
    from pmstudio.infrastructure.postgres.project_factory import SqliteProjectFactory

    class _Ids:
        def new(self, prefix: str) -> str:
            return f"{prefix}-factory"

    document_id = SqliteProjectFactory(conn, ids=_Ids()).create(
        project_id=PROJECT,
        title="工厂建的项目",
        now=NOW,
        template=standard_template(),
    )

    assert str(document_id) == "doc-factory"
    assert SqliteDocumentRepository(conn).get(PROJECT).title == "工厂建的项目"
    assert SqliteEvidenceRepository(conn).get(PROJECT).evidences() == ()


def _round(round_id: str, *, created_at: datetime = NOW) -> Round:
    return Round.start(
        round_id=RoundId(round_id),
        project_id=PROJECT,
        user_input="想做个订单导出的功能",
        now=created_at,
    )


def test_round_payload_seq_order_and_skip_latest_wins(conn) -> None:
    rounds = SqliteRoundRepository(conn, leases=_Holds())
    first = _round("r-first")
    second = _round("r-second")
    rounds.save(first)
    rounds.save(second)

    # 同一 created_at：seq 决定"最新一轮"与列表顺序，不靠 round_id 字典序。
    assert rounds.latest_round_id(PROJECT) == RoundId("r-second")
    index = SqliteWorkspaceIndex(conn)
    assert [item.round_id for item in index.list_rounds(PROJECT)] == [
        "r-second",
        "r-first",
    ]

    assert rounds.get(RoundId("r-first")).user_input == "想做个订单导出的功能"

    rounds.record_skip(
        RoundId("r-first"),
        RoundSkip(
            seat_id=SEAT,
            pass_index=1,
            reason=user_skip_reason(SEAT),
            source="user_skipped",
            requested_at_ms=1_000,
        ),
    )
    rounds.record_skip(
        RoundId("r-first"),
        RoundSkip(
            seat_id=SEAT,
            pass_index=2,
            reason="第二趟再跳",
            source="user_skipped",
            requested_at_ms=2_000,
        ),
    )
    skips = rounds.skips_for(RoundId("r-first"))
    assert [(item.pass_index, item.reason) for item in skips] == [
        (2, "第二趟再跳")
    ]
    assert rounds.get(RoundId("r-first")).skipped_seats == (SEAT,)
    assert rounds.skips_for(RoundId("r-first"), owner=OTHER) == ()

    with pytest.raises(UnknownRound):
        rounds.get(RoundId("r-first"), owner=OTHER)
    with pytest.raises(UnknownRound):
        rounds.record_skip(
            RoundId("r-first"),
            RoundSkip(
                seat_id=SEAT,
                pass_index=3,
                reason="越权",
                source="user_skipped",
                requested_at_ms=3_000,
            ),
            owner=OTHER,
        )


def test_round_save_update_requires_matching_lease_and_keeps_jsonb(conn) -> None:
    rounds = SqliteRoundRepository(conn, leases=_Holds())
    round_ = make_round()
    rounds.save(round_)

    with pytest.raises(LeaseLost):
        rounds.save(round_)

    token = _grant_lease(conn, round_.round_id)
    tokens = _bind(token)
    try:
        stored = rounds.get(round_.round_id)
        stored.note_analysis_basis("含中文与 \\n 转义")
        rounds.save(stored)
    finally:
        _unbind(tokens)

    loaded = rounds.get(round_.round_id)
    assert loaded.analysis_basis == "含中文与 \\n 转义"


def test_get_for_update_blocks_the_second_connection_until_commit(
    database: _Database, conn
) -> None:
    rounds = SqliteRoundRepository(conn)
    rounds.save(_round("r-lock"))

    entered = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    def other_transaction() -> None:
        with _second(database) as other:
            try:
                with PgUnitOfWork(other):
                    entered.set()
                    SqliteRoundRepository(other).get_for_update(
                        RoundId("r-lock"), owner=OWNER
                    )
                    finished.set()
            except BaseException as error:  # noqa: BLE001
                errors.append(error)

    with PgUnitOfWork(conn):
        SqliteRoundRepository(conn).get_for_update(RoundId("r-lock"))
        worker = threading.Thread(target=other_transaction)
        worker.start()
        assert entered.wait(2), "第二连接没有开始"
        assert not finished.wait(0.3), "第二连接不应在持锁时读到"
    worker.join(timeout=5)

    assert errors == []
    assert finished.is_set()


# ── explorations / discussions / evidence / memory ────────────────────────


def _exploration(
    exploration_id: str, *, status: ExplorationStatus = ExplorationStatus.RUNNING
) -> Exploration:
    return Exploration(
        exploration_id=ExplorationId(exploration_id),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        created_at=NOW,
        status=status,
    )


def _discussion(discussion_id: str) -> Discussion:
    return Discussion.start(
        discussion_id=DiscussionId(discussion_id),
        project_id=PROJECT,
        topic="讨论导出范围",
        participants=(Participant(role_id="dlg.challenger", role_title="反方", joined_round=1),),
        question=None,
        now=NOW,
    )


def test_exploration_discussion_evidence_memory_round_trip_and_owner(conn) -> None:
    SqliteDocumentRepository(conn).save(make_document())
    explorations = SqliteExplorationRepository(conn, leases=_Holds())
    discussions = SqliteDiscussionRepository(conn)
    evidences = SqliteEvidenceRepository(conn)
    memories = SqliteMemoryRepository(conn)

    explorations.save(_exploration(str(EXPLORATION)))
    discussions.save(_discussion(str(DISCUSSION)))
    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.MANUAL,
        text="想做个订单导出的功能",
        source=EvidenceSource(),
        now=NOW,
    )
    evidences.save(ledger)
    memory = ProjectMemory(
        project_id=PROJECT,
        entries=(
            MemoryEntry(
                memory_id="mem1",
                kind=MemoryKind.DECISION,
                text="导出只给运营角色",
                refs=(EvidenceId("E1"),),
                round_id="r-1",
                created_at=NOW,
                updated_at=NOW,
            ),
        ),
    )
    memories.save(memory)

    assert explorations.get(EXPLORATION).created_at == NOW
    assert [item.exploration_id for item in explorations.list_for_project(PROJECT)] == [
        str(EXPLORATION)
    ]
    assert discussions.get(DISCUSSION).topic == "讨论导出范围"
    assert [item.discussion_id for item in discussions.list_for_project(PROJECT)] == [
        str(DISCUSSION)
    ]
    assert [item.eid for item in evidences.get(PROJECT).evidences()] == ["E1"]
    assert memories.get(PROJECT).entries[0].text == "导出只给运营角色"

    with pytest.raises(UnknownExploration):
        explorations.get(EXPLORATION, owner=OTHER)
    with pytest.raises(UnknownDiscussion):
        discussions.get(DISCUSSION, owner=OTHER)
    with pytest.raises(UnknownProject):
        evidences.get(PROJECT, owner=OTHER)
    assert memories.get(PROJECT, owner=OTHER).entries == ()


def test_running_unique_conflict_is_mapped_and_transaction_can_continue(
    conn,
) -> None:
    explorations = SqliteExplorationRepository(conn, leases=_Holds())
    explorations.save(_exploration("e-running-1"))

    with pytest.raises(ExplorationBusy):
        with PgUnitOfWork(conn):
            explorations.save(_exploration("e-running-2"))

    # 冲突后 UoW 已回滚；同一连接的新事务必须能继续（不留在 aborted 状态）。
    explorations.save(_exploration("e-done", status=ExplorationStatus.DONE))
    assert explorations.get(ExplorationId("e-done")).status is ExplorationStatus.DONE


def test_workspace_index_projects_documents_rounds_and_materials(conn) -> None:
    documents = SqliteDocumentRepository(conn)
    rounds = SqliteRoundRepository(conn)
    index = SqliteWorkspaceIndex(conn)

    first_project = ProjectId("p-first")
    second_project = ProjectId("p-second")
    documents.save(_document(first_project, "项目甲", document_id="doc-first"))
    documents.save(_document(second_project, "项目乙", document_id="doc-second"))
    conn.execute(
        "UPDATE documents SET updated_at = %s WHERE project_id = %s",
        (NOW.replace(hour=9), str(first_project)),
    )
    assert [item.title for item in index.list_summaries()] == ["项目乙", "项目甲"]
    assert index.summary(second_project).updated_at.tzinfo is not None

    round_ = _round("r-materials")
    round_.add_materials(
        [
            ser_material_ref("m1", "m1#c00", "E4"),
            ser_material_ref("m1", "m1#c01", "E5"),
        ]
    )
    rounds.save(round_)
    assert index.material_references(PROJECT) == {"m1": ("E4", "E5")}

    conn.execute(
        "UPDATE rounds SET payload = %s WHERE round_id = %s",
        (Jsonb({"user_input": "坏行", "seats": ["x"], "seat_reviews": "bad"}), "r-materials"),
    )
    assert index.recent_opinions(PROJECT) == ()


def _document(project_id: ProjectId, title: str, *, document_id: str):
    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.ids import DocumentId

    return Document.new(
        document_id=DocumentId(document_id),
        project_id=project_id,
        title=title,
        now=NOW,
    )


def ser_material_ref(material_id: str, chunk_id: str, eid: str):
    from pmstudio.domain.round.round import MaterialRef

    return MaterialRef(material_id=material_id, chunk_id=chunk_id, eid=EvidenceId(eid))


def test_unwhen_accepts_json_string_and_row_datetime() -> None:
    assert ser._unwhen(NOW.isoformat()) == NOW
    assert ser._unwhen(NOW) is NOW


# ── 两连接：claim / skip / review ─────────────────────────────────────────


def test_two_connections_claim_only_one_last_web_slot(conn, database: _Database) -> None:
    rounds = SqliteRoundRepository(conn, leases=_Holds())
    round_ = _round("r-claim")
    round_.attach_web_requests(
        [
            _web_request("w1"),
            _web_request("w2"),
        ]
    )
    rounds.save(round_)
    token = _grant_lease(conn, RoundId("r-claim"))

    barrier = threading.Barrier(2)
    results: list[str] = []

    def run(request_id: str) -> None:
        with _second(database) as other:
            tokens = _bind(token)
            try:
                barrier.wait(timeout=5)
                claim_web_request(
                    rounds=SqliteRoundRepository(other, leases=_Holds()),
                    round_writer=SqliteRoundRepository(other, leases=_Holds()),
                    uow=PgUnitOfWork(other),
                    round_id=RoundId("r-claim"),
                    request_id=request_id,
                    limit=1,
                )
                results.append("ok")
            except WebExecutionLimitReached:
                results.append("limit")
            finally:
                _unbind(tokens)

    workers = [
        threading.Thread(target=run, args=("w1",)),
        threading.Thread(target=run, args=("w2",)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert sorted(results) == ["limit", "ok"]
    final = rounds.get(RoundId("r-claim"))
    assert sum(1 for item in final.web_requests if item.state.value == "approved") == 1


def test_skip_and_review_lock_the_same_round_row(conn, database: _Database) -> None:
    rounds = SqliteRoundRepository(conn, leases=_Holds())
    round_ = make_round(seats=(SEAT,))
    rounds.save(round_)
    token = _grant_lease(conn, round_.round_id)

    barrier = threading.Barrier(2)
    results: list[str] = []

    def review() -> None:
        with _second(database) as other:
            tokens = _bind(token)
            try:
                barrier.wait(timeout=5)
                repo = SqliteRoundRepository(other, leases=_Holds())
                with PgUnitOfWork(other):
                    target = repo.get_for_update(round_.round_id)
                    if SEAT in target.skipped_seats:
                        results.append("review_dropped")
                        return
                    target.attest(SeatReview(seat_id=SEAT, basis="交回", opinions=()))
                    repo.save(target)
                results.append("review")
            finally:
                _unbind(tokens)

    def skip() -> None:
        with _second(database) as other:
            tokens = _bind(token)
            try:
                barrier.wait(timeout=5)
                repo = SqliteRoundRepository(other, leases=_Holds())
                with PgUnitOfWork(other):
                    target = repo.get_for_update(round_.round_id)
                    if any(item.seat_id == SEAT for item in target.current_reviews):
                        results.append("skip_rejected")
                        return
                    target.skip_seat(SEAT)
                    repo.record_skip(
                        round_.round_id,
                        RoundSkip(
                            seat_id=SEAT,
                            pass_index=1,
                            reason=user_skip_reason(SEAT),
                            source="user_skipped",
                            requested_at_ms=1,
                        ),
                    )
                results.append("skip")
            finally:
                _unbind(tokens)

    workers = [threading.Thread(target=review), threading.Thread(target=skip)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert set(results) in ({"skip", "review_dropped"}, {"review", "skip_rejected"})
    final = rounds.get(round_.round_id)
    if "skip" in results:
        assert final.skipped_seats == (SEAT,)
        assert final.current_reviews == ()
    else:
        assert final.skipped_seats == ()
        assert [item.seat_id for item in final.current_reviews] == [SEAT]


def _web_request(request_id: str):  # type: ignore[no-untyped-def]
    from pmstudio.domain.round.web import WebRequest

    return WebRequest(
        request_id=request_id,
        query=f"query-{request_id}",
        why="需要行业做法",
        origin="analyzer",
    )


