"""SQLite 连接：建库、建表、WAL。单用户单进程，一个连接就够。"""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
#: 11 = 删 observations.sections/features/create
#:     at（死字段销账）。
#: 12 = 模板库：`templates` 表（CREATE TABLE IF NOT EXISTS 自建）+
#:     `documents.template` 列（老库 ALTER 补）。
#: 13 = 多用户改造：13 张表补 `owner_id` + 存量回填给首 admin + NULL 熔断。
USER_VERSION = 13

#: v13 起带 `owner_id` 的表：12 张项目域表 + `templates`（裁决 A）。
#: `templates` 的 builtin 行 owner 留 NULL（出厂模板人人可见）——回填与熔断都跳过它们。
#: `users` / `sessions` / `model_calls` 不加列（身份本身不带归属、留样 join 查）。
OWNER_TABLES: tuple[str, ...] = (
    "documents",
    "document_versions",
    "rounds",
    "evidence_ledgers",
    "materials",
    "material_chunks",
    "explorations",
    "discussions",
    "project_memory",
    "observations",
    "round_digests",
    "inspirations",
    "templates",
)

#: `CREATE TABLE IF NOT EXISTS` 补不了**已有**表的列，所以老库要靠这里的 `ALTER TABLE`（幂等）。
#: 留样表本身是可丢的（`02` §5.3：要清就 DELETE），但**不主动删数据**。
ADDED_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "documents": (
        ("template", "TEXT NOT NULL DEFAULT '{}'"),
        ("owner_id", "TEXT"),
    ),
    "document_versions": (("owner_id", "TEXT"),),
    "rounds": (("owner_id", "TEXT"),),
    "evidence_ledgers": (("owner_id", "TEXT"),),
    "materials": (("owner_id", "TEXT"),),
    "material_chunks": (("owner_id", "TEXT"),),
    "explorations": (("owner_id", "TEXT"),),
    "discussions": (("owner_id", "TEXT"),),
    "project_memory": (("owner_id", "TEXT"),),
    "observations": (("owner_id", "TEXT"),),
    "round_digests": (("owner_id", "TEXT"),),
    "inspirations": (("owner_id", "TEXT"),),
    "templates": (("owner_id", "TEXT"),),
    "model_calls": (
        ("attempt", "INTEGER NOT NULL DEFAULT 1"),
        ("ok", "INTEGER NOT NULL DEFAULT 1"),
        ("error", "TEXT"),
    ),
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    migrate(connection)
    assert_owner_coverage(connection)
    connection.execute(f"PRAGMA user_version = {USER_VERSION}")
    return connection


#: 老库要删的列（销账死字段）。`ALTER TABLE ... DROP COLUMN` 要求 SQLite ≥ 3.35。
DROPPED_COLUMNS: dict[str, tuple[str, ...]] = {
    "observations": ("relevance_count", "last_accessed", "sections", "features", "created_at"),
    "inspirations": ("grounding", "context"),
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

    backfill_owners(connection)


def backfill_owners(connection: sqlite3.Connection) -> None:
    """存量行补归属：只填 NULL（幂等），owner = 首个 admin。

    没 admin 就什么都不做——留给 `assert_owner_coverage` 熔断报错，而不是瞎填一个。
    `templates` 的 builtin 行（builtin=1）永不填：出厂模板人人可见。
    """

    admin = connection.execute(
        "SELECT user_id FROM users WHERE role = 'admin' LIMIT 1"
    ).fetchone()
    if admin is None:
        return
    admin_id = admin["user_id"]

    # 存量全归首 admin（§0.6：单用户时代的项目本就是这个人的），逐表直填、只碰 NULL。
    for table in OWNER_TABLES:
        if table == "templates":
            # builtin 行是出厂模板，人人可见——不认领。
            connection.execute(
                "UPDATE templates SET owner_id = ? WHERE owner_id IS NULL AND builtin = 0",
                (admin_id,),
            )
        else:
            connection.execute(
                f"UPDATE {table} SET owner_id = ? WHERE owner_id IS NULL", (admin_id,)
            )


def assert_owner_coverage(connection: sqlite3.Connection) -> None:
    """任一内容表还有 NULL owner 就拒绝启动：缺归属的内容不该悄悄漏出去。

    `templates` 只查自建行——builtin 行留 NULL 是规矩，不是漏网。
    """

    for table in OWNER_TABLES:
        where = (
            "owner_id IS NULL AND builtin = 0"
            if table == "templates"
            else "owner_id IS NULL"
        )
        orphans = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {where}"
        ).fetchone()[0]
        if orphans:
            raise RuntimeError(
                f"{table} 表残留 {orphans} 行 NULL owner，先回填再启动"
            )
