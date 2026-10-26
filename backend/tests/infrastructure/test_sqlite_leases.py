"""SQLite 租约：两连接争用、续租/释放、过期接管、未知 target、仓储 fencing。

全部用**真 SQLite + 真仓储**，时间用固定毫秒（`NOW_MS`）驱动租约表；
仓储 save 的过期判据用真实墙钟，所以这里分两类用例：
- 租约语义（acquire/renew/release/steal）用固定 `now_ms`；
- 仓储 fencing 用「未来过期」或「已过期」的 `now_ms` 造出可判定的行。
"""

from __future__ import annotations

import time

import pytest

from pmstudio.application.errors import LeaseLost, UnknownExploration, UnknownRound
from pmstudio.application.lease import (
    LeaseScope,
    clear_lease_scope,
    current_lease_scope,
    set_lease_scope,
)
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ScopeKind,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import ExplorationId, ProjectId, RoundId, UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.infrastructure.sqlite import database, owner_scope
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteExplorationRepository,
    SqliteRoundRepository,
)
from tests.support.builders import NOW, PROJECT, make_round

NOW_MS = 1_800_000_000_000
TTL_MS = 10_000
HOLDER = "worker-a"


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


@pytest.fixture(autouse=True)
def _lease_scope_isolation():  # type: ignore[no-untyped-def]
    """每个用例从"没有租约"开始，结束后还原——contextvar 不跨用例泄漏。"""

    clear_lease_scope()
    yield
    clear_lease_scope()


@pytest.fixture()
def two_connections(tmp_path):  # type: ignore[no-untyped-def]
    path = tmp_path / "leases.db"
    first = database.connect(path)
    second = database.connect(path)
    owner = owner_scope.current_owner_scope()
    assert owner is not None
    first.execute(
        "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
        " created_at, updated_at) VALUES ('r1', 'p1', ?, 'started', 'none', '{}', ?, ?)",
        (str(owner), NOW.isoformat(), NOW.isoformat()),
    )
    first.execute(
        "INSERT INTO explorations (exploration_id, project_id, owner_id, kind, status,"
        " payload, created_at, updated_at) VALUES ('x1', 'p1', ?, 'inquiry', 'done',"
        " '{}', ?, ?)",
        (str(owner), NOW.isoformat(), NOW.isoformat()),
    )
    yield first, second, owner
    first.close()
    second.close()


def _store(connection) -> SqliteLeaseStore:  # type: ignore[no-untyped-def]
    return SqliteLeaseStore(connection)


def _bind(token: LeaseToken) -> LeaseScope:
    scope = LeaseScope()
    scope.bind(token)
    set_lease_scope(scope)
    return scope


def test_two_connections_race_and_exactly_one_gets_the_round(two_connections) -> None:
    first, second, owner = two_connections

    one = _store(first).acquire(LeaseKind.ROUND, "r1", owner, HOLDER, NOW_MS, TTL_MS)
    two = _store(second).acquire(LeaseKind.ROUND, "r1", owner, "worker-b", NOW_MS, TTL_MS)

    assert one is not None
    assert two is None
    assert (one.kind, one.target_id, one.user_id, one.holder) == (
        LeaseKind.ROUND,
        "r1",
        str(owner),
        HOLDER,
    )
    assert one.expires_at_ms == NOW_MS + TTL_MS
    assert one.token == 1


def test_acquire_is_owner_scoped_and_unknown_targets_are_distinct(
    two_connections,
) -> None:
    first, _, owner = two_connections
    other = UserId("u-someone-else")
    store = _store(first)

    with pytest.raises(UnknownRound):
        store.acquire(LeaseKind.ROUND, "r1", other, HOLDER, NOW_MS, TTL_MS)
    with pytest.raises(UnknownRound):
        store.force_acquire(LeaseKind.ROUND, "r1", other, HOLDER, NOW_MS, TTL_MS)
    with pytest.raises(UnknownRound):
        store.acquire(LeaseKind.ROUND, "r-missing", owner, HOLDER, NOW_MS, TTL_MS)
    with pytest.raises(UnknownExploration):
        store.acquire(LeaseKind.EXPLORATION, "x-missing", owner, HOLDER, NOW_MS, TTL_MS)

    # 别人的 acquire 没把行抢走：本 owner 仍然能拿到第一把锁
    token = store.acquire(LeaseKind.ROUND, "r1", owner, HOLDER, NOW_MS, TTL_MS)
    assert token is not None
    assert token.token == 1


