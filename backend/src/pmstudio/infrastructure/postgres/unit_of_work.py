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

    def __enter__(self) -> PgUnitOfWork:
        if self._depth == 0:
            self._connection.execute("BEGIN")
        else:
            self._connection.execute(f'SAVEPOINT "{self._savepoint()}"')
        self._depth += 1
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self._depth -= 1
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
        if exc_type is None:
            self._connection.execute("COMMIT")
        elif not self._closed():
            # 连接已在错误点回滚并丢弃；再发 ROLLBACK 只会多一次无意义重连。
            self._connection.execute("ROLLBACK")
        return False
