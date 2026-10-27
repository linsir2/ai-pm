"""v17 迁移：lease 三列、`round_skips`、running 唯一索引（含老库重复 running 清理）。

老库形状手写 DDL（不复用 schema.sql）——要的就是"升级前"的样子。
"""

import sqlite3

from pmstudio.infrastructure.sqlite import database
from tests.support.builders import NOW

OLD_V16_DDL = """
CREATE TABLE rounds (
  round_id   TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  owner_id   TEXT,
  step       TEXT NOT NULL,
  waiting_on TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE explorations (
  exploration_id TEXT PRIMARY KEY,
  project_id     TEXT NOT NULL,
  owner_id       TEXT,
  kind           TEXT NOT NULL,
  status         TEXT NOT NULL,
  payload        TEXT NOT NULL,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
"""


def _old_db(path):  # type: ignore[no-untyped-def]
    old = sqlite3.connect(path)
    old.executescript(OLD_V16_DDL)
    old.execute(
        "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
        " created_at, updated_at) VALUES ('r1', 'p1', 'u1', 'done', 'none', '{}', ?, ?)",
        (NOW.isoformat(), NOW.isoformat()),
    )
    rows = (
        ("x-old", "p1", "running", "2026-10-01T10:00:00+00:00"),
        ("x-new", "p1", "running", "2026-10-02T10:00:00+00:00"),
        ("x-other", "p2", "running", "2026-10-01T10:00:00+00:00"),
        ("x-done", "p1", "done", "2026-10-03T10:00:00+00:00"),
    )
    for exploration_id, project_id, status, created_at in rows:
        old.execute(
            "INSERT INTO explorations (exploration_id, project_id, owner_id, kind, status,"
            " payload, created_at, updated_at) VALUES (?, ?, 'u1', 'inquiry', ?, '{}', ?, ?)",
            (exploration_id, project_id, status, created_at, created_at),
        )
    # 真的把老库标成 v16：只建出旧形状而不写版本号，测不到"v16 升到当前版本"这条路。
    old.execute("PRAGMA user_version = 16")
    old.commit()
    old.close()


def test_v16_db_gains_lease_columns_skips_table_and_unique_running(tmp_path):
    path = tmp_path / "old.db"
    _old_db(path)

    connection = database.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION

        for table in ("rounds", "explorations"):
            columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            assert {"lease_owner", "lease_expires_at", "lease_token"} <= columns

        # 业务数据没丢；新列有默认值
        row = connection.execute(
            "SELECT step, payload, lease_owner, lease_token FROM rounds WHERE round_id = 'r1'"
        ).fetchone()
        assert (row["step"], row["payload"], row["lease_owner"], row["lease_token"]) == (
            "done",
            "{}",
            None,
            0,
        )

        # 同一项目只留最新一条 running；其它项目的 running 不动；done 不动
        status = {
            item["exploration_id"]: item["status"]
            for item in connection.execute(
                "SELECT exploration_id, status FROM explorations"
            )
        }
        assert status == {
            "x-old": "abandoned",
            "x-new": "running",
            "x-other": "running",
            "x-done": "done",
        }

        # 部分唯一索引真的建出来了：再插一条同项目 running 必须被拒
        try:
            connection.execute(
                "INSERT INTO explorations (exploration_id, project_id, owner_id, kind,"
                " status, payload, created_at, updated_at) VALUES ('x-dup', 'p1', 'u1',"
                " 'inquiry', 'running', '{}', ?, ?)",
                (NOW.isoformat(), NOW.isoformat()),
            )
        except sqlite3.IntegrityError:
            pass
        else:  # pragma: no cover - 守卫自身有效性
            raise AssertionError("running 唯一索引没生效")

        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(round_skips)")
        }
        assert {"round_id", "owner_id", "seat_id", "pass_index"} <= columns
    finally:
        connection.close()


def test_migration_is_idempotent_for_v17(tmp_path):
    path = tmp_path / "old.db"
    _old_db(path)
    database.connect(path).close()

    connection = database.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == database.USER_VERSION
        count = connection.execute(
            "SELECT COUNT(*) FROM explorations WHERE project_id = 'p1'"
            " AND status = 'running'"
        ).fetchone()[0]
        assert count == 1
    finally:
        connection.close()
