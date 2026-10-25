"""P（生产·真跑·slow）：真 SQLite + 真 `SqliteHybridRetriever` + 真 fastembed。

这条是 P2.9 的硬证据：语义那条材料**与查询没有任何词面重合**——纯 FTS5 查不到它，
只有向量分支能把它带进 SEAT 投影。不这样做的话，"FTS 也能查到"会让测试绿着但
什么都没证明（见 `test_round_context_pool_e2e.py` 的 FTS e2e）。

**只在显式 `-m slow` 时跑**：首次运行会下载/加载 `BAAI/bge-small-zh-v1.5`（~90MB）。
依赖或 sqlite-vec 扩展缺失时 `skip` 并写明原因；但**向量写入后
`material_chunks_vec` 的条数必须 > 0**——写入失败是错，不是跳过。
"""

import hashlib

import pytest

from pmstudio.application.dto import SEARCH_LIMIT, MaterialChunk, ParsedMaterial
from pmstudio.application.round_context import RoundContextPreparer
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.round.retrieval import RetrievalStage
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteEvidenceRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from tests.support.builders import NOW, PROJECT, ROUND_ID
from tests.support.doubles import FixedClock

#: 语义查询：换成"人话"的防误删需求。
QUERY = "如何防止用户误删数据"
#: 词面能命中的那条（query token 原样在正文里）。
LEXICAL_TEXT = "防止误删要在删除前弹二次确认，这是最常见的前端保护。"
#: **只能靠语义命中**的那条：与 QUERY 的 token 集交集为空。
SEMANTIC_TEXT = "回收站与二次确认可以避免手滑清空记录。"


def put(store: SqliteMaterialStore, text: str, title: str):
    parsed = ParsedMaterial(
        origin="upload",
        title=title,
        source_ref=f"attachment:{title}",
        media_type="text/markdown",
        bytes_len=len(text.encode("utf-8")),
        chunks=(
            MaterialChunk(
                chunk_id="c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="第 1 行",
            ),
        ),
        parser_id="local-text",
        parser_version="v1",
    )
    content = text.encode("utf-8")
    return store.put(
        project_id=PROJECT,
        parsed=parsed,
        sha256=hashlib.sha256(content).hexdigest(),
        content=content,
        now=NOW,
    )


@pytest.mark.slow
def test_a_semantic_only_material_reaches_the_seat_projection(tmp_path):
    pytest.importorskip("fastembed", reason="真 hybrid 慢测需要 fastembed")
    pytest.importorskip("sqlite_vec", reason="真 hybrid 慢测需要 sqlite-vec")

    connection = connect(tmp_path / "hybrid.db")
    try:
        has_vec_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='material_chunks_vec'"
        ).fetchone()
        if has_vec_table is None:
            pytest.skip("sqlite-vec 扩展加载失败：material_chunks_vec 未建成")

        ids = UuidIdGenerator()
        store = SqliteMaterialStore(connection, ids=ids)
        SqliteProjectFactory(connection, ids=ids).create(
            project_id=PROJECT, title="真 hybrid e2e", now=NOW, template=standard_template()
        )
        lexical = put(store, LEXICAL_TEXT, "删除保护.md")
        semantic = put(store, SEMANTIC_TEXT, "回收站.md")
        lexical_chunk = lexical.parsed.chunks[0].chunk_id
        semantic_chunk = semantic.parsed.chunks[0].chunk_id

        # **真模型**：向量索引与查询共用同一个 embedder（懒加载，只在这次真跑里付 ONNX）。
        embedder = FastEmbedEmbedder()
        vector_index = SqliteVectorIndex(connection, embedder=embedder)
        vector_index.write(lexical.material_id)
        vector_index.write(semantic.material_id)

        stored = connection.execute(
            "SELECT COUNT(*) FROM material_chunks_vec"
        ).fetchone()[0]
        assert stored > 0, "向量写入失败（0 条）——这不许用 skip 冒充绿"
        assert connection.execute(
            "SELECT 1 FROM material_chunks_vec WHERE chunk_id = ?",
            (semantic_chunk,),
        ).fetchone(), "语义那条的向量没写进去"

        # 前置鉴别力：纯 FTS5 查不到语义那条。否则"混合检索命中"可能是词面兜的底。
        fts_hits = SqliteFtsRetriever(connection).search(
            project_id=PROJECT, query=QUERY, limit=SEARCH_LIMIT
        )
        fts_chunks = {hit.chunk_id for hit in fts_hits}
        assert lexical_chunk in fts_chunks, "词面那条都查不到——语料/查询没有鉴别力"
        assert semantic_chunk not in fts_chunks, (
            "语义那条被纯 FTS5 命中了——这条测试证明不了向量分支"
        )

        # 生产检索器：`SqliteHybridRetriever`（不是手工拼 FTS + 向量）。
        retriever = SqliteHybridRetriever(connection, embedder=embedder)
        hybrid_chunks = {
            hit.chunk_id
            for hit in retriever.search(
                project_id=PROJECT, query=QUERY, limit=SEARCH_LIMIT
            )
        }
        assert semantic_chunk in hybrid_chunks, "混合检索没带回语义那条"

        rounds = SqliteRoundRepository(connection)
        evidences = SqliteEvidenceRepository(connection)
        round_ = Round.start(
            round_id=ROUND_ID, project_id=PROJECT, user_input=QUERY, now=NOW
        )
        rounds.save(round_)
        prepared = RoundContextPreparer(
            rounds=rounds,
            round_writer=rounds,
            evidence=evidences,
            evidence_writer=evidences,
            materials=store,
            retriever=retriever,
            uow=SqliteUnitOfWork(connection),
            clock=FixedClock(),
        ).prepare(round_id=ROUND_ID, stage=RetrievalStage.SEAT, query=QUERY)

        trace = rounds.get(ROUND_ID).retrievals[-1]
        assert trace.stage is RetrievalStage.SEAT
        assert trace.ok is True, trace.error
        assert semantic_chunk in {hit.chunk_id for hit in trace.hits}
        assert semantic_chunk in {hit.chunk_id for hit in trace.selected}, (
            "语义那条进了命中却没进本步选中"
        )
        assert SEMANTIC_TEXT in prepared.text, "prepared.text 里没有语义那条的正文"
    finally:
        connection.close()
