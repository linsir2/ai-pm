"""真 PostgreSQL 测试库夹具（一个 test database + 一个 test schema）。

T1 只提供骨架；T8 会把这里的 helper 接进 `tests/conftest.py` 并让全量测试直连 PG。
约定：

- `PMSTUDIO_TEST_DATABASE_URL`（app / PgBouncer）与
  `PMSTUDIO_TEST_DIRECT_DATABASE_URL`（直连）都必须给；缺了直接 `pytest.exit`，
  **不回落 SQLite**。
- 两个 URL 都改写成 `options=-csearch_path=<schema>,public`，并保留 URL 里原有
  的其他 options（连接层再合并 `-c timezone=UTC`）。
- 测试间只 truncate 显式数据表；`schema_migrations` 与
  `vector_index_meta` 不清（迁移与向量口径要跨用例保留）。
"""

from __future__ import annotations

import atexit
import os
import re
import uuid
from collections.abc import Iterator, Mapping
from contextlib import suppress
from dataclasses import dataclass, replace
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import pytest
from psycopg import OperationalError
from psycopg.conninfo import conninfo_to_dict

from pmstudio.infrastructure.postgres.database import connect

APP_URL_ENV = "PMSTUDIO_TEST_DATABASE_URL"
DIRECT_URL_ENV = "PMSTUDIO_TEST_DIRECT_DATABASE_URL"
SCHEMA_ENV = "PMSTUDIO_TEST_SCHEMA"
DEFAULT_SCHEMA = "pmstudio_test"

#: 一次 pytest 进程一个后缀：并发跑同一批文件时，模块专用 schema 也必须互不重叠。
_RUN_SUFFIX = f"{os.getpid()}_{uuid.uuid4().hex[:8]}"

#: 18 张显式数据表（19 张普通表减掉 `vector_index_meta`）。测试间整批清空。
#: 未知/临时表（例如 T1 的 `uow_probe`）不在这里，不会被顺手 TRUNCATE。
DATA_TABLES = (
    "documents",
    "document_versions",
    "rounds",
    "round_skips",
    "templates",
    "evidence_ledgers",
    "materials",
    "material_chunks",
    "explorations",
    "discussions",
    "project_memory",
    "model_calls",
    "tool_calls",
    "observations",
    "round_digests",
    "users",
    "sessions",
    "inspirations",
)

#: 这两张表是夹具的"基础设施"：迁移账本与向量口径。测试间不清。
PRESERVED_TABLES = frozenset({"schema_migrations", "vector_index_meta"})

_SEARCH_PATH = re.compile(r"-c\s*search_path=\S+")


@dataclass(frozen=True, slots=True)
class PgTestDatabase:
    app_url: str
    direct_url: str
    schema: str

    def environment_for_children(self) -> dict[str, str]:
        """子进程环境：测试变量与生产变量**双写**，且不覆盖其他环境变量。"""

        env = dict(os.environ)
        env.update(
            {
                APP_URL_ENV: self.app_url,
                DIRECT_URL_ENV: self.direct_url,
                SCHEMA_ENV: self.schema,
                "PMSTUDIO_DATABASE_URL": self.app_url,
                "PMSTUDIO_DIRECT_DATABASE_URL": self.direct_url,
            }
        )
        return env


def _safe_label(url: str) -> str:
    """只打印 host/port/dbname/user——密码不出现。"""

    try:
        info = conninfo_to_dict(url)
    except Exception:  # noqa: BLE001 —— 解析失败也不要回显原串（可能带密码）
        return "(URL 解析失败)"
    host = info.get("host", "?")
    port = info.get("port", "?")
    dbname = info.get("dbname", "?")
    user = info.get("user", "?")
    return f"{user}@{host}:{port}/{dbname}"


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _replace_search_path(options: str, schema: str) -> str:
    """保留其他 options，只把 search_path 换成 `<schema>,public`。"""

    kept = _SEARCH_PATH.sub("", options or "").strip()
    wanted = f"-csearch_path={schema},public"
    return f"{kept} {wanted}".strip()


def schema_url(url: str, schema: str) -> str:
    """把 URL 的 search_path 指向测试 schema，其他 query 参数原样保留。"""

    parts = urlsplit(url)
    if parts.scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"测试库 URL 必须是 postgresql://，现在是 {parts.scheme!r}")
    query = parse_qsl(parts.query, keep_blank_values=True)
    options = next((value for key, value in query if key == "options"), "")
    rest = [(key, value) for key, value in query if key != "options"]
    rest.append(("options", _replace_search_path(options, schema)))
    # `=` 要编码成 %3D；逗号保留可读（与设计里的示例一致）。
    encoded = "&".join(
        f"{quote(key, safe='')}={quote(value, safe=',/_-.:')}" for key, value in rest
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, encoded, parts.fragment))


