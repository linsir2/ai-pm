"""SQLite 连接：建库、建表、WAL。单用户单进程，一个连接就够。"""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
#: 9 = 删 `inspirations.grounding`（死字段销账）。
USER_VERSION = 9

#: `CREATE TABLE IF NOT EXISTS` 补不了**已有**表的列，所以老库要靠这里的 `ALTER TABLE`（幂等）。
#: 留样表本身是可丢的（`02` §5.3：要清就 DELETE），但**不主动删数据**。
ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "model_calls": (
        ("attempt", "INTEGER NOT NULL DEFAULT 1"),
        ("ok", "INTEGER NOT NULL DEFAULT 1"),
        ("error", "TEXT"),
    )
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    migrate(connection)
    connection.execute(f"PRAGMA user_version = {USER_VERSION}")
    return connection


#: 老库要删的列（销账死字段）。`ALTER TABLE ... DROP COLUMN` 要求 SQLite ≥ 3.35。
DROPPED_COLUMNS: dict[str, tuple[str, ...]] = {
    "observations": ("relevance_count", "last_accessed"),
    "inspirations": ("grounding",),
}


def migrate(connection: sqlite3.Connection) -> None:
    """把老库补到当前形状：加列、删列、不丢业务数据、可重复跑。"""

    for table, columns in ADDED_COLUMNS.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            continue  # 表还没建（schema.sql 会按新形状建），不用补
        for name, definition in columns:
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    for table, columns in DROPPED_COLUMNS.items():
        existing = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not existing:
            continue  # 表还没建，无需删
        for name in columns:
            if name in existing:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
