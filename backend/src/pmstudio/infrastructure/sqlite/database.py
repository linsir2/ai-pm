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
    # 90 天绝对上限的锚点，是加会话表**之后**才补的列（`schema.sql` 的
    # `CREATE TABLE IF NOT EXISTS` 补不了已有表）。跑过早期版本的库缺它 → 登录第一条
    # INSERT 就炸 → 登录必 500。老行在下面 `migrate()` 里回填，不留 NULL。
    "sessions": (("created_at", "TEXT"),),
}


def _enable_wal(connection: sqlite3.Connection) -> None:
    """切到 WAL。**多进程下这一步本身会争用**（高并发 P4 真跑抓到的）。

    `PRAGMA journal_mode = WAL` 要短暂持排他锁，而它**不吃 `timeout=10` 的
    busy 等待**——两个 worker 同时启动、都在一个新库上执行，直接
    `database is locked`，**两个都起不来**。

    判据很直接：**目标达成就行**。切完之后再读一次，如果已经是 `wal` 就没事；
    真的切不过去（别的原因）才抛。
    """
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        current = connection.execute("PRAGMA journal_mode").fetchone()[0]
        if str(current).lower() != "wal":
            raise
    return


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # 显式 `timeout=10.0`（多用户改造 §0.9）：Python 默认 5.0 是隐式的，写出来是为了
    # "并发写等锁最多等多久"这件事有个可见的数；单 worker + 短事务是前提，见 `scripts/dev.py`。
    connection = sqlite3.connect(
        db_path, isolation_level=None, check_same_thread=False, timeout=10.0
    )
    connection.row_factory = sqlite3.Row
    _enable_wal(connection)
    connection.execute("PRAGMA foreign_keys = ON")
    # **先补列、再建 schema**（多用户改造 Task 5）：`schema.sql` 里有
    # `CREATE INDEX ... ON <表>(owner_id)`，老库那一刻还没这列——`CREATE TABLE IF NOT
    # EXISTS` 补不了已有表的列，只有 `migrate()` 的 `ALTER TABLE` 能。放在 schema 之后
    # 建索引就会在老库上炸「no such column: owner_id」。新库两边都幂等，不受影响。
    migrate(connection)
    connection.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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

    # 补完列再填值：`sessions.created_at` 是绝对上限的锚点，NULL 会让 `GetMe` 的
    # datetime 减法炸成 500。老行拿 `last_seen_at` 顶——那时能拿到的最接近建会话的时刻。
    # 幂等：只碰 NULL，新会话由 INSERT 自己写。老库可能压根还没建会话表（`schema.sql`
    # 会按新形状建，那时新表自带 NOT NULL 的 created_at），所以先问表在不在。
    if any(
        row["name"] == "sessions"
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    ):
        connection.execute(
            "UPDATE sessions SET created_at = last_seen_at"
            " WHERE created_at IS NULL AND last_seen_at IS NOT NULL"
        )


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


def assert_owners_backfilled(connection: sqlite3.Connection) -> None:
    """任一内容表还有 NULL owner 就拒绝启动：缺归属的内容不该悄悄漏出去。

    由组装点（`build_container`）在"建 users → 种首 admin → `backfill_owners`"之后调用，
    **不在 `connect()` 里**——否则存量库会在 admin 种子落库之前先熔断，永远升不上来。
    `templates` 只查自建行——builtin 行留 NULL 是规矩，不是漏网。

    熔断时**必须给出可执行的下一步**（多用户改造 fix wave）：真正的原因是
    "存量内容需要归属，但库里没有管理员账号可归属"，而 `backfill_owners` 找不到 admin
    就静默不动。曾经这里写的是"先回填再启动"——可本仓根本没有手动回填命令，
    照着做的人只会卡死。现在点名要配的两个变量，配好重启即由组装点自动回填。
    """

    orphans_by_table: dict[str, int] = {}
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
            orphans_by_table[table] = orphans
    if not orphans_by_table:
        return

    detail = "、".join(f"{table} {n} 行" for table, n in orphans_by_table.items())
    has_admin = (
        connection.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
        is not None
    )
    if not has_admin:
        raise RuntimeError(
            f"存量内容没有归属（{detail} NULL owner），而库里还没有管理员账号可归属。"
            "请在 backend/.env 里配 PMSTUDIO_FIRST_ADMIN_USER 与 "
            "PMSTUDIO_FIRST_ADMIN_PASSWORD，重启后会自动把这些存量归到该管理员名下。"
        )
    raise RuntimeError(f"{detail} NULL owner，先回填再启动")