def load_environment(env: Mapping[str, str] | None = None) -> PgTestDatabase:
    """读取两个测试 URL；缺了就 `pytest.exit`（不是 skip，也不是回落）。"""

    source = os.environ if env is None else env
    app_base = source.get(APP_URL_ENV, "").strip()
    direct_base = source.get(DIRECT_URL_ENV, "").strip()
    missing = [
        name
        for name, value in ((APP_URL_ENV, app_base), (DIRECT_URL_ENV, direct_base))
        if not value
    ]
    if missing:
        pytest.exit(
            "缺测试数据库 URL：" + "、".join(missing)
            + "。起一个 pgvector/pgvector:0.8.7-pg18-trixie，再导出两个 URL；"
            "不回落 SQLite。",
            returncode=2,
        )
    schema = source.get(SCHEMA_ENV, "").strip() or DEFAULT_SCHEMA
    return PgTestDatabase(
        app_url=schema_url(app_base, schema),
        direct_url=schema_url(direct_base, schema),
        schema=schema,
    )


def isolated_schema(database: PgTestDatabase, suffix: str) -> PgTestDatabase:
    """在同一个测试库里派生一个**模块 + 进程专用 schema**。

    设计里的默认口径仍是一个 test DB + 一个 schema（pytest 串行）；这个 helper 只给
    T6 的租约/多进程用例用：多个 pytest 进程并行跑**同一批文件**时，不能互相
    `TRUNCATE` 对方的行。进程号 + 随机后缀保证每次运行独立，退出时尽力清掉。
    T8 统一测试夹具时可以删掉。
    """

    schema = f"{database.schema}_{suffix}_{_RUN_SUFFIX}"
    isolated = replace(
        database,
        schema=schema,
        app_url=schema_url(database.app_url, schema),
        direct_url=schema_url(database.direct_url, schema),
    )
    _ISOLATED.append(isolated)
    return isolated


_ISOLATED: list[PgTestDatabase] = []


def _drop_isolated_schemas() -> None:
    while _ISOLATED:
        database = _ISOLATED.pop()
        with suppress(Exception):
            with connect(database.direct_url) as connection:
                # 不跟残留线程/连接死等：清不掉就留给下次容器重建。
                connection.execute("SET lock_timeout = '2s'")
                connection.execute(
                    f"DROP SCHEMA IF EXISTS {_quote_identifier(database.schema)} CASCADE"
                )


atexit.register(_drop_isolated_schemas)


def prepare(database: PgTestDatabase) -> None:
    """建 schema、确保 pgvector 扩展可用，并断言 current_schema / timezone。"""

    schema = _quote_identifier(database.schema)
    try:
        with connect(database.direct_url) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            row = connection.execute("SELECT current_schema() AS schema").fetchone()
            assert row is not None and row["schema"] == database.schema
            row = connection.execute("SHOW timezone").fetchone()
            assert row is not None and row["TimeZone"] == "UTC"
            row = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            assert row is not None, "pgvector 扩展不可用"
    except OperationalError as error:
        pytest.exit(
            f"连不上测试数据库 {_safe_label(database.direct_url)}：{error}",
            returncode=2,
        )

    with connect(database.app_url) as connection:
        row = connection.execute("SELECT current_schema() AS schema").fetchone()
        assert row is not None and row["schema"] == database.schema, (
            "app URL 的 options 没把 search_path 指到测试 schema"
        )


def _table_names(connection, schema: str) -> list[str]:  # type: ignore[no-untyped-def]
    rows = connection.execute(
        """
        SELECT table_name
          FROM information_schema.tables
         WHERE table_schema = %s
           AND table_type = 'BASE TABLE'
         ORDER BY table_name
        """,
        (schema,),
    ).fetchall()
    return [str(row["table_name"]) for row in rows]


def truncate_data_tables(database: PgTestDatabase) -> None:
    """清空测试 schema 里的 18 张显式数据表；保留迁移账本与向量口径。"""

    with connect(database.direct_url) as connection:
        existing = set(_table_names(connection, database.schema))
        names = [name for name in DATA_TABLES if name in existing]
        if not names:
            return
        qualified = ", ".join(
            f"{_quote_identifier(database.schema)}.{_quote_identifier(name)}"
            for name in names
        )
        connection.execute(f"TRUNCATE TABLE {qualified} RESTART IDENTITY CASCADE")


def upsert_vector_profile(
    database: PgTestDatabase,
    *,
    profile: str = "bge-small-zh-v1.5",
    dimensions: int = 512,
    model: str = "Qdrant/bge-small-zh-v1.5",
) -> None:
    """truncate 后恢复向量口径（T2 建表后生效；T1 没这张表就跳过）。"""

    with connect(database.direct_url) as connection:
        exists = connection.execute(
            "SELECT to_regclass(%s) AS table_name",
            (f"{_quote_identifier(database.schema)}.vector_index_meta",),
        ).fetchone()
        if exists is None or exists["table_name"] is None:
            return
        connection.execute(
            """
            INSERT INTO vector_index_meta (id, profile, dimensions, model)
            VALUES (1, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE
               SET profile = EXCLUDED.profile,
                   dimensions = EXCLUDED.dimensions,
                   model = EXCLUDED.model
            """,
            (profile, dimensions, model),
        )


@pytest.fixture(scope="session")
def pg_test_database() -> PgTestDatabase:
    database = load_environment()
    prepare(database)
    return database


@pytest.fixture()
def pg_direct_connection(pg_test_database: PgTestDatabase) -> Iterator[object]:
    truncate_data_tables(pg_test_database)
    upsert_vector_profile(pg_test_database)
    with connect(pg_test_database.direct_url) as connection:
        yield connection
    truncate_data_tables(pg_test_database)
    upsert_vector_profile(pg_test_database)
