"""独立的材料维护 job（直连 PostgreSQL，不走 PgBouncer）。

backend 启动时**不再**跑材料维护：它要按材料逐个设 owner 作用域，还要重算向量，
属于"跨账号的运维动作"，不该跟 web 进程的生命周期绑在一起。compose 把它做成
一次性 service；重复触发/并发触发时用 `pg_try_advisory_lock` 让后来者**直接跳过**
（不是排队等到 web 请求超时）。

用法：

    cd backend && .venv/bin/python scripts/maintenance_worker.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pmstudio.bootstrap.maintenance import MaterialMaintenance  # noqa: E402
from pmstudio.bootstrap.settings import ConfigError, OpsSettings  # noqa: E402
from pmstudio.infrastructure.postgres import owner_scope  # noqa: E402
from pmstudio.infrastructure.postgres.database import connect  # noqa: E402
from pmstudio.infrastructure.postgres.embedding import FastEmbedEmbedder  # noqa: E402
from pmstudio.infrastructure.postgres.materials import SqliteMaterialStore  # noqa: E402
from pmstudio.infrastructure.postgres.repositories import (  # noqa: E402
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.postgres.vector_index import (  # noqa: E402
    SqliteVectorIndex,
)
from pmstudio.infrastructure.tools.extract import LocalMaterialParser  # noqa: E402

#: 维护 job 的 advisory lock key（与 init/migrate 的 key 不同：它们可以并行）。
MAINTENANCE_LOCK_KEY = 0x504D53544D41494E


class _UuidIds:
    def new(self, prefix: str) -> str:
        import uuid

        return f"{prefix}-{uuid.uuid4().hex[:8]}"


def run() -> int:
    try:
        settings = OpsSettings.from_env()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2

    connection = connect(settings.direct_database_url)
    try:
        row = connection.execute(
            "SELECT pg_try_advisory_lock(%s) AS locked", (MAINTENANCE_LOCK_KEY,)
        ).fetchone()
        if row is None or not row["locked"]:
            print("材料维护：已有实例在跑，本次跳过", flush=True)
            return 0
        try:
            parser = LocalMaterialParser()
            store = SqliteMaterialStore(connection, ids=_UuidIds())
            vectors = SqliteVectorIndex(connection, embedder=FastEmbedEmbedder())
            task = MaterialMaintenance(
                connection=connection,
                parser=parser,
                store=store,
                vectors=vectors,
                owner_scope=owner_scope,
                index=SqliteWorkspaceIndex(connection),
            )
            refreshed = asyncio.run(task.run_once())
            print(f"材料维护：重切 {refreshed} 份", flush=True)
        finally:
            connection.execute(
                "SELECT pg_advisory_unlock(%s)", (MAINTENANCE_LOCK_KEY,)
            )
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