def test_renew_requires_the_same_owner_holder_and_token(two_connections) -> None:
    first, second, owner = two_connections
    token = _store(first).acquire(LeaseKind.ROUND, "r1", owner, HOLDER, NOW_MS, TTL_MS)
    assert token is not None

    assert _store(first).renew(
        LeaseKind.ROUND, "r1", owner, HOLDER, token.token, NOW_MS + 1, TTL_MS
    )
    assert not _store(first).renew(
        LeaseKind.ROUND, "r1", owner, "worker-b", token.token, NOW_MS + 1, TTL_MS
    )
    assert not _store(first).renew(
        LeaseKind.ROUND, "r1", UserId("u-other"), HOLDER, token.token, NOW_MS + 1, TTL_MS
    )
    assert not _store(second).renew(
        LeaseKind.ROUND, "r1", owner, HOLDER, token.token + 1, NOW_MS + 1, TTL_MS
    )
    # 过期之后不能再续租（0 行，不抛）
    assert not _store(first).renew(
        LeaseKind.ROUND, "r1", owner, HOLDER, token.token, NOW_MS + TTL_MS + 1, TTL_MS
    )


def test_release_is_best_effort_and_never_raises_on_zero_rows(two_connections) -> None:
    first, second, owner = two_connections
    store = _store(first)
    token = store.acquire(LeaseKind.ROUND, "r1", owner, HOLDER, NOW_MS, TTL_MS)
    assert token is not None

    assert store.release(LeaseKind.ROUND, "r1", owner, HOLDER, token.token)
    # 再释放一次 = 0 行：不报 409、不抛，只是 False
    assert store.release(LeaseKind.ROUND, "r1", owner, HOLDER, token.token) is False
    # 错 holder / 错 owner 也是 0 行
    assert store.release(LeaseKind.ROUND, "r1", owner, "worker-b", token.token) is False
    assert (
        store.release(LeaseKind.ROUND, "r1", UserId("u-other"), HOLDER, token.token)
        is False
    )

    # 释放后下一个 acquire 拿到新 token（只增不减）
    again = _store(second).acquire(LeaseKind.ROUND, "r1", owner, "worker-b", NOW_MS, TTL_MS)
    assert again is not None
    assert again.token == token.token + 1


def test_expired_lease_is_stolen_and_old_token_cannot_save(two_connections) -> None:
    first, second, owner = two_connections
    old = _store(first).acquire(LeaseKind.ROUND, "r1", owner, HOLDER, NOW_MS, TTL_MS)
    assert old is not None

    new = _store(second).acquire(
        LeaseKind.ROUND, "r1", owner, "worker-b", NOW_MS + TTL_MS, TTL_MS
    )
    assert new is not None
    assert new.token == old.token + 1

    rounds = SqliteRoundRepository(first)
    _bind(old)
    with pytest.raises(LeaseLost):
        rounds.save(make_round())

    # 行仍然归新 token，旧 worker 的失败没有把 lease_owner 改回去
    row = first.execute(
        "SELECT lease_owner, lease_token FROM rounds WHERE round_id = 'r1'"
    ).fetchone()
    assert (row["lease_owner"], row["lease_token"]) == ("worker-b", new.token)


def test_matching_token_but_expired_row_is_rejected_by_save(two_connections) -> None:
    first, _, owner = two_connections
    past = _now_ms() - TTL_MS * 2
    token = _store(first).acquire(LeaseKind.ROUND, "r1", owner, HOLDER, past, TTL_MS)
    assert token is not None
    assert token.expires_at_ms <= _now_ms()

    _bind(token)
    with pytest.raises(LeaseLost):
        SqliteRoundRepository(first).save(make_round())


def test_force_acquire_takes_over_and_fences_the_old_token(two_connections) -> None:
    first, second, owner = two_connections
    old = _store(first).acquire(LeaseKind.ROUND, "r1", owner, HOLDER, NOW_MS, TTL_MS)
    assert old is not None

    forced = _store(second).force_acquire(
        LeaseKind.ROUND, "r1", owner, "abandoner", NOW_MS, TTL_MS
    )
    assert forced.token == old.token + 1
    assert forced.holder == "abandoner"

    _bind(old)
    with pytest.raises(LeaseLost):
        SqliteRoundRepository(first).save(make_round())


