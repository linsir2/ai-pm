"""PostgreSQL 事务边界：正常退出 COMMIT，抛异常 ROLLBACK。

连接是 autocommit（`connect(..., autocommit=True)`），所以事务由这里显式开。
读操作不开事务，写入必须包在 `with uow:` 里。

**可嵌套**：内层用 SAVEPOINT；只有最外层真正 COMMIT / ROLLBACK。
内层异常先回滚到自己的 savepoint，再把异常交给外层——这样外层即使接住
内层异常，事务也不会留下半截写入。
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol


class _Executor(Protocol):
    def execute(self, query: str, params: Any = None) -> Any: ...


class PgUnitOfWork:
    def __init__(self, connection: _Executor) -> None:
        self._connection = connection
        self._depth = 0

    def _savepoint(self) -> str:
        return f"pmstudio_uow_{self._depth}"

    def _closed(self) -> bool:
        return bool(getattr(self._connection, "closed", False))

    def _enter_transaction(self) -> None:
        marker = getattr(self._connection, "enter_transaction", None)
        if callable(marker):
            marker()

    def _leave_transaction(self) -> None:
        marker = getattr(self._connection, "leave_transaction", None)
        if callable(marker):
            marker()

    def __enter__(self) -> PgUnitOfWork:
        if self._depth == 0:
            self._connection.execute("BEGIN")
        else:
            self._connection.execute(f'SAVEPOINT "{self._savepoint()}"')
        self._enter_transaction()
        self._depth += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._depth -= 1
        try:
            if self._depth > 0:
                if not self._closed():
                    if exc_type is None:
                        self._connection.execute(
                            f'RELEASE SAVEPOINT "{self._savepoint()}"'
                        )
                    else:
                        self._connection.execute(
                            f'ROLLBACK TO SAVEPOINT "{self._savepoint()}"'
                        )
                        self._connection.execute(
                            f'RELEASE SAVEPOINT "{self._savepoint()}"'
                        )
                # 内层不决定整笔成败：异常继续往上抛，由最外层回滚。
                return False
            if self._closed():
                if exc_type is None:
                    # 连接被关闭却想正常提交：不能假装成功。走 execute 统一报 503。
                    self._connection.execute("COMMIT")
            elif exc_type is None:
                self._connection.execute("COMMIT")
            else:
                self._connection.execute("ROLLBACK")
            return False
        finally:
            self._leave_transaction()
