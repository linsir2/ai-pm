"""测试用租约把手：在真 PostgreSQL（过渡期也兼容旧 SQLite 用例）上拿一把锁。

老用例直接调仓储/用例、不走 runner，所以没有 runner 建立的租约。这些用例仍是
有效的仓储/编排证据——补一把"测试持有者"的租约即可，不必把 runner 接进来。
时间由 `PgLeaseStore` 用 DB `clock_timestamp()` 生成，不传主机毫秒。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from psycopg import Connection

from pmstudio.application.lease import LeaseScope, set_lease_scope, wall_clock_ms
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.owner_scope import current_owner_scope
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore

HOLDER = "test-holder"
TTL_MS = 24 * 60 * 60 * 1000


def _acquire(
    connection: Any,
    kind: LeaseKind,
    target_id: object,
    owner: UserId,
    holder: str,
):
    """T6 过渡期：旧 SQLite 用例还拿 sqlite 连接进来；T8/T10 删掉这一支。"""

    if isinstance(connection, sqlite3.Connection):
        return SqliteLeaseStore(connection).acquire(
            kind, str(target_id), owner, holder, wall_clock_ms(), TTL_MS
        )
    return PgLeaseStore(connection).acquire(
        kind, str(target_id), owner, holder, TTL_MS
    )


def _owner(owner: UserId | None) -> UserId:
    resolved = owner or current_owner_scope()
    if resolved is None:
        raise AssertionError("测试没有 owner 上下文")
    return resolved


def bind_round_lease(
    connection: Connection | sqlite3.Connection,
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
    connection: Connection | sqlite3.Connection,
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
