"""PG 备份/恢复演练：真 `pg_dump -Fc` → scratch 库 → `pg_restore --no-owner`。

需要 PATH 里有 PostgreSQL 18 的 `pg_dump` / `pg_restore` / `psql`。
本机没有时：把三个命令的包装脚本目录放到 `PMSTUDIO_TEST_PG_BIN`，测试会前置到 PATH；
否则明确 skip（不拿 SQLite 顶替）。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
import pytest
from psycopg import connect as psycopg_connect
from scripts.init_db import finalize_database
from scripts.migrate import apply_migrations

from pmstudio.api.deps import SESSION_COOKIE
from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import AppSettings, OpsSettings
from pmstudio.infrastructure.postgres.database import connect
from tests.support.postgres import load_environment

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKUP = BACKEND_ROOT / "scripts" / "backup.sh"
RESTORE_CHECK = BACKEND_ROOT / "scripts" / "restore_check.sh"
PASSWORD = "backup-admin-pw"


def _replace_database(url: str, dbname: str) -> str:
    parts = urlsplit(url)
    # 新库要落在 public：测试 base URL 的 search_path 是测试 schema，不能带过去。
    return urlunsplit((parts.scheme, parts.netloc, f"/{dbname}", "", ""))


def _base_url() -> str:
    return load_environment().direct_url


def _tool_environment() -> dict[str, str]:
    env = dict(os.environ)
    override = env.get("PMSTUDIO_TEST_PG_BIN", "").strip()
    if override:
        env["PATH"] = override + os.pathsep + env.get("PATH", "")
    return env


def _require_pg18_tools() -> dict[str, str]:
    env = _tool_environment()
    for name in ("pg_dump", "pg_restore", "psql"):
        path = shutil.which(name, path=env["PATH"])
        if path is None:
            pytest.skip(f"缺 {name}；用 PMSTUDIO_TEST_PG_BIN 指向 PG18 客户端")
        output = subprocess.run(
            [path, "--version"], capture_output=True, text=True, check=False
        ).stdout
        if not output.startswith(f"{name} (PostgreSQL) 18."):
            pytest.skip(f"{name} 不是 18：{output.strip()}")
    return env


@pytest.fixture()
def database() -> Iterator[str]:
    base = _base_url()
    admin_url = _replace_database(base, "postgres")
    name = f"pmstudio_backup_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    with psycopg_connect(admin_url, autocommit=True) as connection:
        if not connection.execute(
            "SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"
        ).fetchone()[0]:
            pytest.skip("测试账号没有 CREATEDB，跑不了备份恢复用例")
        connection.execute(f'CREATE DATABASE "{name}"')
    url = _replace_database(base, name)
    try:
        apply_migrations(url)
        finalize_database(
            url,
            OpsSettings(
                direct_database_url=url,
                first_admin_username="backup-admin",
                first_admin_password=PASSWORD,
            ),
        )
        yield url
    finally:
        with psycopg_connect(admin_url, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def _login_and_create_project(url: str) -> tuple[str, str]:
    settings = AppSettings(
        database_url=url,
        model="openai/test-model",
        api_key="test-key",
        api_base=None,
    )
    app = build_container(settings).app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://backup") as client:
        login = await client.post(
            "/api/auth/login",
            json={"username": "backup-admin", "password": PASSWORD},
        )
        assert login.status_code == 200, login.text
        created = await client.post("/api/projects", json={"title": "备份演练项目"})
        assert created.status_code == 201, created.text
        cookie = login.cookies.get(SESSION_COOKIE) or ""
        assert cookie
        return cookie, created.json()["project_id"]


def _fingerprint(url: str) -> str:
    """恢复前后必须逐字一致；sessions 明确排除（恢复后按设计清空）。"""

    with connect(url) as connection:
        rows = connection.execute(
            """
            SELECT 'users' AS label, count(*)::text AS value FROM users
            UNION ALL SELECT 'documents', count(*)::text FROM documents
            UNION ALL SELECT 'document_versions', count(*)::text FROM document_versions
            UNION ALL SELECT 'project_titles',
              coalesce(md5(string_agg(project_id || ':' || title, '|' ORDER BY project_id)), '')
              FROM documents
            """
        ).fetchall()
    payload = "\n".join(f"{row['label']}|{row['value']}" for row in rows)
    return hashlib.sha256(payload.encode()).hexdigest()


def _make_scratch(base_url: str) -> tuple[str, str]:
    name = f"pmstudio_restore_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    admin_url = _replace_database(base_url, "postgres")
    with psycopg_connect(admin_url, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{name}"')
    return name, _replace_database(base_url, name)


def _drop_scratch(base_url: str, name: str) -> None:
    with psycopg_connect(_replace_database(base_url, "postgres"), autocommit=True) as connection:
        connection.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


async def test_backup_dump_and_restore_check(database: str, tmp_path: Path) -> None:
    env = _require_pg18_tools()
    cookie, _project = await _login_and_create_project(database)
    original = _fingerprint(database)

    backup_dir = tmp_path / "backups"
    completed = subprocess.run(
        ["bash", str(BACKUP)],
        env={
            **env,
            "PMSTUDIO_DIRECT_DATABASE_URL": database,
            "PMSTUDIO_BACKUP_DIR": str(backup_dir),
        },
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    dumps = sorted((backup_dir / "daily").glob("pmstudio-*.dump"))
    assert dumps, "backup.sh 没有产出 dump"
    dump = dumps[-1]
    assert dump.stat().st_mode & 0o777 == 0o600
    assert not list((backup_dir / "daily").glob("*.db")), "不许再产出 SQLite 备份"

    # 保留策略：7 日 + 4 周（用假旧文件验 prune，不用真等一周）。
    for index in range(8):
        fake = backup_dir / "daily" / f"pmstudio-2020010{index}T000000Z.dump"
        fake.write_bytes(b"old")
        fake.chmod(0o600)
    weekly_dir = backup_dir / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    for week in ("202001", "202002", "202003", "202004", "202005"):
        fake = weekly_dir / f"pmstudio-{week}.dump"
        fake.write_bytes(b"old")
        fake.chmod(0o600)
    again = subprocess.run(
        ["bash", str(BACKUP)],
        env={
            **env,
            "PMSTUDIO_DIRECT_DATABASE_URL": database,
            "PMSTUDIO_BACKUP_DIR": str(backup_dir),
        },
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode == 0, again.stderr
    assert len(list((backup_dir / "daily").glob("pmstudio-*.dump"))) == 7
    assert len(list(weekly_dir.glob("pmstudio-*.dump"))) == 4

    # 真跑恢复演练脚本（它自己再 dump 一次，再恢复、TRUNCATE sessions、指纹比对）。
    drill = subprocess.run(
        ["bash", str(RESTORE_CHECK)],
        env={
            **env,
            "PMSTUDIO_DIRECT_DATABASE_URL": database,
            "PMSTUDIO_BACKUP_DIR": str(tmp_path / "drill"),
        },
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drill.returncode == 0, drill.stdout + drill.stderr
    assert "PASS" in drill.stdout

    # 再用刚才的 dump 恢复一次，验证旧 cookie 在恢复后 401（sessions 已清空）。
    scratch_name, scratch_url = _make_scratch(database)
    try:
        with connect(scratch_url) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        restored = subprocess.run(
            [
                shutil.which("pg_restore", path=env["PATH"]),
                "--exit-on-error",
                "--no-owner",
                "--dbname",
                scratch_url,
                str(dump),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert restored.returncode == 0, restored.stderr
        with connect(scratch_url) as connection:
            connection.execute("TRUNCATE sessions")
        assert _fingerprint(scratch_url) == original

        settings = AppSettings(
            database_url=scratch_url,
            model="openai/test-model",
            api_key="test-key",
            api_base=None,
        )
        app = build_container(settings).app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://restored"
        ) as client:
            client.cookies.set(SESSION_COOKIE, cookie)
            me = await client.get("/api/auth/me")
        assert me.status_code == 401, "恢复后旧 cookie 仍然有效——sessions 没清空"
    finally:
        _drop_scratch(database, scratch_name)
