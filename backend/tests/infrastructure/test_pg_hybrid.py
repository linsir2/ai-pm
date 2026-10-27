"""真 PostgreSQL：FTS + pgvector 混合检索（凸组合融合）。

`ts_rank_cd` 是正分（越大越相关），语义侧是 `1 - cosine_distance`；融合仍是
`α·语义 + (1−α)·ts_rank 饱和归一`。α/pivot 见 `materials.py` 的常量注释。
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from scripts.migrate import apply_migrations

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.domain.shared.ids import ProjectId, UserId
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.materials import (
    COVERAGE_FULL,
    COVERAGE_MAJORITY,
    FUSION_ALPHA,
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
    dedup_by_material,
)
from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    set_owner_scope,
)
from pmstudio.infrastructure.postgres.vector_index import SqliteVectorIndex
from tests.support.doubles import SeqIds
from tests.support.embedding import FakeEmbedder
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
    upsert_vector_profile,
)

OWNER = UserId("u-hybrid")
PROJECT = ProjectId("p-hybrid")
NOW = datetime(2026, 10, 27, tzinfo=UTC)
_IDS = SeqIds()


@pytest.fixture(scope="module")
def database() -> PgTestDatabase:
    pg = load_environment()
    prepare(pg)
    apply_migrations(pg.direct_url)
    return pg


@pytest.fixture()
def conn(database: PgTestDatabase) -> Iterator[object]:
    truncate_data_tables(database)
    upsert_vector_profile(database)
    token = set_owner_scope(OWNER)
    with connect(database.direct_url) as connection:
        yield connection
    clear_owner_scope(token)
    truncate_data_tables(database)
    upsert_vector_profile(database)


def _put(
    conn,  # type: ignore[no-untyped-def]
    *texts: str,
    title: str = "手册",
    project_id: ProjectId = PROJECT,
) -> str:
    store = SqliteMaterialStore(conn, ids=_IDS)
    return store.put(
        project_id=project_id,
        parsed=ParsedMaterial(
            origin="upload",
            title=title,
            source_ref=f"attachment:{title}",
            media_type="text/markdown",
            bytes_len=1,
            chunks=tuple(
                MaterialChunk(
                    chunk_id=f"local#c{index:02d}",
                    ordinal=index,
                    text=text,
                    heading_path=(),
                    anchor=f"第 {index + 1} 段",
                )
                for index, text in enumerate(texts)
            ),
            parser_id="local-text",
            parser_version="v1",
        ),
        sha256=hashlib.sha256(f"{title}:{texts}:{project_id}".encode()).hexdigest(),
        content=b"raw",
        now=NOW,
        owner=OWNER,
    ).material_id


def _write_vectors(conn, *material_ids: str, embedder=None) -> None:  # type: ignore[no-untyped-def]
    index = SqliteVectorIndex(conn, embedder=embedder or FakeEmbedder(dimensions=512))
    for material_id in material_ids:
        index.write(material_id)


class _Constant(FakeEmbedder):
    """所有文本同一个向量：让语义项对每块相同，只考词面信号。"""

    def embed_many(self, texts):
        return [tuple([1.0] + [0.0] * (self.dimensions - 1)) for _ in texts]


class _Broken(FakeEmbedder):
    def embed_many(self, texts):
        raise RuntimeError("模型下载失败")


def test_an_exact_lexical_hit_is_not_knocked_out_by_the_semantic_branch(conn) -> None:
    lexical = _put(conn, "退货时限是七天")
    semantic = _put(conn, "退货的期限与流程说明")
    _write_vectors(conn, lexical, semantic)

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货时限", owner=OWNER
    )

    assert hits and hits[0].material_id == lexical


def test_a_semantic_query_hits_where_fts_misses(conn) -> None:
    mid = _put(conn, "x")
    _write_vectors(conn, mid)
    query = "怎么把货退回去"

    assert SqliteFtsRetriever(conn).search(
        project_id=PROJECT, query=query, owner=OWNER
    ) == ()
    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query=query, owner=OWNER
    )
    assert [hit.material_id for hit in hits][:1] == [mid]


def test_the_substring_fallback_only_fills_empty_slots_and_escapes_like(conn) -> None:
    lexical = _put(conn, "vecto 存储选型规范")
    fallback = _put(conn, "向量检索依赖 vector 数据库的选型")
    _write_vectors(conn, lexical)

    hits = SqliteHybridRetriever(conn, embedder=_Broken(dimensions=512)).search(
        project_id=PROJECT, query="vecto", owner=OWNER
    )

    assert [hit.material_id for hit in hits] == [lexical, fallback]


def test_fusion_scores_are_bounded_and_positive_lexical(conn) -> None:
    ids = [_put(conn, "退货时限是七天"), _put(conn, "机柜布线要走背板槽位")]
    _write_vectors(conn, *ids)
    retriever = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512))

    hits = retriever.search(project_id=PROJECT, query="退货时限", owner=OWNER)

    assert hits
    assert all(-1.01 <= hit.score <= 1.01 for hit in hits)
    fts = retriever._fts.search(project_id=PROJECT, query="退货时限", limit=20, owner=OWNER)
    assert fts and all(hit.score > 0 for hit in fts)


def test_one_material_takes_at_most_two_slots(conn) -> None:
    mid = _put(conn, "退货流程第一段", "退货流程第二段", "退货流程第三段")
    other = _put(conn, "机柜布线规范")
    _write_vectors(conn, mid, other)

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货流程", limit=5, owner=OWNER
    )

    assert [hit.material_id for hit in hits].count(mid) <= 2


def test_the_lexical_baseline_uses_the_same_dedup() -> None:
    from pmstudio.application.dto import MaterialHit

    def hit(chunk_id: str, material_id: str) -> MaterialHit:
        return MaterialHit(
            material_id=material_id,
            chunk_id=chunk_id,
            score=1.0,
            excerpt="…",
            anchor="第 1 页",
            source_ref="attachment:x.md",
            title="x.md",
        )

    kept = dedup_by_material(
        [hit("c1", "m1"), hit("c2", "m1"), hit("c3", "m1"), hit("c4", "m2")], limit=5
    )
    assert [item.chunk_id for item in kept] == ["c1", "c2", "c4"]


def test_every_search_logs_the_breakdown(conn, caplog) -> None:
    mid = _put(conn, "退货时限是七天")
    _write_vectors(conn, mid)

    with caplog.at_level(logging.INFO):
        SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
            project_id=PROJECT, query="退货时限", owner=OWNER
        )

    lines = [
        record.getMessage()
        for record in caplog.records
        if "material_search " in record.getMessage()
    ]
    assert len(lines) == 1
    for field in ("fts_candidates=", "vec_candidates=", "fts_hits=", "hybrid_hits="):
        assert field in lines[0]


def test_frozen_fusion_parameters_are_the_measured_ones() -> None:
    assert COVERAGE_FULL == 1.0
    assert COVERAGE_MAJORITY == 0.5
    assert FUSION_ALPHA == 0.4
