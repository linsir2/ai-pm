"""真 PostgreSQL：探索 running 唯一冲突、僵壳清理、放弃接管与旧 token fencing。"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from scripts.migrate import apply_migrations

from pmstudio.application.errors import ExplorationBusy, LeaseLost
from pmstudio.application.lease import LeaseScope, clear_lease_scope, set_lease_scope
from pmstudio.application.use_cases import AbandonExploration, RetryExploration, RunInquiry
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.shared.ids import ExplorationId
from pmstudio.domain.shared.lease import LeaseKind
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    set_owner_scope,
)
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDocumentRepository,
    SqliteExplorationRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.conftest import TEST_OWNER
from tests.support.builders import NOW, PROJECT, document_with_feature
from tests.support.doubles import FixedClock, RecordingPublisher
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
)

OWNER = TEST_OWNER
TTL_MS = 60_000


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


def _seed_project(connection) -> None:  # type: ignore[no-untyped-def]
    SqliteDocumentRepository(connection).save(document_with_feature())


def _running(exploration_id: str) -> Exploration:
    return Exploration.start(
        exploration_id=ExplorationId(exploration_id),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )


def _failed(exploration_id: str) -> Exploration:
    exploration = _running(exploration_id)
    exploration.fail("上一次没跑完")
    return exploration


def _inquiry(connection) -> RunInquiry:  # type: ignore[no-untyped-def]
    explorations = SqliteExplorationRepository(connection)
    return RunInquiry(
        documents=SqliteDocumentRepository(connection),
        explorations=explorations,
        exploration_writer=explorations,
        evidence=None,
        memory=None,
        uow=PgUnitOfWork(connection),
        inquirer=None,
        checker=None,
        clock=FixedClock(),
        ids=UuidIdGenerator(),
        events=RecordingPublisher(),
        leases=PgLeaseStore(connection),
    )


def _running_count(connection) -> int:  # type: ignore[no-untyped-def]
    row = connection.execute(
        "SELECT count(*) AS n FROM explorations WHERE status = %s",
        (ExplorationStatus.RUNNING.value,),
    ).fetchone()
    assert row is not None
    return int(row["n"])


async def test_two_creates_race_and_only_one_running_row_survives(
    conn, database: _Database
) -> None:
    _seed_project(conn)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def run() -> None:
        with _second(database) as other:
            set_owner_scope(OWNER)
            try:
                barrier.wait(timeout=5)
                asyncio.run(
                    _inquiry(other).create(
                        project_id=PROJECT,
                        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
                        owner=OWNER,
                    )
                )
                results.append("ok")
            except ExplorationBusy:
                results.append("busy")
            finally:
                clear_owner_scope()

    workers = [threading.Thread(target=run), threading.Thread(target=run)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert sorted(results) == ["busy", "ok"]
    assert _running_count(conn) == 1
    # 冲突方整笔回滚：库里只有胜者那一行，没有第二条孤儿 running。
    row = conn.execute("SELECT count(*) AS n FROM explorations").fetchone()
    assert row is not None and int(row["n"]) == 1

    with pytest.raises(ExplorationBusy):
        await _inquiry(conn).create(
            project_id=PROJECT,
            scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
            owner=OWNER,
        )
    assert _running_count(conn) == 1


async def test_resume_update_hitting_another_running_row_is_busy_and_rolls_back(
    conn,
) -> None:
    _seed_project(conn)
    explorations = SqliteExplorationRepository(conn)
    explorations.save(_running("x-live"))
    explorations.save(_failed("x-failed"))

    retry = RetryExploration(
        explorations=explorations,
        exploration_writer=explorations,
        uow=PgUnitOfWork(conn),
        clock=FixedClock(),
        events=RecordingPublisher(),
        leases=PgLeaseStore(conn),
    )
    with pytest.raises(ExplorationBusy):
        await retry.resume(ExplorationId("x-failed"), owner=OWNER)

    assert explorations.get(ExplorationId("x-failed")).status is ExplorationStatus.FAILED
    assert explorations.get(ExplorationId("x-live")).status is ExplorationStatus.RUNNING
    # 冲突事务已回滚；同一连接的下一次事务必须能继续（不留在 aborted 状态）。
    assert _running_count(conn) == 1


async def test_clear_stale_abandons_expired_and_orphan_leases_but_keeps_live(
    conn,
) -> None:
    _seed_project(conn)
    explorations = SqliteExplorationRepository(conn)
    leases = PgLeaseStore(conn)

    # running 唯一索引保证同一项目同时只有一条；三种情形顺序验证。
    explorations.save(_running("x-expired"))
    assert (
        leases.acquire(LeaseKind.EXPLORATION, "x-expired", OWNER, "dead", TTL_MS)
        is not None
    )
    conn.execute(
        "UPDATE explorations SET lease_expires_at ="
        " (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - 1"
        " WHERE exploration_id = 'x-expired'"
    )
    await _inquiry(conn).clear_stale(PROJECT, owner=OWNER)
    assert explorations.get(ExplorationId("x-expired")).status is ExplorationStatus.ABANDONED
    assert conn.execute(
        "SELECT lease_owner FROM explorations WHERE exploration_id = 'x-expired'"
    ).fetchone() == {"lease_owner": None}

    # 老库遗留的 running 行没有 lease_expires_at：同样按僵壳清理。
    explorations.save(_running("x-orphan"))
    await _inquiry(conn).clear_stale(PROJECT, owner=OWNER)
    assert explorations.get(ExplorationId("x-orphan")).status is ExplorationStatus.ABANDONED

    # 活租约不动：不抢、不改状态，让 create/resume 在唯一索引上给 409。
    explorations.save(_running("x-live"))
    assert (
        leases.acquire(LeaseKind.EXPLORATION, "x-live", OWNER, "alive", TTL_MS)
        is not None
    )
    await _inquiry(conn).clear_stale(PROJECT, owner=OWNER)
    assert explorations.get(ExplorationId("x-live")).status is ExplorationStatus.RUNNING
    assert conn.execute(
        "SELECT lease_owner FROM explorations WHERE exploration_id = 'x-live'"
    ).fetchone() == {"lease_owner": "alive"}
    assert _running_count(conn) == 1


async def test_abandon_force_acquires_releases_and_fences_the_old_worker(conn) -> None:
    _seed_project(conn)
    explorations = SqliteExplorationRepository(conn)
    explorations.save(_running("x-abandon"))
    old_token = PgLeaseStore(conn).acquire(
        LeaseKind.EXPLORATION, "x-abandon", OWNER, "old-worker", TTL_MS
    )
    assert old_token is not None
    old_view = explorations.get(ExplorationId("x-abandon"))
    scope = LeaseScope(holder="old-worker", owner=OWNER)
    scope.bind(old_token)
    context = set_lease_scope(scope)

    class _Canceller:
        async def cancel(self, exploration_id: object) -> None:
            return None

    try:
        await AbandonExploration(
            explorations=explorations,
            exploration_writer=explorations,
            uow=PgUnitOfWork(conn),
            clock=FixedClock(),
            events=RecordingPublisher(),
            canceller=_Canceller(),
            leases=PgLeaseStore(conn),
        )(exploration_id=ExplorationId("x-abandon"), owner=OWNER)

        assert (
            explorations.get(ExplorationId("x-abandon")).status
            is ExplorationStatus.ABANDONED
        )
        assert conn.execute(
            "SELECT lease_owner FROM explorations WHERE exploration_id = 'x-abandon'"
        ).fetchone() == {"lease_owner": None}

        with pytest.raises(LeaseLost):
            with PgUnitOfWork(conn):
                explorations.save(old_view)
    finally:
        clear_lease_scope(context)
