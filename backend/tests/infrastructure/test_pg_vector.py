"""真 PostgreSQL + pgvector：向量写回、cosine 分数、HNSW 与 profile 生命周期。

关键口径：`embedding` 是 `material_chunks` 上的 `vector(512)` 列；查询用 `<=>`，
分数是 `1 - distance`（不是 L2 的 `1 - d²/2`）；写向量用 `UPDATE`，`drop()` 置 NULL。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from pgvector import Vector
from scripts.migrate import apply_migrations

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.domain.shared.ids import ProjectId, UserId
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.materials import (
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    set_owner_scope,
)
from pmstudio.infrastructure.postgres.vector_index import (
    SqliteVectorIndex,
    ensure_vector_profile,
    vector_char_budget,
)
from pmstudio.infrastructure.tools.limits import WEB_VECTOR_CHARS
from tests.support.doubles import SeqIds
from tests.support.embedding import FakeEmbedder
from tests.support.postgres import (
    PgTestDatabase,
    load_environment,
    prepare,
    truncate_data_tables,
    upsert_vector_profile,
)

OWNER = UserId("u-vector")
PROJECT = ProjectId("p-vector")
OTHER_PROJECT = ProjectId("p-vector-other")
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


def _parsed(*texts: str, title: str = "退货说明.pdf", origin: str = "upload") -> ParsedMaterial:
    return ParsedMaterial(
        origin=origin,
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
    )


def seed(
    conn,  # type: ignore[no-untyped-def]
    *texts: str,
    project_id: ProjectId = PROJECT,
    owner: UserId = OWNER,
    origin: str = "upload",
    title: str = "退货说明.pdf",
) -> str:
    store = SqliteMaterialStore(conn, ids=_IDS)
    return store.put(
        project_id=project_id,
        parsed=_parsed(*texts, title=title, origin=origin),
        sha256=hashlib.sha256(f"{title}:{texts}:{project_id}".encode()).hexdigest(),
        content=b"raw",
        now=NOW,
        owner=owner,
    ).material_id


def _vectors(conn) -> dict[str, object]:  # type: ignore[no-untyped-def]
    rows = conn.execute(
        "SELECT chunk_id, embedding FROM material_chunks"
        " WHERE embedding IS NOT NULL ORDER BY chunk_id"
    ).fetchall()
    return {str(row["chunk_id"]): row["embedding"] for row in rows}


def test_write_updates_every_chunk_embedding_and_runs_inference_outside_tx(conn) -> None:
    mid = seed(conn, "用户退货流程", "机柜布线规范")
    seen: list[bool] = []

    class Recorder(FakeEmbedder):
        def embed_many(self, texts):
            seen.append(conn.info.transaction_status.name != "IDLE")
            return super().embed_many(texts)

    SqliteVectorIndex(conn, embedder=Recorder(dimensions=512)).write(mid)

    assert seen and not any(seen), "embed_many 跑在了写事务里"
    assert set(_vectors(conn)) == {f"{mid}#c00", f"{mid}#c01"}
    assert all(vector.dimensions() == 512 for vector in _vectors(conn).values())


def test_drop_sets_embedding_to_null_and_keeps_chunks(conn) -> None:
    mid = seed(conn, "用户退货流程")
    index = SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512))
    index.write(mid)
    assert _vectors(conn)

    index.drop(mid)

    assert _vectors(conn) == {}
    assert conn.execute(
        "SELECT count(*) AS n FROM material_chunks WHERE material_id = %s", (mid,)
    ).fetchone() == {"n": 1}


def test_knn_score_is_one_minus_cosine_distance_not_l2_half_square(conn) -> None:
    target = seed(conn, "退货时限是七天")
    seed(conn, "机柜布线要走背板槽位")
    embedder = FakeEmbedder(dimensions=512)
    SqliteVectorIndex(conn, embedder=embedder).write(target)
    query = embedder.embed_many(["退货时限"])[0]
    row = conn.execute(
        "SELECT embedding <=> %s AS distance FROM material_chunks WHERE chunk_id = %s",
        (Vector(list(query)), f"{target}#c00"),
    ).fetchone()
    distance = float(row["distance"])

    scores = SqliteHybridRetriever(conn, embedder=embedder)._knn_scores(
        str(PROJECT), str(OWNER), "退货时限"
    )

    assert scores[f"{target}#c00"] == pytest.approx(1.0 - distance)
    assert scores[f"{target}#c00"] != pytest.approx(1.0 - distance * distance / 2.0)


def test_hnsw_index_uses_cosine_ops_and_is_used_when_seqscan_is_off(conn) -> None:
    index_row = conn.execute(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = current_schema()"
        " AND indexname = 'idx_material_chunks_embedding'"
    ).fetchone()
    assert index_row is not None
    definition = index_row["indexdef"].lower()
    assert "using hnsw" in definition
    assert "vector_cosine_ops" in definition

    mid = seed(conn, "用户退货流程")
    embedder = FakeEmbedder(dimensions=512)
    SqliteVectorIndex(conn, embedder=embedder).write(mid)
    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        plan = conn.execute(
            "EXPLAIN SELECT chunk_id FROM material_chunks"
            " WHERE embedding IS NOT NULL ORDER BY embedding <=> %s LIMIT 5",
            (Vector(list(embedder.embed_many(["退货流程"])[0])),),
        ).fetchall()
    assert any(
        "idx_material_chunks_embedding" in str(row["QUERY PLAN"]) for row in plan
    ), plan


def test_iterative_scan_is_set_local_only_inside_an_explicit_transaction(conn) -> None:
    mid = seed(conn, "用户退货流程")
    embedder = FakeEmbedder(dimensions=512)
    SqliteVectorIndex(conn, embedder=embedder).write(mid)
    retriever = SqliteHybridRetriever(conn, embedder=embedder)

    retriever._knn_scores(str(PROJECT), str(OWNER), "退货流程")
    outside = conn.execute("SHOW hnsw.iterative_scan").fetchone()
    assert outside["hnsw.iterative_scan"] == "off"

    with conn.transaction():
        retriever._knn_scores(str(PROJECT), str(OWNER), "退货流程")
        inside = conn.execute("SHOW hnsw.iterative_scan").fetchone()
        ef_search = conn.execute("SHOW hnsw.ef_search").fetchone()
    assert inside["hnsw.iterative_scan"] == "relaxed_order"
    assert ef_search["hnsw.ef_search"] == "200"


def test_filtered_hnsw_still_finds_the_only_allowed_project(conn) -> None:
    for index in range(80):
        seed(conn, f"无关材料 {index}", project_id=OTHER_PROJECT, title=f"other-{index}.md")
    target = seed(conn, "退货时限是七天")
    embedder = FakeEmbedder(dimensions=512)
    index = SqliteVectorIndex(conn, embedder=embedder)
    index.write(target)
    # 给别的项目也写向量：它们不在允许集合里
    with conn.transaction():
        conn.execute(
            "UPDATE material_chunks SET embedding = %s WHERE material_id IN"
            " (SELECT material_id FROM materials WHERE project_id = %s)",
            (Vector([0.0] * 512), str(OTHER_PROJECT)),
        )

    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        scores = SqliteHybridRetriever(conn, embedder=embedder)._knn_scores(
            str(PROJECT), str(OWNER), "退货时限"
        )
    assert f"{target}#c00" in scores


def test_exact_scan_and_hnsw_agree_on_the_top_chunk_for_small_data(conn) -> None:
    ids = [seed(conn, text) for text in ("退货时限是七天", "机柜布线规范", "发票开票流程")]
    embedder = FakeEmbedder(dimensions=512)
    index = SqliteVectorIndex(conn, embedder=embedder)
    for mid in ids:
        index.write(mid)
    query = Vector(list(embedder.embed_many(["退货时限"])[0]))

    with conn.transaction():
        conn.execute("SET LOCAL enable_indexscan = off")
        conn.execute("SET LOCAL enable_bitmapscan = off")
        exact = conn.execute(
            "SELECT chunk_id FROM material_chunks WHERE embedding IS NOT NULL"
            " ORDER BY embedding <=> %s LIMIT 1",
            (query,),
        ).fetchone()["chunk_id"]
    with conn.transaction():
        conn.execute("SET LOCAL enable_seqscan = off")
        scores = SqliteHybridRetriever(conn, embedder=embedder)._knn_scores(
            str(PROJECT), str(OWNER), "退货时限"
        )
    assert max(scores, key=scores.get) == exact


# ── profile / meta ───────────────────────────────────────────────────────────


def test_ensure_vector_profile_upserts_one_row_and_detects_no_change(conn) -> None:
    conn.execute("DELETE FROM vector_index_meta")

    assert ensure_vector_profile(conn) is True
    rows = conn.execute("SELECT * FROM vector_index_meta").fetchall()
    assert len(rows) == 1
    assert rows[0]["dimensions"] == 512

    assert ensure_vector_profile(conn) is False
    assert conn.execute("SELECT count(*) AS n FROM vector_index_meta").fetchone() == {"n": 1}


def test_profile_change_clears_embeddings_and_rebuilds_under_advisory_lock(conn) -> None:
    mid = seed(conn, "用户退货流程")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write(mid)
    assert _vectors(conn)
    conn.execute(
        "UPDATE vector_index_meta SET profile = 'old/model/natural' WHERE id = 1"
    )

    assert ensure_vector_profile(conn, profile="new/model/natural") is True

    assert _vectors(conn) == {}, "profile 变了旧向量必须清空"
    profile = conn.execute("SELECT profile FROM vector_index_meta WHERE id = 1").fetchone()
    assert profile == {"profile": "new/model/natural"}
    # 表还在、块还在，只是向量为空
    assert conn.execute(
        "SELECT count(*) AS n FROM material_chunks WHERE material_id = %s", (mid,)
    ).fetchone() == {"n": 1}


def test_a_profile_dimension_other_than_512_fails_explicitly(conn) -> None:
    with pytest.raises(ValueError, match="512"):
        ensure_vector_profile(conn, dimensions=768)
    assert conn.execute(
        "SELECT dimensions FROM vector_index_meta WHERE id = 1"
    ).fetchone() == {"dimensions": 512}


def test_vector_budget_is_character_based_for_web_materials_only() -> None:
    assert vector_char_budget("web_search") == WEB_VECTOR_CHARS
    assert vector_char_budget("upload") is None
    assert vector_char_budget("web_fetch") is None


@pytest.mark.slow
def test_real_fastembed_vectors_roundtrip(conn) -> None:
    from pmstudio.infrastructure.postgres.embedding import FastEmbedEmbedder

    target = seed(conn, "用户退货时限是七天，超过期限需要申请。")
    seed(conn, "机柜布线要走背板槽位，网线不能压在电源上。")
    embedder = FastEmbedEmbedder()
    SqliteVectorIndex(conn, embedder=embedder).write(target)

    scores = SqliteHybridRetriever(conn, embedder=embedder)._knn_scores(
        str(PROJECT), str(OWNER), "退货怎么退"
    )
    assert scores and -1.0 <= max(scores.values()) <= 1.0
    assert max(scores, key=scores.get) == f"{target}#c00"