def test_exploration_lease_uses_the_same_owner_and_holder_contract(
    two_connections,
) -> None:
    first, second, owner = two_connections
    store = _store(first)
    token = store.acquire(LeaseKind.EXPLORATION, "x1", owner, HOLDER, NOW_MS, TTL_MS)
    assert token is not None
    assert token.kind is LeaseKind.EXPLORATION
    assert (
        _store(second).acquire(
            LeaseKind.EXPLORATION, "x1", owner, "worker-b", NOW_MS, TTL_MS
        )
        is None
    )
    assert store.renew(
        LeaseKind.EXPLORATION, "x1", owner, HOLDER, token.token, NOW_MS + 1, TTL_MS
    )
    assert store.release(LeaseKind.EXPLORATION, "x1", owner, HOLDER, token.token)


def test_scope_ensure_held_tracks_bind_expiry_loss_and_clear() -> None:
    scope = LeaseScope()
    with pytest.raises(LeaseLost):
        scope.ensure_held(now_ms=NOW_MS)

    token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id="r1",
        user_id="u1",
        holder=HOLDER,
        token=1,
        expires_at_ms=NOW_MS + TTL_MS,
    )
    scope.bind(token)
    set_lease_scope(scope)
    assert current_lease_scope() is scope
    assert scope.ensure_held(now_ms=NOW_MS + 1) == token
    with pytest.raises(LeaseLost):
        scope.ensure_held(now_ms=NOW_MS + TTL_MS)

    scope.bind(token)
    scope.mark_lost()
    with pytest.raises(LeaseLost):
        scope.ensure_held(now_ms=NOW_MS)

    scope.bind(token)
    scope.clear()
    with pytest.raises(LeaseLost):
        scope.ensure_held(now_ms=NOW_MS)


def test_new_row_insert_is_allowed_but_updates_need_a_live_lease(two_connections) -> None:
    first, _, owner = two_connections
    rounds = SqliteRoundRepository(first)
    round_ = Round(
        round_id=RoundId("r-new"),
        project_id=PROJECT,
        user_input="新的一轮",
        created_at=NOW,
    )
    rounds.save(round_)  # 新行 INSERT：没有租约也允许

    with pytest.raises(LeaseLost):
        rounds.save(round_)  # 无 token 的 existing UPDATE

    token = _store(first).acquire(
        LeaseKind.ROUND, "r-new", owner, HOLDER, _now_ms(), TTL_MS
    )
    assert token is not None
    _bind(token)
    round_.abandon("用户放弃")
    rounds.save(round_)  # 匹配租约：放行

    assert rounds.get(RoundId("r-new")).step.value == "abandoned"

    _store(first).release(LeaseKind.ROUND, "r-new", owner, HOLDER, token.token)
    with pytest.raises(LeaseLost):
        rounds.save(round_)  # 释放后的迟到 save


def test_a_lease_for_another_target_or_kind_does_not_fence_this_row(
    two_connections,
) -> None:
    first, _, owner = two_connections
    rounds = SqliteRoundRepository(first)
    round_ = Round(
        round_id=RoundId("r-new"),
        project_id=PROJECT,
        user_input="新的一轮",
        created_at=NOW,
    )
    rounds.save(round_)

    # 拿着别的 target 的租约来写这一轮 = 没有租约
    other = _store(first).acquire(LeaseKind.ROUND, "r1", owner, HOLDER, _now_ms(), TTL_MS)
    assert other is not None
    _bind(other)
    with pytest.raises(LeaseLost):
        rounds.save(round_)

    exploration = _store(first).acquire(
        LeaseKind.EXPLORATION, "x1", owner, HOLDER, _now_ms(), TTL_MS
    )
    assert exploration is not None
    _bind(exploration)
    with pytest.raises(LeaseLost):
        rounds.save(round_)


def test_exploration_save_is_fenced_too(two_connections) -> None:
    first, _, owner = two_connections
    explorations = SqliteExplorationRepository(first)
    run = Exploration.start(
        exploration_id=ExplorationId("x-new"),
        project_id=ProjectId("p1"),
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )
    explorations.save(run)  # INSERT 允许

    with pytest.raises(LeaseLost):
        explorations.save(run)

    token = _store(first).acquire(
        LeaseKind.EXPLORATION, "x-new", owner, HOLDER, _now_ms(), TTL_MS
    )
    assert token is not None
    _bind(token)
    run.complete(hypotheses=(), blind_spots=())
    explorations.save(run)
    assert explorations.get(ExplorationId("x-new")).status.value == "done"
