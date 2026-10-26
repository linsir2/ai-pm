"""迁移 v13：owner 列 + 回填 + NULL 熔断（多用户改造，裁决 A / Ruling 13）。

v12 的库没有任何归属。13 给 **13 张表**补上 `owner_id TEXT`：12 张项目域表
（documents 及其余 join projects 的表）+ `templates`。存量行回填给首个 admin；
`templates` 的 builtin 行留 NULL（出厂模板人人可见）。补不齐就熔断——
宁可起不来，也不让没归属的内容悄悄漏给所有人。

次序是契约（Ruling 13）：`connect()` **只建表+迁移，不熔断**；组装点按
`connect → 建 users → 种首 admin → backfill_owners → assert_owners_backfilled` 走。
本文件按同一顺序显式调用。豁免不加列的：users / sessions / model_calls。

注意：`user_version` 的字面量随 `database.USER_VERSION` 前进（本文件已到 17——v17 加了
lease 三列 / `round_skips` / running 唯一索引）。这里断言的是"连完就是当前形状"，不是"停在 13"。
"""

import sqlite3

import pytest

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.infrastructure.sqlite import database
from tests.support.builders import NOW

ADMIN_ID = "u-admin"

#: 13 张要带 owner 的表（裁决 A）：12 张项目域表 + templates。
OWNER_TABLES = (
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

#: v12 形状（**无** owner_id）手写 DDL——不复用 schema.sql，要的就是旧形状。
#: 列全带上：schema.sql 的 CREATE INDEX 随后会跑，索引引用的列必须在。
OLD_V12_DDL = """
CREATE TABLE documents (
  project_id         TEXT PRIMARY KEY,
  document_id        TEXT NOT NULL,
  title              TEXT NOT NULL,
  template           TEXT NOT NULL DEFAULT '{}',
  blocks             TEXT NOT NULL,
  preference_entries TEXT NOT NULL,
  tombstones         TEXT NOT NULL,
  created_at         TEXT NOT NULL,
  updated_at         TEXT NOT NULL
);
CREATE TABLE document_versions (
  project_id        TEXT NOT NULL,
  version_no        INTEGER NOT NULL,
  trigger           TEXT NOT NULL,
  summary           TEXT NOT NULL,
  changed_block_ids TEXT NOT NULL,
  snapshot          TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  PRIMARY KEY (project_id, version_no)
);
CREATE TABLE rounds (
  round_id   TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  step       TEXT NOT NULL,
  waiting_on TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE evidence_ledgers (
  project_id TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE materials (
  material_id    TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  origin         TEXT NOT NULL,
  title          TEXT NOT NULL,
  source_ref     TEXT NOT NULL,
  media_type     TEXT NOT NULL,
  bytes_len      INTEGER NOT NULL,
  sha256         TEXT NOT NULL,
  parser_id      TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  truncated      INTEGER NOT NULL,
  warnings       TEXT NOT NULL,
  content        BLOB NOT NULL,
  created_at     TEXT NOT NULL
);
CREATE TABLE material_chunks (
  chunk_id     TEXT PRIMARY KEY,
  material_id  TEXT NOT NULL,
  ordinal      INTEGER NOT NULL,
  text         TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  anchor       TEXT NOT NULL
);
CREATE TABLE explorations (
  exploration_id TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  kind           TEXT NOT NULL,
  status         TEXT NOT NULL,
  payload        TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE TABLE discussions (
  discussion_id TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  status         TEXT NOT NULL,
  payload        TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE TABLE project_memory (
  project_id TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE observations (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  round_id   TEXT NOT NULL,
  obs_type   TEXT NOT NULL,
  title      TEXT NOT NULL,
  detail     TEXT NOT NULL DEFAULT '',
  source_ref TEXT NOT NULL DEFAULT ''
);
CREATE TABLE round_digests (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  round_id   TEXT NOT NULL,
  summary    TEXT NOT NULL,
  sections   TEXT NOT NULL DEFAULT '[]',
  features   TEXT NOT NULL DEFAULT '[]',
  outcomes   TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE TABLE inspirations (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  origin     TEXT NOT NULL,
  statement  TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE templates (
  template_id TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  spec        TEXT NOT NULL,
  builtin     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE TABLE users (
  user_id       TEXT PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',
  role          TEXT NOT NULL DEFAULT 'user',
  approved_by   TEXT,
  approved_at   TEXT,
  created_at    TEXT NOT NULL
);
"""


def _old_v12_db(path, *, content: bool, templates: bool = True) -> None:
    """手建一个 v12 形状的库：可选塞 1 项目 + 1 轮次 + 内置/自建模板。

    `templates=False` 供真调 `build_container` 的用例用——那边的模板加载器要真 spec，
    这里塞的 `'{}'` 占位行会把它噎住（模板本身不是本任务的关注点）。
    """

    old = sqlite3.connect(path)
    old.executescript(OLD_V12_DDL)
    if content:
        old.execute(
            "INSERT INTO documents (project_id, document_id, title, template, blocks,"
            " preference_entries, tombstones, created_at, updated_at) VALUES"
            " ('p1', 'doc-1', '旧项目', '{}', '[]', '[]', '[]', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        old.execute(
            "INSERT INTO rounds (round_id, project_id, step, waiting_on, payload,"
            " created_at, updated_at) VALUES ('r1', 'p1', 'done', 'none', '{}', ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    if templates:
        old.execute(
            "INSERT INTO templates (template_id, title, spec, builtin, created_at,"
            " updated_at) VALUES ('t-builtin', '内置', '{}', 1, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        old.execute(
            "INSERT INTO templates (template_id, title, spec, builtin, created_at,"
            " updated_at) VALUES ('t-custom', '自建', '{}', 0, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    old.commit()
    old.close()


def _seed_admin(connection) -> None:  # type: ignore[no-untyped-def]
    """组装点的种子动作：users 为空才建首 admin（幂等，照 `_seed_first_admin` 语义）。"""

    if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
        return
    connection.execute(
        "INSERT INTO users (user_id, username, password_hash, status, role,"
        " created_at) VALUES (?, 'admin', 'x', 'active', 'admin', ?)",
        (ADMIN_ID, NOW.isoformat()),
    )


def _connect_and_backfill(path):  # type: ignore[no-untyped-def]
    """组装点的次序：connect → 种 admin → 回填 → 熔断校验。"""

    connection = database.connect(path)
    _seed_admin(connection)
    database.backfill_owners(connection)
    database.assert_owners_backfilled(connection)
    return connection


def test_v12_db_migrates_to_v13_with_owners(tmp_path):
    path = tmp_path / "old.db"
    _old_v12_db(path, content=True)

    connection = _connect_and_backfill(path)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM documents WHERE owner_id IS NULL"
        ).fetchone()[0]
        == 0
    )
    assert (
        connection.execute("SELECT owner_id FROM documents").fetchone()[0] == "u-admin"
    )

    # 13 张表逐张断言：非 builtin 行不许有 NULL owner
    for table in OWNER_TABLES:
        if table == "templates":
            orphans = connection.execute(
                "SELECT COUNT(*) FROM templates WHERE owner_id IS NULL AND builtin = 0"
            ).fetchone()[0]
        else:
            orphans = connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE owner_id IS NULL"
            ).fetchone()[0]
        assert orphans == 0, f"{table} 残留 NULL owner"

    assert (
        connection.execute(
            "SELECT owner_id FROM templates WHERE template_id = 't-builtin'"
        ).fetchone()[0]
        is None
    )
    assert (
        connection.execute(
            "SELECT owner_id FROM templates WHERE template_id = 't-custom'"
        ).fetchone()[0]
        == ADMIN_ID
    )
    connection.close()


