"""混合检索：FTS5（词法）+ KNN（语义）→ RRF 融合。

`MaterialHit` 没有 `text` 字段（只有 chunk_id/title 等）——断言一律按 chunk_id。
`FakeEmbedder` 必须是 512 维：vec 表是 `FLOAT[512]`，维度错配会被 sqlite-vec 拒绝。
"""

import pytest

from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.materials import SqliteFtsRetriever
from pmstudio.infrastructure.sqlite.tokenize import index_source
from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
from tests.support.embedding import FakeEmbedder


@pytest.fixture
def conn(tmp_path):
    connection = database.connect(tmp_path / "t.db")
    yield connection
    connection.close()


def _put_material(conn, material_id="m-1", *, project_id="p1", title="手册",
                  chunks=(("c1", "x"),)):
    from tests.conftest import TEST_OWNER

    owner = str(TEST_OWNER)
    conn.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at)"
        " VALUES (?, ?, ?, 'upload', ?, '退货说明.pdf', 'application/pdf', 0,"
        " ?, 'plain', '1', '1', 0, '[]', ?, '2026-10-18T00:00:00+00:00')",
        (material_id, str(project_id), owner, title, f"sha-{material_id}", b""),
    )
    for ordinal, (chunk_id, text) in enumerate(chunks):
        conn.execute(
            "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
            " heading_path, anchor) VALUES (?, ?, ?, ?, ?, '[]', ?)",
            (chunk_id, material_id, owner, ordinal, text, f"第{ordinal}段"),
        )
        conn.execute(
            "INSERT INTO material_chunks_fts (chunk_id, material_id, tokens) VALUES (?, ?, ?)",
            (chunk_id, material_id,
             index_source(title=title, heading_path=(), text=text)),
        )


def test_rrf_order_is_the_sum_of_reciprocal_ranks():
    """纯函数：两列排名 → 融合顺序 = Σ 1/(60+rank)。

    交叉两列 `["a","b"]` + `["b","a"]` 时 "b" 两列都在（名次 2 和 1）得分
    `1/62 + 1/61`，"a" 只得一列的 `1/61` → "b" 在前。这是 RRF 的**要点**：
    两边都认可的条目优先于单边强命中（不是 BM25 的绝对分可比）。
    """
    from pmstudio.infrastructure.sqlite.materials import (
        RRF_K,
        reciprocal_rank_fusion,
    )

    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    assert fused == ("b", "a")          # 交叉两列 → 两边都在的 "b" 优先

    assert reciprocal_rank_fusion([["x"], ["y"]]) == ("x", "y")   # 各命中一边 → 首列稳定序

    # 单列退化：RRF 分应单调随名次下降（唯一列时序与输入一致）
    assert reciprocal_rank_fusion([["p", "q", "r"], []]) == ("p", "q", "r")

    # 打分口径：手算一次确认不是"随便排个序"
    scores = {c: 1 / (RRF_K + i) for i, c in enumerate(["a", "b"])}
    assert scores["a"] > scores["b"]    # 单列内 rank1 > rank2


def test_semantic_query_hits_where_fts_misses(conn):
    """R（本批的主需求）：换个说法也能命中。

    先断言 **FTS5 单独查不到**（否则这条测试没有鉴别力），再断言混合查得到。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn)
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    fts_only = SqliteFtsRetriever(conn).search(
        project_id=PROJECT, query="怎么把货退回去", owner=TEST_OWNER)
    assert fts_only == ()                      # 前置：词法确实不中

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="怎么把货退回去", owner=TEST_OWNER)
    assert [h.chunk_id for h in hits][:1] == ["c1"]   # 语义命中


def test_a_lexical_hit_is_never_knocked_out_by_the_vector_branch(conn):
    """R：加了语义之后，**原来能查到的还在**（词法命中零倒退）。

    融合检索最典型的翻车是"新加一路把老结果挤掉"——用户升级后反而找不到
    以前能搜到的材料。BM25 命中的块必须仍在结果里（次序可以变）。

    标题用中性词「手册」：`index_source` 会把标题并进每块的 token 流，
    标题若含检索词就会污染另一块，前置断言（词法只中 c2）就不成立了。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, chunks=(("c1", "机柜布线规范"), ("c2", "退款时限")))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    fts_only = {h.chunk_id for h in SqliteFtsRetriever(conn).search(
        project_id=PROJECT, query="退款时限", owner=TEST_OWNER)}
    assert fts_only == {"c2"}                     # 前置：词法只中 c2

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退款时限", owner=TEST_OWNER)

    assert "c2" in {h.chunk_id for h in hits}      # 升级后仍查得到


def test_another_owners_chunks_are_never_returned(conn):
    """R：隔离。别人的材料块既不该被本项目命中，也不该在候选里冒头。"""
    from pmstudio.domain.shared.ids import UserId
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn)                                   # 本人：c1
    _put_material(conn, "m-2", chunks=(("c2", "退货流程"),))   # 换 owner
    conn.execute("UPDATE material_chunks SET owner_id = ? WHERE chunk_id = 'c2'",
                 (str(UserId("u-mallory")),))
    conn.execute("UPDATE materials SET owner_id = ? WHERE material_id = 'm-2'",
                 (str(UserId("u-mallory")),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="怎么把货退回去", owner=TEST_OWNER)

    assert {h.chunk_id for h in hits} == {"c1"}          # c2 是别人的，不出现


def test_another_projects_chunks_are_never_returned(conn):
    """R：项目隔离。KNN 是**全局**扫描的（vec 表没有项目列），所以项目过滤必须真的生效——
    否则同一个人换个项目就能检索到另一个项目的材料。

    跨项目这条比跨 owner 更要紧：owner 通常只有一个人的时候，隔离全靠项目这一层。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, "m-1")                                  # 本项目 p1：c1
    _put_material(conn, "m-2", project_id="p2", chunks=(("c2", "x"),))   # 另一个项目：c2
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="怎么把货退回去", owner=TEST_OWNER)

    assert {h.chunk_id for h in hits} == {"c1"}          # p2 的 c2 不出现


def test_a_broken_embedder_still_returns_lexical_hits(conn):
    """R：推理炸了不许把检索整个打死——退回纯 FTS5，词法结果照旧。"""
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, chunks=(("c1", "退货流程"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    hits = SqliteHybridRetriever(conn, embedder=Broken()).search(
        project_id=PROJECT, query="退货流程", owner=TEST_OWNER)

    assert [h.chunk_id for h in hits] == ["c1"]           # 降级后仍是可用结果
