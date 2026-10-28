"""一次性的数据库初始化：bootstrap → schema-only → finalize。

三步各用各的连接，按需组合：

- `--bootstrap`：只用 `PMSTUDIO_BOOTSTRAP_DATABASE_URL`（超级用户）。建 vector 扩展、
  建 `pmstudio_owner` / `pmstudio_app` 角色、GRANT、写 PgBouncer auth_file。
- `--schema-only`：只用 `PMSTUDIO_DIRECT_DATABASE_URL`（owner）。跑编号迁移。
- `--finalize`：只用 `PMSTUDIO_DIRECT_DATABASE_URL`（owner）。首管理员 seed、
  owner 回填/断言、内置模板 seed、app 角色 GRANT、向量 profile upsert。
- `--all`：全新安装一次跑完三步。

迁移窗口里不要用 `--all`：先 `--bootstrap`、再 `--schema-only`、再 ETL、最后
`--finalize`。backend 自己**不**跑迁移、不 seed——它只认 app URL。

每一步都持 `pg_advisory_lock`（session 级），两个 init 同时跑时串行。
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import suppress
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from psycopg import sql  # noqa: E402
from psycopg.conninfo import conninfo_to_dict  # noqa: E402
from scripts.migrate import ADVISORY_LOCK_KEY, apply_migrations  # noqa: E402

from pmstudio.bootstrap.settings import (  # noqa: E402
    BootstrapSettings,
    ConfigError,
    OpsSettings,
)
from pmstudio.infrastructure.postgres.database import connect  # noqa: E402
from pmstudio.infrastructure.postgres.embedding import DIMENSIONS  # noqa: E402
from pmstudio.infrastructure.postgres.repositories import (  # noqa: E402
    SqliteTemplateRepository,
)
from pmstudio.infrastructure.postgres.vector_index import (  # noqa: E402
    ensure_vector_profile,
    read_vector_profile,
)
from pmstudio.infrastructure.security import hash_password  # noqa: E402
from pmstudio.infrastructure.templates.loader import seed_builtin_templates  # noqa: E402

OWNER_ROLE = "pmstudio_owner"
APP_ROLE = "pmstudio_app"

#: pg_search 扩展的 schema：`pdb` 是查询/评分函数，`paradedb` 是索引与分词函数。
PG_SEARCH_SCHEMAS: tuple[str, ...] = ("pdb", "paradedb")

#: 角色级 DB 超时（`ALTER ROLE ... SET`，不是 session SET / 连接池参数）。
#: app 是请求路径：锁等待 3s、语句 15s、泄漏事务 15s；owner 是迁移/维护/备份：给更宽的窗口。
APP_STATEMENT_TIMEOUT = "15s"
APP_LOCK_TIMEOUT = "3s"
APP_IDLE_IN_TRANSACTION_TIMEOUT = "15s"
OWNER_STATEMENT_TIMEOUT = "60s"
OWNER_LOCK_TIMEOUT = "10s"
OWNER_IDLE_IN_TRANSACTION_TIMEOUT = "60s"

#: 12 张项目域表 + `templates` + `round_skips`（与 SQLite 时代同一口径）。
#: `templates` 的 builtin 行 owner 留 NULL；其余表查全部 NULL。
OWNER_TABLES: tuple[str, ...] = (
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
    "round_skips",
)


def _session_lock(connection, *, wait: bool = True) -> bool:  # type: ignore[no-untyped-def]
    if wait:
        connection.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        return True
    row = connection.execute(
        "SELECT pg_try_advisory_lock(%s) AS locked", (ADVISORY_LOCK_KEY,)
    ).fetchone()
    return bool(row and row["locked"])


def _session_unlock(connection) -> None:  # type: ignore[no-untyped-def]
    with suppress(Exception):
        connection.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))


def _set_role(connection, role: str, password: str) -> None:  # type: ignore[no-untyped-def]
    exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)
    ).fetchone()
    if exists:
        connection.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )
    else:
        connection.execute(
            sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(password)
            )
        )


def _set_role_timeouts(
    connection,
    role: str,
    *,
    statement_timeout: str,
    lock_timeout: str,
    idle_in_transaction_session_timeout: str,
) -> None:  # type: ignore[no-untyped-def]
    """把超时写进角色默认值；新连接（含 PgBouncer 复用的服务端连接）才生效。

    刻意**不**用 session `SET`，也不改 PgBouncer 池参数：transaction mode 下
    session 设置会串到别的客户端；角色默认值才是每个新事务的稳定口径。
    """

    for setting, value in (
        ("statement_timeout", statement_timeout),
        ("lock_timeout", lock_timeout),
        (
            "idle_in_transaction_session_timeout",
            idle_in_transaction_session_timeout,
        ),
    ):
        connection.execute(
            sql.SQL("ALTER ROLE {} SET {} = {}").format(
                sql.Identifier(role), sql.SQL(setting), sql.Literal(value)
            )
        )


def _database_name(url: str) -> str:
    name = conninfo_to_dict(url).get("dbname")
    if not name:
        raise ConfigError("数据库 URL 里缺 dbname")
    return str(name)


def _grant_owned_objects(
    connection,  # type: ignore[no-untyped-def]
    schema: str,
    app_role: str,
    *,
    owner_role: str | None = None,
) -> None:
    """只把 owner 自己创建的表/序列/函数授给 app。

    官方 ParadeDB 镜像在 public 里预装 postgis / pg_stat_statements 等扩展对象，
    属主是 postgres。以 owner 身份执行 `GRANT ... ON ALL ... IN SCHEMA public`
    会因为对扩展对象没有 grant option 直接 42501；这些对象也不该授给 app。
    """

    if owner_role is None:
        relation_owner = (
            "c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
        )
        function_owner = (
            "p.proowner = (SELECT oid FROM pg_roles WHERE rolname = current_user)"
        )
        params: tuple[str, ...] = (schema,)
    else:
        relation_owner = "c.relowner = (SELECT oid FROM pg_roles WHERE rolname = %s)"
        function_owner = "p.proowner = (SELECT oid FROM pg_roles WHERE rolname = %s)"
        params = (schema, owner_role)

    relations = connection.execute(
        sql.SQL(
            """
            SELECT c.relname, c.relkind
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s
               AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
               AND """
        )
        + sql.SQL(relation_owner)
        + sql.SQL(" ORDER BY c.relname"),
        params,
    ).fetchall()
    for row in relations:
        name = str(row["relname"])
        if row["relkind"] == "S":
            connection.execute(
                sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {}.{} TO {}").format(
                    sql.Identifier(schema),
                    sql.Identifier(name),
                    sql.Identifier(app_role),
                )
            )
        else:
            connection.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {}.{} TO {}"
                ).format(
                    sql.Identifier(schema),
                    sql.Identifier(name),
                    sql.Identifier(app_role),
                )
            )

    functions = connection.execute(
        sql.SQL(
            """
            SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args
              FROM pg_proc p
              JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = %s
               AND p.prokind IN ('f', 'p', 'w')
               AND """
        )
        + sql.SQL(function_owner)
        + sql.SQL(" ORDER BY p.proname, p.oid"),
        params,
    ).fetchall()
    for row in functions:
        connection.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {}.{}({}) TO {}").format(
                sql.Identifier(schema),
                sql.Identifier(str(row["proname"])),
                sql.SQL(str(row["args"])),
                sql.Identifier(app_role),
            )
        )


def _write_auth_file(path: Path, entries: dict[str, str], *, uid: int) -> None:
    """把 PgBouncer auth_file 落成 0600 的只读 secret。

    条目是明文口令：`auth_type=scram-sha-256` 下 PgBouncer 会在加载时自己算
    SCRAM verifier；auth_file 本身不能进版本库。容器里 init 以 root 跑，
    pgbouncer 以 postgres(70) 跑，所以 chown 给能读它的 uid。
    """

    lines = []
    for user, password in entries.items():
        escaped = password.replace('"', '""')
        lines.append(f'"{user}" "{escaped}"\n')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        if os.geteuid() == 0:
            with suppress(PermissionError):
                os.chown(tmp, uid, uid)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            with suppress(OSError):
                tmp.unlink()


def bootstrap_database(settings: BootstrapSettings) -> None:
    """超级用户阶段：extension / 角色 / GRANT / PgBouncer auth。"""

    url = settings.bootstrap_database_url
    database = _database_name(url)
    with connect(url) as connection:
        _session_lock(connection)
        try:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            connection.execute("CREATE EXTENSION IF NOT EXISTS pg_search CASCADE")
            _set_role(connection, OWNER_ROLE, settings.owner_password)
            _set_role(connection, APP_ROLE, settings.app_password)
            _set_role_timeouts(
                connection,
                OWNER_ROLE,
                statement_timeout=OWNER_STATEMENT_TIMEOUT,
                lock_timeout=OWNER_LOCK_TIMEOUT,
                idle_in_transaction_session_timeout=OWNER_IDLE_IN_TRANSACTION_TIMEOUT,
            )
            _set_role_timeouts(
                connection,
                APP_ROLE,
                statement_timeout=APP_STATEMENT_TIMEOUT,
                lock_timeout=APP_LOCK_TIMEOUT,
                idle_in_transaction_session_timeout=APP_IDLE_IN_TRANSACTION_TIMEOUT,
            )

            # owner 负责 DDL/ETL；restore_check 要在同一 URL 上建 scratch 库。
            connection.execute(
                sql.SQL("ALTER ROLE {} WITH CREATEDB NOSUPERUSER NOCREATEROLE").format(
                    sql.Identifier(OWNER_ROLE)
                )
            )
            connection.execute(
                sql.SQL("ALTER ROLE {} WITH NOSUPERUSER NOCREATEDB NOCREATEROLE").format(
                    sql.Identifier(APP_ROLE)
                )
            )

            connection.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                    sql.Identifier(database), sql.Identifier(OWNER_ROLE)
                )
            )
            connection.execute(
                sql.SQL("ALTER SCHEMA public OWNER TO {}").format(
                    sql.Identifier(OWNER_ROLE)
                )
            )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(
                    sql.Identifier(database),
                    sql.Identifier(OWNER_ROLE),
                    sql.Identifier(APP_ROLE),
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(
                    sql.Identifier(OWNER_ROLE), sql.Identifier(APP_ROLE)
                )
            )
            connection.execute(
                sql.SQL("GRANT USAGE ON TYPE vector TO {}, {}").format(
                    sql.Identifier(APP_ROLE), sql.Identifier(OWNER_ROLE)
                )
            )

            # 之后由 owner 创建的表/序列/函数自动给 app 授权。
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(sql.Identifier(OWNER_ROLE), sql.Identifier(APP_ROLE))
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(sql.Identifier(OWNER_ROLE), sql.Identifier(APP_ROLE))
            )
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
                    "GRANT EXECUTE ON FUNCTIONS TO {}"
                ).format(sql.Identifier(OWNER_ROLE), sql.Identifier(APP_ROLE))
            )

            # 已有对象（重复 bootstrap / 迁移窗口）也补齐；只授 owner 自己的对象。
            _grant_owned_objects(
                connection, "public", APP_ROLE, owner_role=OWNER_ROLE
            )

            # pg_search 的查询/分词函数不在 public，显式给 app 与 owner 使用权限。
            for extension_schema in PG_SEARCH_SCHEMAS:
                connection.execute(
                    sql.SQL("GRANT USAGE ON SCHEMA {} TO {}, {}").format(
                        sql.Identifier(extension_schema),
                        sql.Identifier(APP_ROLE),
                        sql.Identifier(OWNER_ROLE),
                    )
                )
                connection.execute(
                    sql.SQL(
                        "GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}, {}"
                    ).format(
                        sql.Identifier(extension_schema),
                        sql.Identifier(APP_ROLE),
                        sql.Identifier(OWNER_ROLE),
                    )
                )
        finally:
            _session_unlock(connection)

    if settings.auth_file is not None:
        _write_auth_file(
            Path(settings.auth_file),
            {
                APP_ROLE: settings.app_password,
                OWNER_ROLE: settings.owner_password,
            },
            uid=settings.auth_uid,
        )


def schema_only(direct_url: str) -> list[int]:
    """owner 阶段：只跑编号迁移，不 seed。"""

    return apply_migrations(direct_url)


def _seed_first_admin(connection, settings: OpsSettings) -> None:  # type: ignore[no-untyped-def]
    row = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()
    if row is not None and int(row["n"]) > 0:
        return
    username = settings.first_admin_username
    password = settings.first_admin_password
    if not username or not password:
        raise ConfigError(
            "users 为空且没有配置首管理员：设置 PMSTUDIO_FIRST_ADMIN_USER 与 "
            "PMSTUDIO_FIRST_ADMIN_PASSWORD 后重跑 --finalize"
        )
    connection.execute(
        """
        INSERT INTO users (user_id, username, password_hash, status, role, created_at)
        VALUES (%s, %s, %s, 'active', 'admin', now())
        """,
        (f"u-{os.urandom(6).hex()}", username, hash_password(password)),
    )


def _backfill_owners(connection) -> int:  # type: ignore[no-untyped-def]
    admin = connection.execute(
        "SELECT user_id FROM users WHERE role = 'admin' ORDER BY seq LIMIT 1"
    ).fetchone()
    if admin is None:
        return 0
    admin_id = str(admin["user_id"])
    changed = 0
    for table in OWNER_TABLES:
        if table == "templates":
            cursor = connection.execute(
                "UPDATE templates SET owner_id = %s"
                " WHERE owner_id IS NULL AND builtin = false",
                (admin_id,),
            )
        else:
            cursor = connection.execute(
                f"UPDATE {table} SET owner_id = %s WHERE owner_id IS NULL",
                (admin_id,),
            )
        changed += cursor.rowcount or 0
    return changed


def _assert_owners(connection) -> None:  # type: ignore[no-untyped-def]
    orphans: dict[str, int] = {}
    for table in OWNER_TABLES:
        where = (
            "owner_id IS NULL AND builtin = false"
            if table == "templates"
            else "owner_id IS NULL"
        )
        row = connection.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {where}"
        ).fetchone()
        count = 0 if row is None else int(row["n"])
        if count:
            orphans[table] = count
    if orphans:
        detail = "、".join(f"{table} {n} 行" for table, n in orphans.items())
        raise RuntimeError(
            f"finalize 后仍有内容没有归属（{detail}）；请检查 owner 回填或管理员账号"
        )


def _grant_app_role(connection) -> None:  # type: ignore[no-untyped-def]
    exists = connection.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)
    ).fetchone()
    if exists is None:
        return
    row = connection.execute("SELECT current_schema() AS schema").fetchone()
    schema = "public" if row is None or row["schema"] is None else str(row["schema"])
    connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
            sql.Identifier(schema), sql.Identifier(APP_ROLE)
        )
    )
    _grant_owned_objects(connection, schema, APP_ROLE)


def _ensure_vector_profile(connection) -> None:  # type: ignore[no-untyped-def]
    profile = read_vector_profile(connection)
    if profile is not None and int(profile["dimensions"]) != DIMENSIONS:
        raise ValueError(
            f"vector_index_meta 维度 {profile['dimensions']} ≠ {DIMENSIONS}；"
            "换维度要显式迁移（ALTER COLUMN TYPE + 重建 HNSW），finalize 不做在线 ALTER"
        )
    ensure_vector_profile(connection)


def finalize_database(
    direct_url: str,
    settings: OpsSettings,
    *,
    seed_templates: bool = True,
) -> dict[str, int]:
    """owner 阶段：seed / 回填 / 断言 / GRANT / 向量 profile。"""

    result = {"owners": 0, "templates": 0}
    with connect(direct_url) as connection:
        _session_lock(connection)
        try:
            _seed_first_admin(connection, settings)
            result["owners"] = _backfill_owners(connection)
            _assert_owners(connection)
            if seed_templates:
                result["templates"] = len(
                    seed_builtin_templates(SqliteTemplateRepository(connection))
                )
            _grant_app_role(connection)
            _ensure_vector_profile(connection)
        finally:
            _session_unlock(connection)
    return result


def _bootstrap_settings_from_env() -> BootstrapSettings:
    return BootstrapSettings.from_env()


def _ops_settings_from_env() -> OpsSettings:
    return OpsSettings.from_env()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PM Studio PostgreSQL 初始化")
    parser.add_argument("--bootstrap", action="store_true", help="extension/角色/GRANT/auth_file")
    parser.add_argument("--schema-only", action="store_true", help="跑编号迁移")
    parser.add_argument("--finalize", action="store_true", help="seed/回填/断言/向量 profile")
    parser.add_argument("--all", action="store_true", help="全新安装：bootstrap→schema→finalize")
    args = parser.parse_args(argv)

    selected = [args.bootstrap, args.schema_only, args.finalize, args.all]
    if sum(1 for item in selected if item) != 1:
        parser.error("必须且只能选一个：--bootstrap / --schema-only / --finalize / --all")

    try:
        if args.all or args.bootstrap:
            bootstrap = _bootstrap_settings_from_env()
            bootstrap_database(bootstrap)
            print("bootstrap：extension / 角色 / GRANT / auth_file 完成", flush=True)

        if args.all or args.schema_only:
            ops = _ops_settings_from_env()
            applied = schema_only(ops.direct_database_url)
            print(
                "schema-only："
                + ("已应用 " + "、".join(f"{v:04d}" for v in applied) if applied else "已是最新"),
                flush=True,
            )

        if args.all or args.finalize:
            ops = _ops_settings_from_env()
            result = finalize_database(ops.direct_database_url, ops)
            print(
                f"finalize：owner 回填 {result['owners']} 行 / 内置模板 {result['templates']} 个"
                " / 向量 profile 512 维",
                flush=True,
            )
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 —— 命令行给出非零退出与原因
        print(f"初始化失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