def test_connect_alone_never_fuses_but_assert_catches_orphans(tmp_path):
    """Ruling 13：connect 不熔断（否则首次升级卡在 seed 之前）。

    "只 connect 不 backfill → 不抛"与"null 行还在时 assert → 抛、点名表"的对照，
    回填之后 assert 又不再抛。
    """

    path = tmp_path / "orphan.db"
    _old_v12_db(path, content=True)

    connection = database.connect(path)  # 不熔断：还没到校验那一步
    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION

    with pytest.raises(RuntimeError, match="documents"):
        database.assert_owners_backfilled(connection)

    _seed_admin(connection)
    database.backfill_owners(connection)
    database.assert_owners_backfilled(connection)  # 不再抛
    connection.close()


def test_fresh_db_is_built_in_v13_shape(tmp_path):
    """新库直接建 13 形状：13 张表有 owner 列，豁免的三张没有。"""

    connection = database.connect(tmp_path / "new.db")

    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    for table in OWNER_TABLES:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        assert "owner_id" in columns, table
    for table in ("users", "sessions", "model_calls"):
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        assert "owner_id" not in columns, table
    connection.close()


def test_migration_is_idempotent(tmp_path):
    """再连一次不许炸、不许改写已有 owner。"""

    path = tmp_path / "old.db"
    _old_v12_db(path, content=True)
    _connect_and_backfill(path).close()

    connection = _connect_and_backfill(path)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
    assert (
        connection.execute(
            "SELECT owner_id FROM documents WHERE project_id = 'p1'"
        ).fetchone()[0]
        == ADMIN_ID
    )
    connection.close()


def test_build_container_fuses_when_orphans_cannot_be_backfilled(tmp_path):
    """真调组装点，不本地复刻次序：孤儿行 + 没配 FIRST_ADMIN → 起不来、点名表。

    这条钉住 `build_container` 里"backfill → assert"那两行——删掉它，本用例就红
    （connect 已不熔断，没有它没人拦得住无归属的存量内容）。
    """

    path = tmp_path / "old.db"
    _old_v12_db(path, content=True, templates=False)
    settings = Settings(
        db_path=path, model="m", api_key="k", api_base=None
    )  # 不配 FIRST_ADMIN_*

    with pytest.raises(RuntimeError, match="documents"):
        build_container(settings)


