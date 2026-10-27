"""批 5 合成证据：上传 → 向量已落 → 混合检索命中 → 删除后清干净。

跑的是**真装配**（真 SQLite、真材料仓、真向量索引、真用例），
只把 embedder 换成 `FakeEmbedder`——真模型本环境下不下来（见 Task 1）。
**不碰真库**：库文件是临时目录里的副本。

用法：`uv run python scripts/vector_acceptance.py`
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pmstudio.application.use_cases import DeleteMaterial, UploadMaterial  # noqa: E402
from pmstudio.domain.shared.ids import ProjectId, UserId  # noqa: E402
from pmstudio.infrastructure.sqlite import database  # noqa: E402
from pmstudio.infrastructure.sqlite.materials import (  # noqa: E402
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.owner_scope import set_owner_scope  # noqa: E402
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork  # noqa: E402
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex  # noqa: E402
from pmstudio.infrastructure.tools.extract import LocalMaterialParser  # noqa: E402
from tests.support.embedding import FakeEmbedder  # noqa: E402

PROJECT = ProjectId("p-accept")
OWNER = "u-accept"

# 三份合成材料：覆盖"词法能中"、"换个说法只能靠语义"两种情形
DOCS = {
    "退货说明.md": "# 退货\n用户下单后七天内可以无理由退货，退货运费由用户承担。",
    "机柜布线.md": "# 布线\n机柜内网线走背板槽位，电源线与信号线必须分开走。",
    "验收标准.md": "# 验收\n导出的文件要带权限戳，导出记录要能追溯到人。",
}


class _Docs:
    def get(self, project_id):  # noqa: ANN001, ANN201
        return None


class _Index:
    def material_references(self, project_id):  # noqa: ANN001, ANN201
        return {}


class _Clock:
    def now(self):  # noqa: ANN201
        return datetime.now(UTC)


def _vec_rows(connection) -> int:
    return connection.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0]


def _line(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "acceptance.db"
        set_owner_scope(UserId(OWNER))
        embedder = FakeEmbedder(dimensions=512)
        connection = database.connect(db_path, embedder=embedder)
        connection.execute(
            "INSERT OR IGNORE INTO users (user_id, password_hash, created_at)"
            " VALUES (?, 'x', '2026-10-19T00:00:00+00:00')",
            (OWNER,),
        )
        store = SqliteMaterialStore(connection, ids=_SeqIds())
        vectors = SqliteVectorIndex(connection, embedder=embedder)
        uow = SqliteUnitOfWork(connection)
        parser = LocalMaterialParser()
        search = SqliteHybridRetriever(connection, embedder=embedder)

        _line(f"库：{db_path}")
        _line(f"迁移后 user_version = {connection.execute('PRAGMA user_version').fetchone()[0]}")

        # ── 1. 上传三份材料 ────────────────────────────────────────────────
        saved = {}
        async def upload_all():
            for name, body in DOCS.items():
                view = await UploadMaterial(
                    documents=_Docs(),
                    materials=store,
                    parser=parser,
                    clock=_Clock(),
                    uow=uow,
                    vectors=vectors,
                )(project_id=PROJECT, filename=name,
                  media_type="text/markdown", data=body.encode())
                saved[name] = view
                _line(f"  上传 {name} → {view.material_id}（{view.chunk_count} 块）")

        asyncio.run(upload_all())

        total_chunks = sum(v.chunk_count for v in saved.values())
        _line(f"\n[1] 向量已落：{_vec_rows(connection)} / 块数 {total_chunks}")
        assert _vec_rows(connection) == total_chunks, "向量条数与块数对不上"

        # ── 2. 词法检索（升级前就能做到的） ────────────────────────────────
        hits = search.search(project_id=PROJECT, query="导出权限戳", owner=OWNER)
        _line(f"[2] 词法查询「导出权限戳」→ {[h.title for h in hits]}")
        assert hits, "词法查询空了——比升级前还差"
        assert hits[0].title == "验收标准.md", hits[0].title

        # ── 3. 语义检索（升级才有的） ──────────────────────────────────────
        hits = search.search(project_id=PROJECT, query="网线怎么排布", owner=OWNER)
        _line(f"[3] 语义查询「网线怎么排布」→ {[h.title for h in hits]}")
        assert hits, "语义查询空"

        # ── 4. 隔离：换个项目什么都查不到 ──────────────────────────────────
        other = search.search(project_id=ProjectId("p-other"), query="导出权限戳", owner=OWNER)
        _line(f"[4] 换项目查询「导出权限戳」→ {len(other)} 条（应为 0）")
        assert other == (), "跨项目泄漏"

        # ── 5. 删除：向量跟着块一起清干净 ──────────────────────────────────
        async def delete_one():
            await DeleteMaterial(
                materials=store, parser=parser, index=_Index(), uow=uow, vectors=vectors
            )(project_id=PROJECT, material_id=saved["退货说明.md"].material_id)

        before = _vec_rows(connection)
        asyncio.run(delete_one())
        after = _vec_rows(connection)
        _line(f"[5] 删除一份材料：向量 {before} → {after}（少 {before - after} 条）")
        assert after == before - saved["退货说明.md"].chunk_count, "删除没带走对应向量"

        remaining_chunks = connection.execute(
            "SELECT count(*) FROM material_chunks").fetchone()[0]
        assert remaining_chunks == after, "向量数与剩余块数不一致（孤儿向量）"

        connection.close()

    _line("\n全部判据通过。")
    return 0


class _SeqIds:
    def __init__(self) -> None:
        self._n = 0

    def new(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}{self._n:04d}"


if __name__ == "__main__":
    raise SystemExit(main())