"""向量索引（检索阶段 2）：主事务之外写向量；降级不判死。"""

import pytest

from pmstudio.application.ports import MaterialVectorIndex
from pmstudio.domain.null_repos import NoOpVectorIndex
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from tests.support.embedding import FakeEmbedder

ALICE = UserId("u-alice")


@pytest.fixture
def conn(tmp_path):
    connection = database.connect(tmp_path / "t.db")
    yield connection
    connection.close()


def _put_material(conn, material_id="m-1", *, project_id="p1",
                  chunks=(("c1", "用户退货流程"),)):
    """把材料与块直接写进库（本任务测的是**向量索引**，不经材料仓）。"""

    conn.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at)"
        " VALUES (?, ?, ?, 'upload', '退货说明', '退货说明.pdf', 'application/pdf', 0,"
        " 'sha-x', 'plain', '1', '1', 0, '[]', ?, '2026-10-18T00:00:00+00:00')",
        (material_id, str(project_id), str(ALICE), b""),
    )
    for ordinal, (chunk_id, text) in enumerate(chunks):
        conn.execute(
            "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
            " heading_path, anchor) VALUES (?, ?, ?, ?, ?, '[]', ?)",
            (chunk_id, material_id, str(ALICE), ordinal, text, f"第{ordinal}段"),
        )


def test_vectors_are_written_outside_any_open_transaction(conn):
    """**本批最关键的一条**：推理时连接上**没有**活动写事务——否则 200ms 写锁回来了。"""
    seen: list[bool] = []

    class Recorder(FakeEmbedder):
        def embed_many(self, texts):
            seen.append(conn.in_transaction)
            return super().embed_many(texts)

    _put_material(conn)
    SqliteVectorIndex(conn, embedder=Recorder(dimensions=512)).write("m-1")

    assert seen, "向量索引一条都没算（测试没有鉴别力）"
    assert not any(seen), "推理时连接上有活动事务——写锁会被推理时长占住"


def test_write_fills_the_vector_table_for_every_chunk(conn):
    _put_material(conn, chunks=(("c1", "用户退货流程"), ("c2", "机柜布线规范")))

    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    assert conn.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 2


def test_drop_removes_the_vectors(conn):
    _put_material(conn)
    index = SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512))
    index.write("m-1")

    index.drop("m-1")

    assert conn.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 0


def test_a_broken_embedder_does_not_raise(conn):
    """降级不判死：推理抛异常，`write()` 只留 warning——材料照旧可用（走 FTS5）。"""
    _put_material(conn)

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    SqliteVectorIndex(conn, embedder=Broken()).write("m-1")   # 不抛即通过

    assert conn.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0] == 0


def test_noop_index_never_touches_the_database():
    NoOpVectorIndex().write("m-1")
    NoOpVectorIndex().drop("m-1")    # 不抛即通过


def test_the_protocol_is_satisfied_by_both_impls(conn):
    """两个实现都满足 `MaterialVectorIndex`（协议与 ports.py 里其它协议一样是 runtime 可查的）。

    钉这个是为了装配点放心换：NoOp 与真实现可互换，`isinstance` 是那条互换性的机器证明。
    """
    assert isinstance(SqliteVectorIndex(conn, embedder=FakeEmbedder()), MaterialVectorIndex)
    assert isinstance(NoOpVectorIndex(), MaterialVectorIndex)