def test_build_container_backfills_orphans_to_first_admin(tmp_path):
    """同一形状库 + 配好 FIRST_ADMIN_*：起得来，孤儿行归属回填成该 admin。"""

    path = tmp_path / "old.db"
    _old_v12_db(path, content=True, templates=False)
    settings = Settings(
        db_path=path,
        model="m",
        api_key="k",
        api_base=None,
        first_admin_username="admin",
        first_admin_password="admin-s3cret",
    )

    build_container(settings)  # 不抛

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    admin_id = connection.execute(
        "SELECT user_id FROM users WHERE role = 'admin' LIMIT 1"
    ).fetchone()["user_id"]
    assert (
        connection.execute(
            "SELECT owner_id FROM documents WHERE project_id = 'p1'"
        ).fetchone()["owner_id"]
        == admin_id
    )
    assert (
        connection.execute(
            "SELECT owner_id FROM rounds WHERE round_id = 'r1'"
        ).fetchone()["owner_id"]
        == admin_id
    )
    # 加载器种下的出厂模板都是 builtin，owner 留 NULL（人人可见）
    assert (
        connection.execute(
            "SELECT COUNT(*) FROM templates WHERE builtin = 1 AND owner_id IS NOT NULL"
        ).fetchone()[0]
        == 0
    )
    connection.close()


def test_build_container_starts_with_no_owner_scope(tmp_path):
    """回归：组装期**没有身份**，应用也得起得来。

    `build_container` 要读一次模板（builtin 出厂模板 `owner_id` 恒 NULL、人人可见），
    那次读发生在任何请求之前——`require_user` 还没机会设 scope。此前那条读硬要求 owner，
    于是**测试全绿、生产一起就崩**：`tests/conftest.py` 的 autouse fixture 给每个用例
    装了默认 scope，正好把这个条件遮住了。所以这里必须显式清掉。
    """

    from pmstudio.infrastructure.sqlite import owner_scope

    path = tmp_path / "old.db"
    _old_v12_db(path, content=False, templates=False)
    settings = Settings(db_path=path, model="m", api_key="k", api_base=None)

    owner_scope.clear_owner_scope()
    assert owner_scope.current_owner_scope() is None
    container = build_container(settings)  # 之前在这里抛 missing_owner_scope

    # builtin 模板照样加载进来了（证明那次无主读真的跑到了 SQL，而不是被吞掉）
    connection = sqlite3.connect(path)
    loaded = connection.execute(
        "SELECT COUNT(*) FROM templates WHERE builtin = 1 AND owner_id IS NULL"
    ).fetchone()[0]
    assert loaded > 0
    connection.close()
    assert container.settings.db_path == path


def test_build_container_error_names_the_env_vars_when_no_admin_can_own_orphans(tmp_path):
    """存量有内容、库里却没有 admin 时，**报错必须可执行**：点名要配哪两个变量。

    之前的文案是"先回填再启动"，但本仓根本没有手动回填命令——用户按它做不动。
    真解法只有一个：配 `PMSTUDIO_FIRST_ADMIN_*`，重启后组装点自动回填。
    """

    path = tmp_path / "old.db"
    _old_v12_db(path, content=True, templates=False)
    settings = Settings(db_path=path, model="m", api_key="k", api_base=None)

    with pytest.raises(RuntimeError) as caught:
        build_container(settings)

    message = str(caught.value)
    assert "PMSTUDIO_FIRST_ADMIN_USER" in message
    assert "PMSTUDIO_FIRST_ADMIN_PASSWORD" in message
    assert "documents" in message


#: 回归：`sessions.created_at`（90 天绝对上限的锚点）是**后加**的列。
#: `CREATE TABLE IF NOT EXISTS` 补不了已有表的列，所以跑过早期版本的库会永远缺这一列——
#: 而登录路径第一条 INSERT 就写它，于是**登录必然 500**。
#: 手动回填取 `last_seen_at`（那时能拿到的最接近建会话时刻的值）；宁可从迁移时刻起算，
#: 也不能让上限失去锚点。
LEGACY_SESSIONS_DDL = """
CREATE TABLE users (
  user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
  role TEXT NOT NULL DEFAULT 'user', approved_by TEXT, approved_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, token_hash TEXT NOT NULL,
  expires_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  ip TEXT NOT NULL DEFAULT '', user_agent TEXT NOT NULL DEFAULT '',
  revoked_at TEXT
);
"""


def test_migrate_adds_created_at_to_an_existing_sessions_table(tmp_path):
    path = tmp_path / "legacy.db"
    old = sqlite3.connect(path)
    old.executescript(LEGACY_SESSIONS_DDL)
    old.execute(
        "INSERT INTO sessions (session_id, user_id, token_hash, expires_at,"
        " last_seen_at) VALUES ('s1', 'u1', 'h', '2999-01-01T00:00:00+00:00',"
        " '2026-01-01T00:00:00+00:00')"
    )
    old.commit()
    old.close()

    connection = database.connect(path)

    columns = {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}
    assert "created_at" in columns
    # 老行不留 NULL：绝对上限的锚点不能是空的（`GetMe` 拿它做 datetime 减法）
    assert connection.execute(
        "SELECT created_at FROM sessions WHERE session_id = 's1'"
    ).fetchone()["created_at"] == "2026-01-01T00:00:00+00:00"
    connection.close()
