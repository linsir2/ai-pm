"""SQLite 连接、单实例锁、迁移与事务。

事务用 `BEGIN IMMEDIATE`：写入前就拿写锁，避免两个写入者互相读到旧值再覆盖（I10）。

**单实例锁**用 `flock`：进程死了操作系统自动放锁，不会留下"死锁文件让下次起不来"这种坑。
粒度是**库级**（一个进程一个库）——所有项目都在同一个库里（directory.md §11）。
"""

import fcntl
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from pmstudio.common.errors import SingleInstanceViolation

SCHEMA_VERSION = 3
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    """一个 SQLite 文件，以及它的单实例锁。

    **锁跟着这个对象活**：拿到它就等于占住这个库，丢掉引用（对象被回收）就等于放锁。
    正常路径上 `Runtime` 一直持有它（见 `bootstrap/runtime.py`）。
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._lock: IO[str] | None = None

    @classmethod
    def open(cls, path: str | Path) -> "Database":
        lock = _acquire_lock(path)
        try:
            connection = sqlite3.connect(str(path), isolation_level=None)
        except BaseException:
            lock.close()
            raise
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        database = cls(connection)
        database._lock = lock
        try:
            database._migrate()
        except BaseException:
            database.close()
            raise
        return database

    def _migrate(self) -> None:
        """建表 + 把库版本推到代码版本。

        当前的变更都是"加表"这类可以重复执行的语句，所以 `schema.sql` 本身就是迁移脚本
        （全部 `CREATE ... IF NOT EXISTS`）。将来要改列形状或回填数据时，在这里按版本号加步骤。
        """
        self._connection.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        row = self._connection.execute("SELECT version FROM schema_meta").fetchone()
        if row is None:
            self._connection.execute("INSERT INTO schema_meta (version) VALUES (?)", (SCHEMA_VERSION,))
            return

        stored = int(row["version"])
        if stored > SCHEMA_VERSION:
            raise RuntimeError(f"库的 schema 版本（{stored}）比代码新（{SCHEMA_VERSION}），拒绝打开")
        if stored < SCHEMA_VERSION:
            self._connection.execute("UPDATE schema_meta SET version = ?", (SCHEMA_VERSION,))

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
        if self._lock is not None:
            self._lock.close()
            self._lock = None


def _acquire_lock(path: str | Path) -> IO[str]:
    """拿库锁。拿不到就说清是谁占着——两个进程各写一半比开不起来更糟。"""
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115 - 生命周期与 Database 绑定
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.seek(0)
        holder = handle.read().strip() or "未知进程"
        handle.close()
        raise SingleInstanceViolation(
            f"{lock_path} 已被占用（{holder}）——同一个库只能有一个进程打开"
        ) from error
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid {os.getpid()}")
    handle.flush()
    return handle
