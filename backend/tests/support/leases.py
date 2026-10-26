"""测试用租约把手：在真 SQLite 上拿一把锁并绑进当前 contextvar。

Task 1 之前的老用例直接调仓储/用例、不走 runner，所以没有 runner 建立的租约。
这些用例仍是有效的仓储/编排证据——补一把"测试持有者"的租约即可，
不必把 runner 接进来（那是 Task 2/3 的事）。
"""

from __future__ import annotations

import sqlite3

from pmstudio.application.lease import LeaseScope, set_lease_scope, wall_clock_ms
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore
from pmstudio.infrastructure.sqlite.owner_scope import current_owner_scope

HOLDER = "test-holder"
TTL_MS = 24 * 60 * 60 * 1000


def _owner(owner: UserId | None) -> UserId:
    resolved = owner or current_owner_scope()
    if resolved is None:
        raise AssertionError("测试没有 owner 上下文")
    return resolved


def bind_round_lease(
    connection: sqlite3.Connection,
    round_id: object,
    *,
    owner: UserId | None = None,
    holder: str = HOLDER,
) -> LeaseToken:
    token = SqliteLeaseStore(connection).acquire(
        LeaseKind.ROUND, str(round_id), _owner(owner), holder, wall_clock_ms(), TTL_MS
    )
    if token is None:
        raise AssertionError(f"测试拿不到轮次租约：{round_id}")
    scope = LeaseScope()
    scope.bind(token)
    set_lease_scope(scope)
    return token


def bind_exploration_lease(
    connection: sqlite3.Connection,
    exploration_id: object,
    *,
    owner: UserId | None = None,
    holder: str = HOLDER,
) -> LeaseToken:
    token = SqliteLeaseStore(connection).acquire(
        LeaseKind.EXPLORATION,
        str(exploration_id),
        _owner(owner),
        holder,
        wall_clock_ms(),
        TTL_MS,
    )
    if token is None:
        raise AssertionError(f"测试拿不到探索租约：{exploration_id}")
    scope = LeaseScope()
    scope.bind(token)
    set_lease_scope(scope)
    return token

