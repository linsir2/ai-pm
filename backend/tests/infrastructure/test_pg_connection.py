"""真 PostgreSQL：连接、UoW、断连恢复。

这些用例**不跳过**：缺 URL 由 `tests.support.postgres.load_environment` 直接
`pytest.exit`（T1 的纪律：不回落 SQLite，也不静默跳过）。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import pytest

from pmstudio.harness.errors import DatabaseUnavailable
from pmstudio.infrastructure.postgres.database import (
    PgConnection,
    connect,
    merge_options,
    options_for,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    schema_url,
    truncate_data_tables,
)


@pytest.fixture(scope="session")
def database() -> PgTestDatabase:
    pg = load_environment()
    prepare(pg)
    return pg


def test_merge_options_keeps_search_path_and_appends_utc() -> None:
    merged = merge_options("-csearch_path=tenant,public")
    assert merged == "-csearch_path=tenant,public -c timezone=UTC"


def test_connect_options_are_parsed_from_the_dsn() -> None:
    url = (
        "postgresql://u:p@db:5432/pmstudio"
        "?options=-csearch_path%3Dtenant,public&sslmode=disable"
    )
    assert options_for(url) == "-csearch_path=tenant,public -c timezone=UTC"


def test_schema_url_preserves_other_query_parameters() -> None:
    url = "postgresql://u:p@db:5432/pmstudio?sslmode=disable&options=-csearch_path%3Dold,public"
    rewritten = schema_url(url, "pmstudio_test")
    query = dict(parse_qsl(urlsplit(rewritten).query))
    assert query["sslmode"] == "disable"
    assert query["options"] == "-csearch_path=pmstudio_test,public"


def test_connect_uses_dict_rows_autocommit_and_utc(database: PgTestDatabase) -> None:
    with connect(database.app_url) as connection:
        assert connection.autocommit is True
        assert connection.prepare_threshold is None
        assert connection.row_factory is not None
        row = connection.execute("SELECT current_schema() AS schema").fetchone()
        assert isinstance(row, dict)
        assert row == {"schema": database.schema}
        timezone = connection.execute("SHOW timezone").fetchone()
        assert timezone == {"TimeZone": "UTC"}
        name = connection.execute("SHOW application_name").fetchone()
        assert str(name["application_name"]).startswith("pmstudio-")


def _probe_table(database: PgTestDatabase) -> None:
    with connect(database.direct_url) as connection:
        connection.execute(
            f'DROP TABLE IF EXISTS "{database.schema}".uow_probe'
        )
        connection.execute(
            f'CREATE TABLE "{database.schema}".uow_probe (value text NOT NULL)'
        )


def _values(database: PgTestDatabase) -> list[str]:
    with connect(database.direct_url) as connection:
        rows = connection.execute(
            f'SELECT value FROM "{database.schema}".uow_probe ORDER BY value'
        ).fetchall()
        return [str(row["value"]) for row in rows]


def test_uow_commits_and_rolls_back(database: PgTestDatabase) -> None:
    _probe_table(database)
    try:
        with connect(database.direct_url) as connection:
            with PgUnitOfWork(connection):
                connection.execute(
                    f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                    ("committed",),
                )
            assert _values(database) == ["committed"]

            with pytest.raises(RuntimeError):
                with PgUnitOfWork(connection):
                    connection.execute(
                        f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                        ("rolled-back",),
                    )
                    raise RuntimeError("boom")
            assert _values(database) == ["committed"]
    finally:
        with connect(database.direct_url) as connection:
            connection.execute(f'DROP TABLE IF EXISTS "{database.schema}".uow_probe')


def test_nested_uow_rolls_back_only_inner_savepoint(database: PgTestDatabase) -> None:
    _probe_table(database)
    try:
        with connect(database.direct_url) as connection:
            uow = PgUnitOfWork(connection)
            with uow:
                connection.execute(
                    f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                    ("outer",),
                )
                with pytest.raises(RuntimeError):
                    with uow:
                        connection.execute(
                            f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                            ("inner",),
                        )
                        raise RuntimeError("inner boom")
                connection.execute(
                    f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                    ("after",),
                )
        assert _values(database) == ["after", "outer"]
    finally:
        with connect(database.direct_url) as connection:
            connection.execute(f'DROP TABLE IF EXISTS "{database.schema}".uow_probe')


def _terminate(pg: PgTestDatabase, pid: int) -> None:
    with connect(pg.direct_url) as killer:
        killer.execute("SELECT pg_terminate_backend(%s)", (pid,))


def test_pg_connection_reconnects_outside_a_transaction(database: PgTestDatabase) -> None:
    wrapper = PgConnection(database.direct_url)
    try:
        pid = wrapper.execute("SELECT pg_backend_pid() AS pid").fetchone()
        assert pid is not None
        _terminate(database, int(pid["pid"]))
        row = wrapper.execute("SELECT 1 AS ok").fetchone()
        assert row == {"ok": 1}
        new_pid = wrapper.execute("SELECT pg_backend_pid() AS pid").fetchone()
        assert new_pid is not None and int(new_pid["pid"]) != int(pid["pid"])
    finally:
        wrapper.close()


def test_pg_connection_returns_503_inside_a_transaction(database: PgTestDatabase) -> None:
    wrapper = PgConnection(database.direct_url)
    try:
        with pytest.raises(DatabaseUnavailable, match="事务"):
            with PgUnitOfWork(wrapper):
                pid = wrapper.execute("SELECT pg_backend_pid() AS pid").fetchone()
                assert pid is not None
                _terminate(database, int(pid["pid"]))
                wrapper.execute("SELECT 1")
        assert wrapper.closed is True
        assert wrapper.execute("SELECT 1 AS ok").fetchone() == {"ok": 1}
    finally:
        wrapper.close()


def test_truncate_keeps_migration_and_vector_tables(database: PgTestDatabase) -> None:
    with connect(database.direct_url) as connection:
        connection.execute(
            f'CREATE TABLE IF NOT EXISTS "{database.schema}".truncate_probe '
            "(id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, value text)"
        )
        connection.execute(
            f'CREATE TABLE IF NOT EXISTS "{database.schema}".schema_migrations '
            "(version int PRIMARY KEY)"
        )
        connection.execute(
            f'CREATE TABLE IF NOT EXISTS "{database.schema}".vector_index_meta '
            "(id smallint PRIMARY KEY, profile text, dimensions int, model text)"
        )
        connection.execute(
            f'INSERT INTO "{database.schema}".truncate_probe (value) VALUES (%s)',
            ("gone",),
        )
        connection.execute(
            f'INSERT INTO "{database.schema}".schema_migrations (version) VALUES (1) '
            "ON CONFLICT DO NOTHING"
        )
        connection.execute(
            f'INSERT INTO "{database.schema}".vector_index_meta '
            "(id, profile, dimensions, model) VALUES (1, 'p', 512, 'm') "
            "ON CONFLICT (id) DO UPDATE SET profile = EXCLUDED.profile"
        )

    truncate_data_tables(database)

    with connect(database.direct_url) as connection:
        probe = connection.execute(
            f'SELECT count(*) AS n FROM "{database.schema}".truncate_probe'
        ).fetchone()
        migrations = connection.execute(
            f'SELECT count(*) AS n FROM "{database.schema}".schema_migrations'
        ).fetchone()
        vectors = connection.execute(
            f'SELECT count(*) AS n FROM "{database.schema}".vector_index_meta'
        ).fetchone()
        assert probe == {"n": 0}
        assert migrations == {"n": 1}
        assert vectors == {"n": 1}
