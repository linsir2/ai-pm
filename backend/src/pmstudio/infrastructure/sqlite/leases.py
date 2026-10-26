"""SQLite 租约存储：轮 / 探索两张表上同形的 lease 三列。

每一条 SQL 都同时带**用户** `owner_id` 与**持有者** `lease_owner`，
再加 `lease_token` 与 `lease_expires_at`——少任何一个，隔离或 fencing 就缺一面：

- `owner_id = ?`：别的用户的同名 target（或猜中的 id）不能抢；
- `lease_owner = ?`：同一用户开两个标签页 / 两个 worker 时区分持有者；
- `lease_token = ?`：过期接管后旧 worker 的迟到写必须失败；
- `lease_expires_at > ?`：token 对但已过期也不能写。

未知或不属于该用户的 target 一律按"资源不在"（`UnknownRound` /
`UnknownExploration`）——不泄漏"这个 id 存在但属于别人"。
"""

from __future__ import annotations

import sqlite3

from pmstudio.application.errors import UnknownExploration, UnknownRound
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken

#: kind → (表名, 主键列)。表名只来自这张白名单，**永不**从调用方拼接。
_TABLES: dict[LeaseKind, tuple[str, str]] = {
    LeaseKind.ROUND: ("rounds", "round_id"),
    LeaseKind.EXPLORATION: ("explorations", "exploration_id"),
}


class SqliteLeaseStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    # —— 内部 ——
    @staticmethod
    def _table(kind: LeaseKind) -> tuple[str, str]:
        try:
            return _TABLES[kind]
        except KeyError:
            raise ValueError(f"未知租约类型：{kind}") from None

    @staticmethod
    def _unknown(kind: LeaseKind, target_id: str) -> Exception:
        if kind is LeaseKind.ROUND:
            return UnknownRound(target_id)
        return UnknownExploration(target_id)

    def _require_target(self, kind: LeaseKind, target_id: str, user_id: UserId) -> None:
        table, key = self._table(kind)
        row = self._connection.execute(
            f"SELECT 1 FROM {table} WHERE {key} = ? AND owner_id = ?",
            (target_id, str(user_id)),
        ).fetchone()
        if row is None:
            raise self._unknown(kind, target_id)

    # —— 端口 ——
    def acquire(
        self,
        kind: LeaseKind,
        target_id: str,
        user_id: UserId,
        holder: str,
        now_ms: int,
        ttl_ms: int,
    ) -> LeaseToken | None:
        self._require_target(kind, target_id, user_id)
        table, key = self._table(kind)
        expires_at = now_ms + ttl_ms
        row = self._connection.execute(
            f"""UPDATE {table}
                SET lease_owner = ?, lease_expires_at = ?, lease_token = lease_token + 1
                WHERE {key} = ? AND owner_id = ?
                  AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)
                RETURNING lease_token""",
            (holder, expires_at, target_id, str(user_id), now_ms),
        ).fetchone()
        if row is None:
            return None
        return LeaseToken(
            kind=kind,
            target_id=target_id,
            user_id=str(user_id),
            holder=holder,
            token=int(row["lease_token"]),
            expires_at_ms=expires_at,
        )

    def renew(
        self,
        kind: LeaseKind,
        target_id: str,
        user_id: UserId,
        holder: str,
        token: int,
        now_ms: int,
        ttl_ms: int,
    ) -> bool:
        table, key = self._table(kind)
        cursor = self._connection.execute(
            f"""UPDATE {table} SET lease_expires_at = ?
                WHERE {key} = ? AND owner_id = ?
                  AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > ?""",
            (now_ms + ttl_ms, target_id, str(user_id), holder, token, now_ms),
        )
        return cursor.rowcount == 1

    def release(
        self,
        kind: LeaseKind,
        target_id: str,
        user_id: UserId,
        holder: str,
        token: int,
    ) -> bool:
        table, key = self._table(kind)
        cursor = self._connection.execute(
            f"""UPDATE {table} SET lease_owner = NULL, lease_expires_at = NULL
                WHERE {key} = ? AND owner_id = ?
                  AND lease_owner = ? AND lease_token = ?""",
            (target_id, str(user_id), holder, token),
        )
        return cursor.rowcount == 1

    def force_acquire(
        self,
        kind: LeaseKind,
        target_id: str,
        user_id: UserId,
        holder: str,
        now_ms: int,
        ttl_ms: int,
    ) -> LeaseToken:
        self._require_target(kind, target_id, user_id)
        table, key = self._table(kind)
        expires_at = now_ms + ttl_ms
        row = self._connection.execute(
            f"""UPDATE {table}
                SET lease_owner = ?, lease_expires_at = ?, lease_token = lease_token + 1
                WHERE {key} = ? AND owner_id = ?
                RETURNING lease_token""",
            (holder, expires_at, target_id, str(user_id)),
        ).fetchone()
        # _require_target 已经确认行存在且属于该用户；这里只防"检查后被删"。
        if row is None:
            raise self._unknown(kind, target_id)
        return LeaseToken(
            kind=kind,
            target_id=target_id,
            user_id=str(user_id),
            holder=holder,
            token=int(row["lease_token"]),
            expires_at_ms=expires_at,
        )

    def holds(self, token: LeaseToken, *, now_ms: int) -> bool:
        table, key = self._table(token.kind)
        row = self._connection.execute(
            f"""SELECT 1 FROM {table}
                WHERE {key} = ? AND owner_id = ?
                  AND lease_owner = ? AND lease_token = ?
                  AND lease_expires_at > ?""",
            (
                token.target_id,
                token.user_id,
                token.holder,
                token.token,
                now_ms,
            ),
        ).fetchone()
        return row is not None
