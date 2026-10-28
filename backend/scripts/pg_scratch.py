"""验收脚本共用的一次性 PostgreSQL schema。

旧脚本习惯"造一个临时 SQLite 文件、跑完删掉"。切 PG 后同一纪律落成
**临时 schema**：用 app/direct 两个 URL 的副本，把 `search_path` 指到
`pmstudio_scratch_<pid>_<随机>`，跑迁移 + finalize，退出时 `DROP SCHEMA ... CASCADE`。

- 读 `.env` 与 `.env.ops`（已存在的环境变量优先），与 `scripts/dev.py` 同语义；
- app URL 必须真的存在（backend 只认它），direct URL 只给建/删 schema、迁移与直读证据；
- 缺 URL 直接退出，不回落 SQLite。
"""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from scripts.init_db import finalize_database, schema_only

from pmstudio.bootstrap.settings import ConfigError, OpsSettings
from pmstudio.infrastructure.postgres.database import connect

BACKEND_ROOT = Path(__file__).resolve().parents[1]
APP_URL_ENV = "PMSTUDIO_DATABASE_URL"
DIRECT_URL_ENV = "PMSTUDIO_DIRECT_DATABASE_URL"
_SEARCH_PATH = re.compile(r"-c\s*search_path=\S+")


@dataclass(frozen=True, slots=True)
class ScratchDatabase:
    schema: str
    app_url: str
    direct_url: str


def load_env_files(backend_root: Path = BACKEND_ROOT) -> dict[str, str]:
    """读 `backend/.env` 与 `backend/.env.ops`；已在环境里的值优先。"""

    values: dict[str, str] = {}
    for name in (".env", ".env.ops"):
        path = backend_root / name
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    values.update({key: value for key, value in os.environ.items() if key.startswith("PMSTUDIO_")})
    return values


def _replace_search_path(options: str, schema: str) -> str:
    kept = _SEARCH_PATH.sub("", options or "").strip()
    wanted = f"-csearch_path={schema},public"
    return f"{kept} {wanted}".strip()


def schema_url(url: str, schema: str) -> str:
    """把 URL 的 search_path 指向给定 schema，保留其它 query 参数。"""

    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"数据库 URL 必须是 postgresql://，现在是 {parts.scheme!r}")
    query = parse_qsl(parts.query, keep_blank_values=True)
    options = next((value for key, value in query if key == "options"), "")
    rest = [(key, value) for key, value in query if key != "options"]
    rest.append(("options", _replace_search_path(options, schema)))
    encoded = "&".join(
        f"{quote(key, safe='')}={quote(value, safe=',/_-.:')}" for key, value in rest
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))


def _base_urls(env: Mapping[str, str]) -> tuple[str, str]:
    app_url = str(env.get(APP_URL_ENV, "")).strip()
    direct_url = str(env.get(DIRECT_URL_ENV, "")).strip()
    missing = [
        name
        for name, value in ((APP_URL_ENV, app_url), (DIRECT_URL_ENV, direct_url))
        if not value
    ]
    if missing:
        raise ConfigError("缺验收数据库 URL：" + "、".join(missing))
    return app_url, direct_url


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def make_scratch(
    *,
    prefix: str,
    env: Mapping[str, str] | None = None,
    admin_user: str | None = None,
    admin_password: str | None = None,
) -> ScratchDatabase:
    values = load_env_files() if env is None else dict(env)
    app_base, direct_base = _base_urls(values)
    schema = f"pmstudio_{prefix}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    with connect(direct_base) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        connection.execute("CREATE EXTENSION IF NOT EXISTS pg_search CASCADE")
        connection.execute(f"CREATE SCHEMA {_quote_identifier(schema)}")
        # 临时 schema 必须给 app 角色 USAGE；表/序列授权由 finalize 按 current_schema()
        # 补齐（`init_db._grant_app_role`）。没有真 app 角色时跳过。
        app_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = 'pmstudio_app'"
        ).fetchone()
        if app_exists is not None:
            connection.execute(
                f"GRANT USAGE ON SCHEMA {_quote_identifier(schema)} TO pmstudio_app"
            )
    direct_url = schema_url(direct_base, schema)
    app_url = schema_url(app_base, schema)
    try:
        schema_only(direct_url)
        settings = OpsSettings(
            direct_database_url=direct_url,
            first_admin_username=admin_user or values.get("PMSTUDIO_FIRST_ADMIN_USER") or None,
            first_admin_password=(
                admin_password or values.get("PMSTUDIO_FIRST_ADMIN_PASSWORD") or None
            ),
        )
        finalize_database(direct_url, settings)
    except Exception:
        drop_scratch(ScratchDatabase(schema, app_url, direct_url), direct_base=direct_base)
        raise
    return ScratchDatabase(schema=schema, app_url=app_url, direct_url=direct_url)


def drop_scratch(database: ScratchDatabase, *, direct_base: str | None = None) -> None:
    """尽力删掉临时 schema；删不掉不掩盖主流程的错误。"""

    base = direct_base
    if base is None:
        values = load_env_files()
        base = str(values.get(DIRECT_URL_ENV, "")).strip()
    if not base:
        return
    try:
        with connect(base) as connection:
            connection.execute("SET lock_timeout = '2s'")
            connection.execute(
                f"DROP SCHEMA IF EXISTS {_quote_identifier(database.schema)} CASCADE"
            )
    except Exception:  # noqa: BLE001 —— 清理失败留给下一次/人工
        pass


@contextmanager
def scratch_database(
    *, prefix: str, admin_user: str | None = None, admin_password: str | None = None
):
    database = make_scratch(
        prefix=prefix, admin_user=admin_user, admin_password=admin_password
    )
    try:
        yield database
    finally:
        drop_scratch(database)
