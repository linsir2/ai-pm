"""执行编号 SQL 迁移（只用直连 URL，不经 PgBouncer）。

约定：

- 文件名 `NNNN_name.sql`，按编号升序执行；重复编号直接报错；
- 每个文件的 DDL 与它写入 `schema_migrations` 的账本行在**同一事务**里提交；
- 整个执行过程持有 `pg_advisory_lock`，两个 init 同时跑时串行；
- 已记账的版本跳过，所以重复执行是幂等的，不会重放 DDL。
"""

from __future__ import annotations

import argparse
import re
import sys
from contextlib import suppress
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
MIGRATIONS_DIR = BACKEND_ROOT / "migrations"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pmstudio.bootstrap.settings import ConfigError, OpsSettings  # noqa: E402
from pmstudio.infrastructure.postgres.database import connect  # noqa: E402

ADVISORY_LOCK_KEY = 0x504D53544D494752
_MIGRATION_NAME = re.compile(r"^(\d{4})_[A-Za-z0-9_]+\.sql$")
_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def discover_migrations(directory: Path) -> list[tuple[int, Path]]:
    """列出编号 SQL；编号重复/为空时明确报错，不猜顺序。"""

    found: dict[int, Path] = {}
    for path in sorted(directory.iterdir()):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            continue
        version = int(match.group(1))
        if version in found:
            raise ValueError(
                f"迁移编号重复：{found[version].name} 与 {path.name} 都是 {version:04d}"
            )
        found[version] = path
    return sorted(found.items())


def apply_migrations(url: str, *, directory: Path = MIGRATIONS_DIR) -> list[int]:
    """把尚未记账的迁移按序落库，返回本次实际执行的版本号。"""

    migrations = discover_migrations(directory)
    applied: list[int] = []
    with connect(url) as connection:
        connection.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        try:
            connection.execute(_LEDGER)
            done = {
                int(row["version"])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall()
            }
            for version, path in migrations:
                if version in done:
                    continue
                sql = path.read_text(encoding="utf-8")
                with connection.transaction():
                    connection.execute(sql)
                    connection.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)",
                        (version,),
                    )
                applied.append(version)
        finally:
            with suppress(Exception):
                connection.execute(
                    "SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,)
                )
    return applied


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="执行 PostgreSQL 编号迁移")
    parser.add_argument(
        "--database-url",
        default="",
        help="直连 PostgreSQL；缺省读 PMSTUDIO_DIRECT_DATABASE_URL",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=MIGRATIONS_DIR,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)

    if args.database_url:
        url = args.database_url
    else:
        try:
            url = OpsSettings.from_env().direct_database_url
        except ConfigError as error:
            print(error, file=sys.stderr)
            return 2

    try:
        applied = apply_migrations(url, directory=args.migrations_dir)
    except Exception as error:  # noqa: BLE001 —— 命令行要给出非零退出与原因
        print(f"迁移失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1

    if applied:
        print("已应用迁移：" + "、".join(f"{version:04d}" for version in applied))
    else:
        print("数据库已是最新，无需迁移")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
