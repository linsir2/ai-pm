"""测试用租约把手：在真 PostgreSQL 上拿一把锁。

老用例直接调仓储/用例、不走 runner，所以没有 runner 建立的租约。这些用例仍是
有效的仓储/编排证据——补一把"测试持有者"的租约即可，不必把 runner 接进来。
时间由 `PgLeaseStore` 用 DB `clock_timestamp()` 生成，不传主机毫秒。
"""

from __future__ import annotations

from psycopg import Connection

from pmstudio.application.lease import LeaseScope, set_lease_scope
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.owner_scope import current_owner_scope

HOLDER = "test-holder"
TTL_MS = 24 * 60 * 60 * 1000


def _acquire(
    connection: Connection,
    kind: LeaseKind,
    target_id: object,
    owner: UserId,
    holder: str,
) -> LeaseToken | None:
    return PgLeaseStore(connection).acquire(
        kind, str(target_id), owner, holder, TTL_MS
    )


def _owner(owner: UserId | None) -> UserId:
    resolved = owner or current_owner_scope()
    if resolved is None:
        raise AssertionError("测试没有 owner 上下文")
    return resolved


def prepare_lease_scope(
    *, owner: UserId | None = None, holder: str = HOLDER
) -> LeaseScope:
    """建新控制行前准备持有者；INSERT 后的 acquire/bind 由真实用例执行。"""

    scope = LeaseScope(owner=_owner(owner), holder=holder)
    set_lease_scope(scope)
    return scope


def bind_round_lease(
    connection: Connection,
    round_id: object,
    *,
    owner: UserId | None = None,
    holder: str = HOLDER,
) -> LeaseToken:
    token = _acquire(connection, LeaseKind.ROUND, round_id, _owner(owner), holder)
    if token is None:
        raise AssertionError(f"测试拿不到轮次租约：{round_id}")
    scope = LeaseScope()
    scope.bind(token)
    set_lease_scope(scope)
    return token


def bind_exploration_lease(
    connection: Connection,
    exploration_id: object,
    *,
    owner: UserId | None = None,
    holder: str = HOLDER,
) -> LeaseToken:
    token = _acquire(
        connection, LeaseKind.EXPLORATION, exploration_id, _owner(owner), holder
    )
    if token is None:
        raise AssertionError(f"测试拿不到探索租约：{exploration_id}")
    scope = LeaseScope()
    scope.bind(token)
    set_lease_scope(scope)
    return token
