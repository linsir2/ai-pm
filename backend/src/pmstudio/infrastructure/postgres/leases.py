"""PostgreSQL 租约存储：轮 / 探索两张表上同形的 lease 三列。

时间口径只有数据库：`clock_timestamp()` 生成 expiry、比较 expiry；调用方不再传
主机毫秒。这样多个 worker / 多台机器不依赖系统时钟是否对得上，也不存在
"应用算了 now+ttl、库里按另一个 now 判过期"的窗口。

每一条 SQL 都同时带**用户** `owner_id` 与**持有者** `lease_owner`，
再加 `lease_token` 与 `lease_expires_at`——少任何一个，隔离或 fencing 就缺一面：

- `owner_id = %s`：别的用户的同名 target（或猜中的 id）不能抢；
- `lease_owner = %s`：同一用户开两个标签页 / 两个 worker 时区分持有者；
- `lease_token = %s`：过期接管后旧 worker 的迟到写必须失败；
- `lease_expires_at > db_now`：token 对但已过期也不能写。

未知或不属于该用户的 target 一律按"资源不在"（`UnknownRound` /
`UnknownExploration`）——不泄漏"这个 id 存在但属于别人"。
"""

from __future__ import annotations

from typing import Any, Protocol

from pmstudio.application.errors import UnknownExploration, UnknownRound
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken

#: kind → (表名, 主键列)。表名只来自这张白名单，**永不**从调用方拼接。
_TABLES: dict[LeaseKind, tuple[str, str]] = {
    LeaseKind.ROUND: ("rounds", "round_id"),
    LeaseKind.EXPLORATION: ("explorations", "exploration_id"),
}

#: 一条语句内只取一次 DB 时钟：到期时间与比较用同一个毫秒值。
_DB_NOW_MS = "(EXTRACT(EPOCH FROM clock_timestamp()) * 1000)::BIGINT"


class _Executor(Protocol):
    def execute(self, query: str, params: Any = None) -> Any: ...


class PgLeaseStore:
    def __init__(self, connection: _Executor) -> None:
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
            f"SELECT 1 FROM {table} WHERE {key} = %s AND owner_id = %s",
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
        ttl_ms: int,
    ) -> LeaseToken | None:
        self._require_target(kind, target_id, user_id)
        table, key = self._table(kind)
        row = self._connection.execute(
            f"""WITH db_now AS (SELECT {_DB_NOW_MS} AS ms)
                UPDATE {table}
                SET lease_owner = %s,
                    lease_expires_at = db_now.ms + %s,
                    lease_token = lease_token + 1
                FROM db_now
                WHERE {key} = %s AND owner_id = %s
                  AND (lease_owner IS NULL OR lease_expires_at IS NULL
                       OR lease_expires_at <= db_now.ms)
                RETURNING lease_token, lease_expires_at""",
            (holder, ttl_ms, target_id, str(user_id)),
        ).fetchone()
        if row is None:
            return None
        return LeaseToken(
            kind=kind,
            target_id=target_id,
            user_id=str(user_id),
            holder=holder,
            token=int(row["lease_token"]),
            expires_at_ms=int(row["lease_expires_at"]),
        )

    def renew(
        self,
        kind: LeaseKind,
        target_id: str,
        user_id: UserId,
        holder: str,
        token: int,
        ttl_ms: int,
    ) -> int | None:
        """续租成功返回 DB 生成的绝对过期毫秒；0 行返回 `None`（不是 409）。"""

        table, key = self._table(kind)
        row = self._connection.execute(
            f"""WITH db_now AS (SELECT {_DB_NOW_MS} AS ms)
                UPDATE {table}
                SET lease_expires_at = db_now.ms + %s
                FROM db_now
                WHERE {key} = %s AND owner_id = %s
                  AND lease_owner = %s AND lease_token = %s
                  AND lease_expires_at > db_now.ms
                RETURNING lease_expires_at""",
            (ttl_ms, target_id, str(user_id), holder, token),
        ).fetchone()
        return None if row is None else int(row["lease_expires_at"])

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
            f"""WITH db_now AS (SELECT {_DB_NOW_MS} AS ms)
                UPDATE {table} SET lease_owner = NULL, lease_expires_at = NULL
                FROM db_now
                WHERE {key} = %s AND owner_id = %s
                  AND lease_owner = %s AND lease_token = %s
                  AND lease_expires_at > db_now.ms""",
            (target_id, str(user_id), holder, token),
        )
        return cursor.rowcount == 1

    def force_acquire(
        self,
        kind: LeaseKind,
        target_id: str,
        user_id: UserId,
        holder: str,
        ttl_ms: int,
    ) -> LeaseToken:
        self._require_target(kind, target_id, user_id)
        table, key = self._table(kind)
        row = self._connection.execute(
            f"""WITH db_now AS (SELECT {_DB_NOW_MS} AS ms)
                UPDATE {table}
                SET lease_owner = %s,
                    lease_expires_at = db_now.ms + %s,
                    lease_token = lease_token + 1
                FROM db_now
                WHERE {key} = %s AND owner_id = %s
                RETURNING lease_token, lease_expires_at""",
            (holder, ttl_ms, target_id, str(user_id)),
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
            expires_at_ms=int(row["lease_expires_at"]),
        )

    def holds(self, token: LeaseToken) -> bool:
        table, key = self._table(token.kind)
        row = self._connection.execute(
            f"""SELECT 1 FROM {table}, (SELECT {_DB_NOW_MS} AS ms) AS db_now
                WHERE {key} = %s AND owner_id = %s
                  AND lease_owner = %s AND lease_token = %s
                  AND lease_expires_at > db_now.ms""",
            (
                token.target_id,
                token.user_id,
                token.holder,
                token.token,
            ),
        ).fetchone()
        return row is not None
