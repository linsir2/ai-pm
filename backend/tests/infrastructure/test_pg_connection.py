"""真 PostgreSQL：连接、UoW、断连恢复。

这些用例**不跳过**：缺 URL 由 `tests.support.postgres.load_environment` 直接
`pytest.exit`（T1 的纪律：不回落 SQLite，也不静默跳过）。
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlsplit

import pytest
from psycopg.errors import AdminShutdown, LockNotAvailable, QueryCanceled

from pmstudio.harness.errors import DatabaseUnavailable
from pmstudio.infrastructure.postgres.database import (
    PgConnection,
    _is_connection_error,
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


def test_only_connection_sqlstates_are_reconnectable() -> None:
    """锁等待 / 语句取消不是连接断开，不能当成可重连错误。"""

    assert _is_connection_error(AdminShutdown("terminated")) is True
    assert _is_connection_error(LockNotAvailable("lock")) is False
    assert _is_connection_error(QueryCanceled("timeout")) is False


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


def test_pg_connection_replays_a_read_after_reconnecting(database: PgTestDatabase) -> None:
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


def test_pg_connection_does_not_replay_a_write(database: PgTestDatabase) -> None:
    """断连时写是否已提交未知；当前写只回 503，**不重放**。"""

    _probe_table(database)
    wrapper = PgConnection(database.direct_url)
    try:
        pid = wrapper.execute("SELECT pg_backend_pid() AS pid").fetchone()
        assert pid is not None
        _terminate(database, int(pid["pid"]))
        with pytest.raises(DatabaseUnavailable, match="写"):
            wrapper.execute(
                f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                ("must-not-double-write",),
            )
        assert _values(database) == []
        # 下一请求重连；读可以正常走。
        assert wrapper.execute("SELECT 1 AS ok").fetchone() == {"ok": 1}
    finally:
        wrapper.close()
        with connect(database.direct_url) as connection:
            connection.execute(f'DROP TABLE IF EXISTS "{database.schema}".uow_probe')


def test_lock_timeout_is_not_mapped_to_database_unavailable(
    database: PgTestDatabase,
) -> None:
    """55P03 是锁等待，不是连接断开——不许被包装成 503 的 DatabaseUnavailable。"""

    _probe_table(database)
    wrapper = PgConnection(database.direct_url)
    locker = connect(database.direct_url)
    try:
        wrapper.execute("SET lock_timeout = '150ms'")
        locker.execute("BEGIN")
        locker.execute(f'LOCK TABLE "{database.schema}".uow_probe IN ACCESS EXCLUSIVE MODE')
        with pytest.raises(Exception) as caught:
            wrapper.execute(
                f'INSERT INTO "{database.schema}".uow_probe (value) VALUES (%s)',
                ("blocked",),
            )
        assert not isinstance(caught.value, DatabaseUnavailable)
        assert getattr(caught.value, "sqlstate", None) == "55P03"
    finally:
        locker.execute("ROLLBACK")
        locker.close()
        wrapper.close()
        with connect(database.direct_url) as connection:
            connection.execute(f'DROP TABLE IF EXISTS "{database.schema}".uow_probe')


def test_pg_connection_returns_503_inside_a_transaction(database: PgTestDatabase) -> None:
    wrapper = PgConnection(database.direct_url)
    try:
        with pytest.raises(DatabaseUnavailable, match="事务"):
            with PgUnitOfWork(wrapper):
                assert wrapper.transaction_depth == 1
                pid = wrapper.execute("SELECT pg_backend_pid() AS pid").fetchone()
                assert pid is not None
                _terminate(database, int(pid["pid"]))
                wrapper.execute("SELECT 1")
        assert wrapper.closed is True
        assert wrapper.transaction_depth == 0
        assert wrapper.execute("SELECT 1 AS ok").fetchone() == {"ok": 1}
    finally:
        wrapper.close()


def test_uow_tracks_depth_and_closing_inside_a_transaction_never_reconnects(
    database: PgTestDatabase,
) -> None:
    wrapper = PgConnection(database.direct_url)
    try:
        with PgUnitOfWork(wrapper):
            assert wrapper.transaction_depth == 1
        assert wrapper.transaction_depth == 0

        with pytest.raises(DatabaseUnavailable, match="事务"):
            with PgUnitOfWork(wrapper):
                assert wrapper.transaction_depth == 1
                wrapper.close()
                wrapper.execute("SELECT 1")
        assert wrapper.transaction_depth == 0
        assert wrapper.execute("SELECT 1 AS ok").fetchone() == {"ok": 1}
    finally:
        wrapper.close()


def test_truncate_keeps_migration_and_vector_tables(database: PgTestDatabase) -> None:
    """用独立 schema 验证 preserved 表不被清，不往共享的迁移/向量表写伪数据。"""

    from dataclasses import replace

    probe_schema = f"{database.schema}_truncate_probe"
    probe = replace(
        database,
        schema=probe_schema,
        direct_url=schema_url(database.direct_url, probe_schema),
    )
    with connect(database.direct_url) as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{probe_schema}" CASCADE')
        connection.execute(f'CREATE SCHEMA "{probe_schema}"')
    try:
        with connect(probe.direct_url) as connection:
            connection.execute(
                "CREATE TABLE templates (template_id text PRIMARY KEY)"
            )
            connection.execute(
                "CREATE TABLE schema_migrations (version int PRIMARY KEY)"
            )
            connection.execute(
                "CREATE TABLE vector_index_meta "
                "(id smallint PRIMARY KEY, profile text, dimensions int, model text)"
            )
            connection.execute("INSERT INTO templates (template_id) VALUES ('gone')")
            connection.execute("INSERT INTO schema_migrations (version) VALUES (1)")
            connection.execute(
                "INSERT INTO vector_index_meta (id, profile, dimensions, model) "
                "VALUES (1, 'p', 512, 'm')"
            )

        truncate_data_tables(probe)

        with connect(probe.direct_url) as connection:
            assert connection.execute(
                "SELECT count(*) AS n FROM templates"
            ).fetchone() == {"n": 0}
            assert connection.execute(
                "SELECT count(*) AS n FROM schema_migrations"
            ).fetchone() == {"n": 1}
            assert connection.execute(
                "SELECT count(*) AS n FROM vector_index_meta"
            ).fetchone() == {"n": 1}
    finally:
        with connect(database.direct_url) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{probe_schema}" CASCADE')
