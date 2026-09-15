"""SQLite 连接、迁移与事务。

事务用 `BEGIN IMMEDIATE`：写入前就拿写锁，避免两个写入者互相读到旧值再覆盖（I10）。
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """一个 SQLite 文件。"""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: str | Path) -> "Database":
        connection = sqlite3.connect(str(path), isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        database = cls(connection)
        database._migrate()
        return database

    def _migrate(self) -> None:
        self._connection.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        row = self._connection.execute("SELECT version FROM schema_meta").fetchone()
        if row is None:
            self._connection.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
        elif row["version"] > SCHEMA_VERSION:
            raise RuntimeError(f"库的 schema 版本（{row['version']}）比代码新（{SCHEMA_VERSION}），拒绝打开")

    def schema_version(self) -> int:
        row = self._connection.execute("SELECT version FROM schema_meta").fetchone()
        return int(row["version"]) if row else 0

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection]:
        """写事务。提交之后才能广播事件——见 directory.md §6.6。"""
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except BaseException:
            self._connection.execute("ROLLBACK")
            raise
        else:
            self._connection.execute("COMMIT")

    def close(self) -> None:
        self._connection.close()
