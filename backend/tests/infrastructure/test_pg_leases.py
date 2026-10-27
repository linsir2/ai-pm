"""真 PostgreSQL 租约：DB 时钟、fencing、心跳 SQLSTATE 重试。

T6 的口径：

- `acquire`/`renew` 的 expiry 由 `clock_timestamp()` 生成并返回，调用方不再传主机时间；
- `holds()` 与仓储写路径用 DB 时间比较；
- 心跳只把 55P03/40001（以及 40P01）当可重试，其它异常原样抛出；
- 租约语义不依赖 WAL / `BEGIN IMMEDIATE` / `current_timestamp` 借锁。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg.errors import LockNotAvailable, SerializationFailure
from scripts.migrate import apply_migrations

from pmstudio.application.errors import LeaseLost, UnknownExploration, UnknownRound
from pmstudio.application.lease import (
    LeaseHeartbeat,
    LeaseScope,
    clear_lease_scope,
    set_lease_scope,
)
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import ExplorationId, RoundId, UserId
from pmstudio.domain.shared.lease import LeaseKind
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.repositories import (
    SqliteExplorationRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.conftest import TEST_OWNER
from tests.support.builders import NOW, PROJECT
from tests.support.postgres import (
    PgTestDatabase,
    isolated_schema,
    load_environment,
    prepare,
    truncate_data_tables,
)

OWNER = TEST_OWNER
HOLDER = "worker-a"
TTL_MS = 30_000


@pytest.fixture(scope="module")
def database() -> PgTestDatabase:
    pg = isolated_schema(load_environment(), "t6_leases")
    prepare(pg)
    apply_migrations(pg.direct_url)
    return pg


@pytest.fixture()
def conn(database: PgTestDatabase) -> Iterator[object]:
    truncate_data_tables(database)
    with connect(database.direct_url) as connection:
        yield connection
    truncate_data_tables(database)


def _db_now_ms(connection) -> int:  # type: ignore[no-untyped-def]
    row = connection.execute(
        "SELECT (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT AS ms"
    ).fetchone()
    assert row is not None
    return int(row["ms"])


def _seed_round(connection, round_id: str = "r-lease") -> Round:  # type: ignore[no-untyped-def]
    round_ = Round.start(
        round_id=RoundId(round_id),
        project_id=PROJECT,
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    SqliteRoundRepository(connection).save(round_)
    return round_


def _seed_exploration(connection, exploration_id: str = "x-lease") -> Exploration:  # type: ignore[no-untyped-def]
    exploration = Exploration.start(
        exploration_id=ExplorationId(exploration_id),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )
    SqliteExplorationRepository(connection).save(exploration)
    return exploration


def _bind(token) -> None:  # type: ignore[no-untyped-def]
    scope = LeaseScope(holder=token.holder, owner=UserId(token.user_id))
    scope.bind(token)
    set_lease_scope(scope)


def _unbind() -> None:
    clear_lease_scope()


def test_acquire_and_renew_return_database_generated_expiry(conn) -> None:
    _seed_round(conn)
    store = PgLeaseStore(conn)

    before = _db_now_ms(conn)
    token = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None
    assert token.token == 1
    assert before + TTL_MS <= token.expires_at_ms <= _db_now_ms(conn) + TTL_MS
    row = conn.execute(
        "SELECT lease_owner, lease_token, lease_expires_at FROM rounds"
        " WHERE round_id = 'r-lease'"
    ).fetchone()
    assert row == {
        "lease_owner": HOLDER,
        "lease_token": 1,
        "lease_expires_at": token.expires_at_ms,
    }

    renewed = store.renew(
        LeaseKind.ROUND, "r-lease", OWNER, HOLDER, token.token, TTL_MS
    )
    assert renewed is not None
    assert renewed >= token.expires_at_ms

    # renew 返回的 expiry 就是行上的新值；调用方拿它更新本地 scope，不自己算 now+ttl。
    fresh = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, "thief", TTL_MS)
    assert fresh is None
    row = conn.execute(
        "SELECT lease_expires_at FROM rounds WHERE round_id = 'r-lease'"
    ).fetchone()
    assert row is not None and int(row["lease_expires_at"]) == renewed


def test_renew_fences_owner_holder_token_and_expiry(conn) -> None:
    _seed_round(conn)
    store = PgLeaseStore(conn)
    token = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None

    assert (
        store.renew(LeaseKind.ROUND, "r-lease", OWNER, "worker-b", token.token, TTL_MS)
        is None
    )
    assert (
        store.renew(
            LeaseKind.ROUND, "r-lease", UserId("u-other"), HOLDER, token.token, TTL_MS
        )
        is None
    )
    assert (
        store.renew(
            LeaseKind.ROUND, "r-lease", OWNER, HOLDER, token.token + 1, TTL_MS
        )
        is None
    )

    conn.execute(
        "UPDATE rounds SET lease_expires_at ="
        " (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - 1"
        " WHERE round_id = 'r-lease'"
    )
    assert (
        store.renew(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, token.token, TTL_MS)
        is None
    )


def test_release_is_best_effort_and_requires_an_unexpired_lease(conn) -> None:
    _seed_round(conn)
    store = PgLeaseStore(conn)
    token = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None

    assert store.release(LeaseKind.ROUND, "r-lease", OWNER, "worker-b", token.token) is False
    assert store.release(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, token.token) is True
    assert store.release(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, token.token) is False

    again = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, "worker-b", TTL_MS)
    assert again is not None
    assert again.token == token.token + 1

    conn.execute(
        "UPDATE rounds SET lease_expires_at ="
        " (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - 1"
        " WHERE round_id = 'r-lease'"
    )
    assert store.release(LeaseKind.ROUND, "r-lease", OWNER, "worker-b", again.token) is False


def test_force_acquire_takes_over_and_fences_the_old_token(conn) -> None:
    _seed_round(conn)
    store = PgLeaseStore(conn)
    old = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, TTL_MS)
    assert old is not None

    forced = store.force_acquire(LeaseKind.ROUND, "r-lease", OWNER, "abandoner", TTL_MS)
    assert forced.token == old.token + 1
    assert forced.holder == "abandoner"
    assert store.holds(old) is False
    assert store.holds(forced) is True

    round_ = SqliteRoundRepository(conn, leases=store).get(RoundId("r-lease"))
    _bind(old)
    try:
        with pytest.raises(LeaseLost):
            SqliteRoundRepository(conn, leases=store).save(round_)
    finally:
        _unbind()


def test_unknown_and_foreign_targets_are_404(conn) -> None:
    _seed_round(conn)
    _seed_exploration(conn)
    store = PgLeaseStore(conn)
    other = UserId("u-someone-else")

    with pytest.raises(UnknownRound):
        store.acquire(LeaseKind.ROUND, "r-lease", other, HOLDER, TTL_MS)
    with pytest.raises(UnknownRound):
        store.force_acquire(LeaseKind.ROUND, "r-missing", OWNER, HOLDER, TTL_MS)
    with pytest.raises(UnknownExploration):
        store.acquire(LeaseKind.EXPLORATION, "x-missing", OWNER, HOLDER, TTL_MS)

    token = store.acquire(LeaseKind.EXPLORATION, "x-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None
    assert token.kind is LeaseKind.EXPLORATION


def test_holds_and_repository_writes_compare_against_database_time(conn, monkeypatch) -> None:
    round_ = _seed_round(conn)
    store = PgLeaseStore(conn)

    def _host_clock_must_not_be_used() -> int:
        raise AssertionError("租约路径不该读主机时钟")

    monkeypatch.setattr("pmstudio.application.lease.wall_clock_ms", _host_clock_must_not_be_used)
    token = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None
    assert store.holds(token) is True

    _bind(token)
    try:
        SqliteRoundRepository(conn, leases=store).save(round_)
        conn.execute(
            "UPDATE rounds SET lease_expires_at ="
            " (EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT - 1"
            " WHERE round_id = 'r-lease'"
        )
        assert store.holds(token) is False
        with pytest.raises(LeaseLost):
            SqliteRoundRepository(conn, leases=store).save(round_)
    finally:
        _unbind()


def test_scope_ensure_held_is_only_a_local_cancellation_gate() -> None:
    from pmstudio.application.lease import LeaseScope as Scope
    from pmstudio.domain.shared.lease import LeaseToken

    now = 1_000
    scope = Scope()
    with pytest.raises(LeaseLost):
        scope.ensure_held()

    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id="r1",
        user_id=str(OWNER),
        holder=HOLDER,
        token=1,
        expires_at_ms=now + 10,
    )
    scope.bind(token)
    assert scope.ensure_held() is token
    scope.mark_lost()
    with pytest.raises(LeaseLost):
        scope.ensure_held()


async def test_heartbeat_retries_sqlstate_lock_and_serialization() -> None:
    from pmstudio.application.lease import LeaseScope as Scope
    from pmstudio.domain.shared.lease import LeaseToken

    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id="r1",
        user_id=str(OWNER),
        holder=HOLDER,
        token=1,
        expires_at_ms=1_000,
    )
    scope = Scope()
    scope.bind(token)
    delays: list[float] = []
    calls: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    def flaky_renew(current: LeaseToken, ttl_ms: int) -> int:
        calls.append(ttl_ms)
        if len(calls) == 1:
            raise LockNotAvailable("lock timeout")
        if len(calls) == 2:
            raise SerializationFailure("serialization failure")
        return 2_000

    heartbeat = LeaseHeartbeat(
        scope=scope,
        renew=flaky_renew,
        interval_seconds=0.01,
        ttl_ms=5_000,
        on_lost=lambda: None,
        sleep=fake_sleep,
    )

    assert await heartbeat.beat_once() is True
    assert calls == [5_000, 5_000, 5_000]
    assert delays == sorted(delays)
    assert all(delay > 0 for delay in delays)
    assert scope.token is not None and scope.token.expires_at_ms == 2_000
    assert scope.lost is False


async def test_heartbeat_marks_lost_only_when_renew_returns_none() -> None:
    from pmstudio.application.lease import LeaseScope as Scope
    from pmstudio.domain.shared.lease import LeaseToken

    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id="r1",
        user_id=str(OWNER),
        holder=HOLDER,
        token=1,
        expires_at_ms=1_000,
    )
    scope = Scope()
    scope.bind(token)
    lost: list[bool] = []

    heartbeat = LeaseHeartbeat(
        scope=scope,
        renew=lambda current, ttl_ms: None,
        interval_seconds=0.01,
        ttl_ms=5_000,
        on_lost=lambda: lost.append(True),
    )

    assert await heartbeat.beat_once() is False
    assert scope.lost is True
    assert lost == [True]


def test_repo_default_lease_store_is_postgres(conn) -> None:
    """T4 Important 1：PG 仓储默认构造不能再拿 SQLite lease store。"""

    _seed_round(conn)
    store = PgLeaseStore(conn)
    token = store.acquire(LeaseKind.ROUND, "r-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None
    repository = SqliteRoundRepository(conn)
    _bind(token)
    try:
        repository.save(repository.get(RoundId("r-lease")))
    finally:
        _unbind()

    exploration = _seed_exploration(conn)
    leased = PgLeaseStore(conn)
    token = leased.acquire(LeaseKind.EXPLORATION, "x-lease", OWNER, HOLDER, TTL_MS)
    assert token is not None
    repository = SqliteExplorationRepository(conn)
    _bind(token)
    try:
        repository.save(repository.get(exploration.exploration_id))
    finally:
        _unbind()


def _exploration_running_count(conn) -> int:
    row = conn.execute(
        "SELECT count(*) AS n FROM explorations WHERE status = %s",
        (ExplorationStatus.RUNNING.value,),
    ).fetchone()
    assert row is not None
    return int(row["n"])


def test_exploration_save_requires_a_live_db_lease_once_running(conn) -> None:
    exploration = _seed_exploration(conn)
    assert _exploration_running_count(conn) == 1
    exploration.abandon("进程崩了")
    with pytest.raises(LeaseLost):
        with PgUnitOfWork(conn):
            SqliteExplorationRepository(conn).save(exploration)
    # UoW 回滚后同一连接仍可继续
    assert SqliteExplorationRepository(conn).get(exploration.exploration_id).status is (
        ExplorationStatus.RUNNING
    )
