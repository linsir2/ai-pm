"""一个用例一个事务：正常退出提交，抛异常回滚。

`isolation_level=None` 意味着 autocommit，事务由这里显式开——
所以**读操作不开事务**，写入必须包在 `with uow:` 里。

**可嵌套**：用例里调领域服务（例如手改文档时 `EditEntry` 调 `ProposalCommitter`）
不许开出第二个事务——只有最外层那次 `BEGIN`，也只有最外层提交或回滚。
"""

import sqlite3
from types import TracebackType


class SqliteUnitOfWork:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._depth = 0

    def __enter__(self) -> "SqliteUnitOfWork":
        if self._depth == 0:
            self._connection.execute("BEGIN IMMEDIATE")
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
            # 内层不决定成败：异常照旧往上抛，由最外层提交或回滚。
            return False
        if exc_type is None:
            self._connection.execute("COMMIT")
        else:
            self._connection.execute("ROLLBACK")
        return False
