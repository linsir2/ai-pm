"""一个用例一个事务：正常退出提交，抛异常回滚。

`isolation_level=None` 意味着 autocommit，事务由这里显式开——
所以**读操作不开事务**，写入必须包在 `with uow:` 里。
"""

import sqlite3
from types import TracebackType


class SqliteUnitOfWork:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> "SqliteUnitOfWork":
        self._connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc_type is None:
            self._connection.execute("COMMIT")
        else:
            self._connection.execute("ROLLBACK")
        return False
