"""迁移 v13：owner 列 + 回填 + NULL 熔断（多用户改造，裁决 A / Ruling 13）。

v12 的库没有任何归属。13 给 **13 张表**补上 `owner_id TEXT`：12 张项目域表
（documents 及其余 join projects 的表）+ `templates`。存量行回填给首个 admin；
`templates` 的 builtin 行留 NULL（出厂模板人人可见）。补不齐就熔断——
宁可起不来，也不让没归属的内容悄悄漏给所有人。

次序是契约（Ruling 13）：`connect()` **只建表+迁移，不熔断**；组装点按
`connect → 建 users → 种首 admin → backfill_owners → assert_owners_backfilled` 走。
本文件按同一顺序显式调用。豁免不加列的：users / sessions / model_calls。
"""

import sqlite3

import pytest

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


def _old_v12_db(path, *, content: bool) -> None:
    """手建一个 v12 形状的库：可选塞 1 项目 + 1 轮次 + 内置/自建模板。"""

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

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 13
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
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 13

    with pytest.raises(RuntimeError, match="documents"):
        database.assert_owners_backfilled(connection)

    _seed_admin(connection)
    database.backfill_owners(connection)
    database.assert_owners_backfilled(connection)  # 不再抛
    connection.close()


def test_fresh_db_is_built_in_v13_shape(tmp_path):
    """新库直接建 13 形状：13 张表有 owner 列，豁免的三张没有。"""

    connection = database.connect(tmp_path / "new.db")

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 13
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

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 13
    assert (
        connection.execute(
            "SELECT owner_id FROM documents WHERE project_id = 'p1'"
        ).fetchone()[0]
        == ADMIN_ID
    )
    connection.close()
