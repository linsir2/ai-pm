"""PostgreSQL 连接。

连接由 PgBouncer 持有（transaction mode），所以这里**不自建连接池**：
backend 每个 worker 一条连接，`prepare_threshold=None` 关掉预编译语句
（transaction mode 下同一客户端语句可能落到不同后端连接）。

事务由显式 `BEGIN/COMMIT/ROLLBACK` 控制（`autocommit=True` + `PgUnitOfWork`），
因此模型/网络调用必须留在 UoW 外。
"""

from __future__ import annotations

import os
import re
import socket
from contextlib import suppress
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from psycopg import OperationalError, ProgrammingError
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from pmstudio.harness.errors import DatabaseUnavailable

#: 所有连接统一用 UTC；**不覆盖 DSN 自带的 search_path**，只在它后面追加。
UTC_OPTION = "-c timezone=UTC"
CONNECT_TIMEOUT = 5
DEFAULT_APPLICATION_NAME = f"pmstudio-{socket.gethostname()}-{os.getpid()}"

#: 连接类 SQLSTATE：PG 的 08 类，以及运维主动断开连接的三条。
_CONNECTION_SQLSTATES = frozenset({"57P01", "57P02", "57P03"})
_READ_ONLY = re.compile(r"^\s*(?:select|show|values|table)\b", re.IGNORECASE)


def merge_options(existing: str | None, *, required: str = UTC_OPTION) -> str:
    """把 DSN 自带 options 与必选项合并；**保留原值，不覆盖**。

    管理员可能在 URL 里给了 `-csearch_path=tenant,public`。这里只追加
    `-c timezone=UTC`；后面的同名参数由 PostgreSQL 按出现顺序生效，
    所以用户若显式写了 timezone，UTC 仍是最终值（时区口径必须统一）。
    """

    base = (existing or "").strip()
    if not base:
        return required
    return f"{base} {required}"


def options_for(url: str) -> str:
    """读取 URL 里的 options 并合并。密码只解析、不打印。"""

    params = conninfo_to_dict(url)
    return merge_options(str(params.get("options", "")))


def connect(
    url: str,
    *,
    connect_timeout: int = CONNECT_TIMEOUT,
    application_name: str | None = None,
) -> psycopg.Connection[dict[str, Any]]:
    """建一条 autocommit 的 psycopg3 连接（dict row）。

    显式 `options=` 覆盖 DSN 里的 options——值已经在 `options_for` 里合并过，
    所以 search_path 保留、timezone 固定为 UTC。
    """

    connection = psycopg.connect(
        url,
        autocommit=True,
        row_factory=dict_row,
        options=options_for(url),
        prepare_threshold=None,
        application_name=application_name or DEFAULT_APPLICATION_NAME,
        connect_timeout=connect_timeout,
    )
    try:
        register_vector(connection)
    except ProgrammingError:
        # 测试/早期 schema 可能还没有 vector 扩展；连接本身不该因此失败。
        # 真正走向量检索时，调用方会明确报错并退回纯词法。
        pass
    else:
        connection._pmstudio_vector_registered = True
    return connection


def _is_connection_error(error: OperationalError) -> bool:
    """只把连接类错误当成"需要重连"；语句超时/锁超时不是。"""

    sqlstate = getattr(error, "sqlstate", None)
    if sqlstate is None:
        text = str(error).lower()
        return "connection" in text or "server closed" in text
    return sqlstate.startswith("08") or sqlstate in _CONNECTION_SQLSTATES


def _in_transaction(connection: psycopg.Connection[Any]) -> bool:
    """连接当前是否在事务里。

    断开后状态可能读不到；此时按"在事务里"处理更安全——静默重试一个
    已发出但回包丢失的写，比返回 503 更危险。
    """

    try:
        from psycopg.pq import TransactionStatus

        return connection.info.transaction_status != TransactionStatus.IDLE
    except Exception:  # noqa: BLE001 —— 连接状态不可读时按事务内处理
        return True


class PgConnection:
    """一条按需重连的连接把手。

    - **事务外**断连：丢掉坏连接；只读语句重连后安全重放，写语句**不重放**，
      当前调用抛 `DatabaseUnavailable`，下一请求再重连；
    - **事务内**断连 / 主动 close：回滚（尽力）、关连接、抛 `DatabaseUnavailable`
      （503），且**绝不重连**。事务内写是否已提交未知，重放可能重复写。
    """

    def __init__(
        self,
        url: str,
        *,
        connect_timeout: int = CONNECT_TIMEOUT,
        application_name: str | None = None,
    ) -> None:
        self._url = url
        self._connect_timeout = connect_timeout
        self._application_name = application_name
        self._connection: psycopg.Connection[dict[str, Any]] | None = None
        #: UoW 嵌套深度。`close()` 故意不清它：事务内关闭后必须继续拒绝重连，
        #: 直到 UoW 退出把深度减回 0。
        self._transaction_depth = 0

    @property
    def connection(self) -> psycopg.Connection[dict[str, Any]]:
        return self._ensure_connection()

    @property
    def closed(self) -> bool:
        current = self._connection
        return current is None or current.closed or current.broken

    @property
    def transaction_depth(self) -> int:
        return self._transaction_depth

    def enter_transaction(self) -> None:
        self._transaction_depth += 1

    def leave_transaction(self) -> None:
        self._transaction_depth = max(0, self._transaction_depth - 1)

    def _ensure_connection(self) -> psycopg.Connection[dict[str, Any]]:
        current = self._connection
        if current is not None and not current.closed and not current.broken:
            return current
        if self._transaction_depth > 0:
            self._discard()
            raise DatabaseUnavailable(
                "数据库连接在事务中不可用；不会重连，请重试整笔操作",
            )
        self._discard()
        try:
            self._connection = connect(
                self._url,
                connect_timeout=self._connect_timeout,
                application_name=self._application_name,
            )
        except OperationalError as error:
            raise DatabaseUnavailable(
                "连不上数据库；请确认 PostgreSQL / PgBouncer 已启动并检查 PMSTUDIO_DATABASE_URL",
            ) from error
        return self._connection

    def _discard(self) -> None:
        current = self._connection
        self._connection = None
        if current is not None:
            with suppress(Exception):
                current.close()

    def execute(self, query: str, params: Any = None) -> psycopg.Cursor[Any]:
        current = self._ensure_connection()
        # 错误发生后状态可能是 UNKNOWN；执行前记录事务状态与 wrapper 深度。
        inside = self._transaction_depth > 0 or _in_transaction(current)
        try:
            return current.execute(query, params)
        except OperationalError as error:
            if not _is_connection_error(error):
                raise
            if inside:
                with suppress(Exception):
                    current.rollback()
                self._discard()
                raise DatabaseUnavailable(
                    "数据库连接在事务中断开，已回滚；请重试",
                ) from error
            self._discard()
            if not _READ_ONLY.match(query):
                raise DatabaseUnavailable(
                    "数据库连接在写操作中断开；当前写未重放，请重试整笔操作",
                ) from error
            try:
                return self._ensure_connection().execute(query, params)
            except OperationalError as retry_error:
                if not _is_connection_error(retry_error):
                    raise
                self._discard()
                raise DatabaseUnavailable(
                    "数据库连接断开且重连失败；请确认 PostgreSQL / PgBouncer 已恢复",
                ) from retry_error

    def close(self) -> None:
        self._discard()

    def reconnect(self) -> None:
        """主动丢弃当前连接；下一次 `execute` 会重连。"""

        self._discard()

    def __enter__(self) -> PgConnection:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
