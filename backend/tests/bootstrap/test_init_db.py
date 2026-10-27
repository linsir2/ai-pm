"""init_db 的三段式：bootstrap / schema-only / finalize（真 PostgreSQL）。

这些用例只在**有建库权限**的测试账号下跑（CI 的 pgvector service 默认是
superuser）。没有权限时明确 skip，不假装通过；不会拿 SQLite 顶替。
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from psycopg import connect as psycopg_connect
from scripts.init_db import (
    APP_ROLE,
    OWNER_ROLE,
    bootstrap_database,
    finalize_database,
    schema_only,
)
from scripts.pg_scratch import drop_scratch, make_scratch

from pmstudio.bootstrap.settings import (
    BootstrapSettings,
    ConfigError,
    OpsSettings,
)
from pmstudio.infrastructure.postgres.database import connect
from tests.support.postgres import load_environment

APP_PASSWORD = "t7-app-pass"
OWNER_PASSWORD = "t7-owner-pass"
ADMIN_USER = "root"
ADMIN_PASSWORD = "root-pass"


def _replace_database(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))


def _as_user(url: str, *, user: str, password: str, dbname: str) -> str:
    parts = urlsplit(url)
    netloc = f"{user}:{password}@{parts.hostname}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, f"/{dbname}", "", ""))


def _base_direct_url() -> str:
    database = load_environment()
    return database.direct_url


@pytest.fixture()
def scratch_database() -> Iterator[str]:
    """临时 database（不是 schema）：bootstrap 的 CREATE ROLE/ALTER DATABASE 是库级。"""

    base = _base_direct_url()
    admin_url = _replace_database(base, "postgres")
    name = f"pmstudio_t7_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    try:
        with psycopg_connect(admin_url, autocommit=True) as connection:
            can_create = connection.execute(
                "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"
            ).fetchone()[0]
            if not can_create:
                pytest.skip("测试账号没有 CREATEDB，跑不了 bootstrap 用例")
            connection.execute(f'CREATE DATABASE "{name}"')
    except Exception as error:  # noqa: BLE001 —— 权限/连接问题明确 skip
        pytest.skip(f"建临时库失败（{type(error).__name__}）：{error}")
    try:
        yield _replace_database(base, name)
    finally:
        with psycopg_connect(admin_url, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _bootstrap(url: str, auth_file: Path) -> BootstrapSettings:
    return BootstrapSettings(
        bootstrap_database_url=url,
        app_password=APP_PASSWORD,
        owner_password=OWNER_PASSWORD,
        auth_file=str(auth_file),
        auth_uid=os.getuid(),
    )


def _ops(url: str, *, admin: bool = True) -> OpsSettings:
    return OpsSettings(
        direct_database_url=_as_user(
            url, user=OWNER_ROLE, password=OWNER_PASSWORD, dbname=urlsplit(url).path.lstrip("/")
        ),
        first_admin_username=ADMIN_USER if admin else None,
        first_admin_password=ADMIN_PASSWORD if admin else None,
    )


def test_all_bootstraps_roles_extension_and_auth_file(scratch_database, tmp_path) -> None:
    auth_file = tmp_path / "userlist.txt"
    bootstrap_database(_bootstrap(scratch_database, auth_file))
    # bootstrap 之后 owner 才能连（角色口令刚建）；schema/finalize 只用 owner URL。
    owner_url = _ops(scratch_database).direct_database_url
    assert schema_only(owner_url) == [1]
    result = finalize_database(owner_url, _ops(scratch_database))
    assert result["templates"] >= 1

    assert auth_file.stat().st_mode & 0o777 == 0o600
    content = auth_file.read_text(encoding="utf-8")
    assert f'"{APP_ROLE}"' in content and f'"{OWNER_ROLE}"' in content

    dbname = urlsplit(scratch_database).path.lstrip("/")
    app_url = _as_user(
        scratch_database, user=APP_ROLE, password=APP_PASSWORD, dbname=dbname
    )
    with connect(owner_url) as owner:
        tables = owner.execute(
            "SELECT count(*) AS n FROM information_schema.tables"
            " WHERE table_schema = current_schema() AND table_type = 'BASE TABLE'"
        ).fetchone()
        assert tables is not None and int(tables["n"]) == 20
        extension = owner.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        assert extension is not None
        profile = owner.execute(
            "SELECT dimensions FROM vector_index_meta WHERE id = 1"
        ).fetchone()
        assert profile == {"dimensions": 512}
        users = owner.execute(
            "SELECT username, status, role FROM users"
        ).fetchall()
        assert users == [{"username": ADMIN_USER, "status": "active", "role": "admin"}]

    with connect(app_url) as app:
        assert app.execute("SELECT count(*) AS n FROM users").fetchone() == {"n": 1}
        with pytest.raises(Exception) as caught:
            app.execute("CREATE TABLE app_must_not_ddl (x int)")
        assert getattr(caught.value, "sqlstate", None) == "42501"

    # finalize 幂等：不会重复种管理员/模板。
    again = finalize_database(owner_url, _ops(scratch_database))
    assert again["owners"] == 0
    assert again["templates"] == result["templates"]


def test_scratch_schema_grants_the_real_app_role(scratch_database, tmp_path) -> None:
    """T7 评审 Important 1：临时 schema 也要给 pmstudio_app USAGE + 表/序列权限。"""

    bootstrap_database(_bootstrap(scratch_database, tmp_path / "userlist.txt"))
    owner_url = _ops(scratch_database).direct_database_url
    schema_only(owner_url)
    finalize_database(owner_url, _ops(scratch_database))
    dbname = urlsplit(scratch_database).path.lstrip("/")
    app_base = _as_user(
        scratch_database, user=APP_ROLE, password=APP_PASSWORD, dbname=dbname
    )
    scratch = make_scratch(
        prefix="grant",
        env={
            "PMSTUDIO_DATABASE_URL": app_base,
            "PMSTUDIO_DIRECT_DATABASE_URL": owner_url,
        },
        admin_user="scratch-admin",
        admin_password="scratch-pass",
    )
    try:
        with connect(scratch.app_url) as app:
            assert app.execute(
                "SELECT count(*) AS n FROM templates"
            ).fetchone() == {"n": 2}
            assert app.execute(
                "SELECT count(*) AS n FROM users"
            ).fetchone() == {"n": 1}
            row = app.execute(
                "SELECT has_schema_privilege(current_user, current_schema(), 'USAGE') AS ok"
            ).fetchone()
            assert row == {"ok": True}
    finally:
        drop_scratch(scratch)


def test_schema_only_does_not_seed(scratch_database, tmp_path) -> None:
    bootstrap_database(_bootstrap(scratch_database, tmp_path / "userlist.txt"))
    owner_url = _ops(scratch_database).direct_database_url
    schema_only(owner_url)
    with connect(owner_url) as owner:
        assert owner.execute("SELECT count(*) AS n FROM users").fetchone() == {"n": 0}
        assert owner.execute("SELECT count(*) AS n FROM templates").fetchone() == {"n": 0}
        assert owner.execute("SELECT count(*) AS n FROM vector_index_meta").fetchone() == {"n": 0}


def test_finalize_rejects_non_512_profile(scratch_database, tmp_path) -> None:
    bootstrap_database(_bootstrap(scratch_database, tmp_path / "userlist.txt"))
    owner_url = _ops(scratch_database).direct_database_url
    schema_only(owner_url)
    finalize_database(owner_url, _ops(scratch_database))
    with connect(owner_url) as owner:
        owner.execute("UPDATE vector_index_meta SET dimensions = 384 WHERE id = 1")
    with pytest.raises(ValueError, match="384"):
        finalize_database(owner_url, _ops(scratch_database))
    with connect(owner_url) as owner:
        row = owner.execute(
            "SELECT dimensions FROM vector_index_meta WHERE id = 1"
        ).fetchone()
        assert row == {"dimensions": 384}, "失败时不许静默改掉 profile"


def test_finalize_backfills_owners_to_first_admin(scratch_database, tmp_path) -> None:
    bootstrap_database(_bootstrap(scratch_database, tmp_path / "userlist.txt"))
    owner_url = _ops(scratch_database).direct_database_url
    schema_only(owner_url)
    with connect(owner_url) as owner:
        owner.execute(
            "INSERT INTO documents (project_id, owner_id, document_id, title,"
            " blocks, preference_entries, tombstones, created_at, updated_at)"
            " VALUES ('p-null', NULL, 'd-1', '旧项目', '{}'::jsonb, '[]'::jsonb,"
            " '[]'::jsonb, now(), now())"
        )
    result = finalize_database(owner_url, _ops(scratch_database))
    assert result["owners"] >= 1
    with connect(owner_url) as owner:
        row = owner.execute(
            "SELECT owner_id FROM documents WHERE project_id = 'p-null'"
        ).fetchone()
        assert row is not None and row["owner_id"] is not None


def test_finalize_without_admin_config_is_a_hard_error(scratch_database, tmp_path) -> None:
    bootstrap_database(_bootstrap(scratch_database, tmp_path / "userlist.txt"))
    owner_url = _ops(scratch_database).direct_database_url
    schema_only(owner_url)
    with pytest.raises(ConfigError, match="PMSTUDIO_FIRST_ADMIN"):
        finalize_database(owner_url, _ops(scratch_database, admin=False))


def test_bootstrap_requires_its_own_url(monkeypatch) -> None:
    monkeypatch.delenv("PMSTUDIO_BOOTSTRAP_DATABASE_URL", raising=False)
    monkeypatch.setenv("PMSTUDIO_APP_PASSWORD", "a")
    monkeypatch.setenv("PMSTUDIO_OWNER_PASSWORD", "o")
    with pytest.raises(ConfigError, match="PMSTUDIO_BOOTSTRAP_DATABASE_URL"):
        BootstrapSettings.from_env()


def test_ops_settings_do_not_read_bootstrap_url() -> None:
    with pytest.raises(ConfigError, match="PMSTUDIO_DIRECT_DATABASE_URL"):
        OpsSettings.from_env(
            {"PMSTUDIO_BOOTSTRAP_DATABASE_URL": "postgresql://postgres@db:5432/x"}
        )
