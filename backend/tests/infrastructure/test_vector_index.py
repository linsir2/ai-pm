"""向量索引（检索阶段 2）：主事务之外写向量；降级不判死。"""

import pytest
from psycopg.pq import TransactionStatus

from pmstudio.application.ports import MaterialVectorIndex
from pmstudio.domain.null_repos import NoOpVectorIndex
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.postgres.tokenize import index_source
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from tests.support.embedding import FakeEmbedder
from tests.support.postgres import open_direct

ALICE = UserId("u-alice")


@pytest.fixture
def conn():
    connection = open_direct()
    yield connection
    connection.close()


def _in_transaction(connection) -> bool:  # type: ignore[no-untyped-def]
    return connection.info.transaction_status != TransactionStatus.IDLE


def _put_material(conn, material_id="m-1", *, project_id="p1",
                  chunks=(("c1", "用户退货流程"),), origin="upload"):
    """把材料与块直接写进库（本任务测的是**向量索引**，不经材料仓）。"""

    conn.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at)"
        " VALUES (%s, %s, %s, %s, '退货说明', '退货说明.pdf', 'application/pdf', 0,"
        " 'sha-x', 'plain', '1', 1, false, '{}', %s, '2026-10-18T00:00:00+00:00')",
        (material_id, str(project_id), str(ALICE), origin, b""),
    )
    for ordinal, (chunk_id, text) in enumerate(chunks):
        conn.execute(
            "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
            " heading_path, anchor, tokens) VALUES (%s, %s, %s, %s, %s, '{}', %s, %s)",
            (chunk_id, material_id, str(ALICE), ordinal, text, f"第{ordinal}段",
             index_source(title="退货说明", heading_path=(), text=text)),
        )


def _vector_count(connection) -> int:  # type: ignore[no-untyped-def]
    return connection.execute(
        "SELECT count(*) AS n FROM material_chunks WHERE embedding IS NOT NULL"
    ).fetchone()["n"]


def test_vectors_are_written_outside_any_open_transaction(conn):
    """**本批最关键的一条**：推理时连接上**没有**活动写事务。"""
    seen: list[bool] = []

    class Recorder(FakeEmbedder):
        def embed_many(self, texts):
            seen.append(_in_transaction(conn))
            return super().embed_many(texts)

    _put_material(conn)
    SqliteVectorIndex(conn, embedder=Recorder(dimensions=512)).write("m-1")

    assert seen, "向量索引一条都没算（测试没有鉴别力）"
    assert not any(seen), "推理时连接上有活动事务——写锁会被推理时长占住"


def test_write_fills_the_vector_for_every_chunk(conn):
    seen: list[str] = []

    class Recorder(FakeEmbedder):
        def embed_many(self, texts):
            seen.extend(texts)
            return super().embed_many(texts)

    _put_material(conn, chunks=(("c1", "退货时限是七天"), ("c2", "机柜布线规范")))

    SqliteVectorIndex(conn, embedder=Recorder(dimensions=512)).write("m-1")

    assert _vector_count(conn) == 2
    assert seen, "向量索引一条都没算（测试没有鉴别力）"
    assert any("退货时限是七天" in text for text in seen), (
        f"喂进 embedder 的不是自然语言正文：{seen!r}"
    )


def test_drop_removes_the_vectors(conn):
    _put_material(conn)
    index = SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512))
    index.write("m-1")

    index.drop("m-1")

    assert _vector_count(conn) == 0


def test_a_broken_embedder_does_not_raise(conn):
    """降级不判死：推理抛异常，`write()` 只留 warning——材料照旧可用（走 FTS）。"""
    _put_material(conn)

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    SqliteVectorIndex(conn, embedder=Broken()).write("m-1")   # 不抛即通过

    assert _vector_count(conn) == 0


def test_noop_index_never_touches_the_database():
    NoOpVectorIndex().write("m-1")
    NoOpVectorIndex().drop("m-1")    # 不抛即通过


def test_the_protocol_is_satisfied_by_both_impls(conn):
    """两个实现都满足 `MaterialVectorIndex`。"""

    assert isinstance(SqliteVectorIndex(conn, embedder=FakeEmbedder()), MaterialVectorIndex)
    assert isinstance(NoOpVectorIndex(), MaterialVectorIndex)


def test_the_vector_budget_only_applies_to_web_materials():
    """R：预算**只限联网材料**——上传与抓网页是用户精挑要精读的，不该限。"""
    from pmstudio.infrastructure.sqlite.vector_index import vector_char_budget
    from pmstudio.infrastructure.tools.limits import WEB_VECTOR_CHARS

    assert vector_char_budget("web_search") == WEB_VECTOR_CHARS
    assert vector_char_budget("upload") is None, "上传材料不该限"
    assert vector_char_budget("web_fetch") is None, "抓网页材料不该限"


def test_the_block_budget_is_measured_in_characters_not_blocks():
    """R：预算的量纲是**字**（`blocks_within_budget`），不是块数。"""
    from pmstudio.infrastructure.sqlite.vector_index import blocks_within_budget

    rows = [{"text": "字" * 400} for _ in range(30)]  # 共 12000 字

    assert blocks_within_budget(rows, 9600) == 24, "9600 字 = 24 个 400 字的块"
    assert blocks_within_budget(rows, 400) == 1
    assert blocks_within_budget(rows, None) == 30, "不限 = 全要"
    smaller = [{"text": "字" * 200} for _ in range(60)]
    assert blocks_within_budget(smaller, 9600) == 48
    covered = sum(len(row["text"]) for row in smaller[: blocks_within_budget(smaller, 9600)])
    assert covered == 9600


def test_a_web_material_only_gets_vectors_within_its_char_budget(conn):
    """R：联网材料只写**字数预算内**的块的向量（**FTS 仍覆盖全部块**）。"""
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
    from pmstudio.infrastructure.tools.limits import WEB_VECTOR_CHARS

    body = "字" * 400
    _put_material(
        conn,
        chunks=tuple((f"c{index}", body) for index in range(30)),
        origin="web_search",
    )

    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    assert _vector_count(conn) == WEB_VECTOR_CHARS // 400
    assert conn.execute("SELECT count(*) AS n FROM material_chunks").fetchone()["n"] == 30, (
        "块不该被删——FTS 还要覆盖全部块"
    )


def test_an_upload_material_still_gets_a_vector_for_every_chunk(conn):
    """R：上传材料**不受限**（回归：批 5/6a 的语义召回能力不能被这次改动削掉）。"""
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

    _put_material(
        conn,
        chunks=tuple((f"c{index}", f"第 {index} 段正文") for index in range(30)),
        origin="upload",
    )

    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    assert _vector_count(conn) == 30


def test_an_explicit_limit_overrides_the_origin_rule(conn):
    """R：`max_blocks` 显式给上限时按它走（调用方说了算）。"""
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

    _put_material(
        conn,
        chunks=tuple((f"c{index}", f"第 {index} 段正文") for index in range(12)),
        origin="upload",
    )

    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1", max_blocks=3)

    assert _vector_count(conn) == 3
