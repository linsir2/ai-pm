"""加表之后，旧库要能自己走到当前版本。"""

import sqlite3
from pathlib import Path

import pytest

from pmstudio.storage.db import SCHEMA_VERSION, Database


def _make_v1_database(path: Path) -> None:
    """造一个 R0.1 形态的库：只有 schema_meta 与 projects，版本停在 1。"""
    connection = sqlite3.connect(str(path))
    connection.executescript(
        """
        CREATE TABLE schema_meta (version INTEGER NOT NULL);
        INSERT INTO schema_meta (version) VALUES (1);
        CREATE TABLE projects (
            project_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            template_id TEXT NOT NULL,
            config_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    connection.commit()
    connection.close()


def _table_names(db: Database) -> set[str]:
    rows = db._connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row["name"] for row in rows}


def test_old_database_is_upgraded_to_the_current_version(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    _make_v1_database(path)

    db = Database.open(path)
    try:
        assert SCHEMA_VERSION == 3
        assert db.schema_version() == SCHEMA_VERSION
        assert "events" in _table_names(db)
        assert {"rounds", "board_regions"} <= _table_names(db)
    finally:
        db.close()


def test_newer_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(str(path))
    connection.executescript(
        f"CREATE TABLE schema_meta (version INTEGER NOT NULL);"
        f"INSERT INTO schema_meta (version) VALUES ({SCHEMA_VERSION + 1});"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="拒绝打开"):
        Database.open(path)
