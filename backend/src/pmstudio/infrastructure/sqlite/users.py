"""users / sessions 表仓储（多用户改造 Task 2，PostgreSQL）。

安全口径：

- **会话 token 明文永不入库**：`sessions` 只有 `token_hash` 列，仓储的方法也只收
  摘要——"明文进不来"是结构保证，不靠调用方自觉；明文由认证用例在写 cookie 时算掉。
- 密码学不在这里：`password_hash` 是 Task 1 `infrastructure/security.py` 产出的字符串，
  仓储只存 / 取，不做 argon2。
- SQL 全参化（psycopg `%s`）。

身份进引擎只以 `UserId` 值对象；这些是仓储的读投影，不是领域聚合，所以类型定义在
application 的端口文件里（`UserRecord` / `SessionRecord`）。
"""

import uuid
from datetime import UTC, datetime

from psycopg import Connection

from pmstudio.application.ports import SessionRecord, UserRecord
from pmstudio.domain.shared.ids import UserId


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


class SqliteUserRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def register(
        self, *, username: str, password_hash: str, role: str = "user"
    ) -> UserId:
        """造一个 pending 用户。重名由 `username` 的 UNIQUE 约束挡住（抛 UniqueViolation）。"""

        user_id = UserId(_new_id("u"))
        self._connection.execute(
            """
            INSERT INTO users (user_id, username, password_hash, status, role, created_at)
            VALUES (%s, %s, %s, 'pending', %s, %s)
            """,
            (
                str(user_id),
                username,
                password_hash,
                role,
                _now(),
            ),
        )
        return user_id

    def get_by_name(self, username: str) -> UserRecord | None:
        row = self._connection.execute(
            "SELECT * FROM users WHERE username = %s", (username,)
        ).fetchone()
        return None if row is None else self._from_row(row)

    def get_by_id(self, user_id: UserId) -> UserRecord | None:
        row = self._connection.execute(
            "SELECT * FROM users WHERE user_id = %s", (str(user_id),)
        ).fetchone()
        return None if row is None else self._from_row(row)

    def list_all(self) -> tuple[UserRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM users ORDER BY created_at, seq"
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    def set_status(
        self,
        user_id: UserId,
        status: str,
        *,
        approved_by: str | None = None,
        approved_at: datetime | None = None,
    ) -> None:
        self._connection.execute(
            """
            UPDATE users
            SET status = %s, approved_by = %s, approved_at = %s
            WHERE user_id = %s
            """,
            (
                status,
                approved_by,
                approved_at,
                str(user_id),
            ),
        )

    def list_pending(self) -> tuple[UserRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM users WHERE status = 'pending' ORDER BY created_at, seq"
        ).fetchall()
        return tuple(self._from_row(row) for row in rows)

    @staticmethod
    def _from_row(row: dict) -> UserRecord:
        return UserRecord(
            user_id=UserId(row["user_id"]),
            username=row["username"],
            password_hash=row["password_hash"],
            status=row["status"],
            role=row["role"],
            approved_by=row["approved_by"],
            approved_at=row["approved_at"],
            created_at=row["created_at"],
        )


class SqliteSessionRepository:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def create(
        self,
        *,
        user_id: UserId,
        token_hash: str,
        expires_at: datetime,
        now: datetime,
        ip: str = "",
        user_agent: str = "",
    ) -> SessionRecord:
        session_id = _new_id("s")
        self._connection.execute(
            """
            INSERT INTO sessions (session_id, user_id, token_hash, expires_at,
                                  created_at, last_seen_at, ip, user_agent)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                session_id,
                str(user_id),
                token_hash,
                expires_at,
                now,
                now,
                ip,
                user_agent,
            ),
        )
        return SessionRecord(
            session_id=session_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
            last_seen_at=now,
            ip=ip,
            user_agent=user_agent,
            revoked_at=None,
        )

    def get_by_token_hash(self, token_hash: str) -> SessionRecord | None:
        """按摘要取**未被吊销**的会话。过期与否由调用方按 `expires_at` 判。"""

        row = self._connection.execute(
            "SELECT * FROM sessions WHERE token_hash = %s AND revoked_at IS NULL",
            (token_hash,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def touch(self, token_hash: str, *, now: datetime, expires_at: datetime) -> None:
        """续期：同时推进 `last_seen_at` 与 `expires_at`。已吊销的会话不动（静默）。"""

        self._connection.execute(
            """
            UPDATE sessions
            SET last_seen_at = %s, expires_at = %s
            WHERE token_hash = %s AND revoked_at IS NULL
            """,
            (
                now,
                expires_at,
                token_hash,
            ),
        )

    def revoke_by_token_hash(self, token_hash: str) -> None:
        """按单条会话吊销（登出 / 绝对上限命中）。已吊销的不再动，其余会话不受影响。"""

        self._connection.execute(
            "UPDATE sessions SET revoked_at = %s WHERE token_hash = %s AND revoked_at IS NULL",
            (_now(), token_hash),
        )

    def revoke_user(self, user_id: UserId) -> None:
        """吊销这个用户的全部活会话（封禁 = 踢全部）。已吊销的不再动。"""

        self._connection.execute(
            "UPDATE sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL",
            (_now(), str(user_id)),
        )

    @staticmethod
    def _from_row(row: dict) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            user_id=UserId(row["user_id"]),
            token_hash=row["token_hash"],
            expires_at=row["expires_at"],
            created_at=row["created_at"],
            last_seen_at=row["last_seen_at"],
            ip=row["ip"],
            user_agent=row["user_agent"],
            revoked_at=row["revoked_at"],
        )
