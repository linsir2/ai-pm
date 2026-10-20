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


def test_rrf_is_kept_only_as_a_reference_implementation():
    """RRF 现在**只作对照**（量化表里那一列就是这么算的），不再是融合口径。

    留它是为了让"当初为什么换掉它"可复算：交叉两列时"两边都在"的条目得分更高，
    这正是它**对称**、因而压不住"词法第 1 但语义第 50"的块的原因。
    """
    from pmstudio.infrastructure.sqlite.materials import reciprocal_rank_fusion

    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]])
    assert fused == ("b", "a")          # 交叉两列 → 两边都在的 "b" 优先
    assert reciprocal_rank_fusion([["x"], ["y"]]) == ("x", "y")


def test_a_lexical_exact_hit_is_never_knocked_out_by_a_semantic_one(conn):
    """R：**词面原样在的块一定排在前面**（用户把原话打出来时不该被语义掀翻）。

    这是 2026-10-19 实测逼出来的：RRF 下逐字子串类 top1 30→25、recall@5 40→36
    （用户升级后反而搜不到以前能搜到的），所以融合换成"按词面覆盖率分档、
    档内按语义分排"。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    # 目标块的正文**原样**含查询的每个 token；另一块只在语义上像
    _put_material(conn, "m-1", chunks=(("c1", "退货时限是七天"),))
    _put_material(conn, "m-2", chunks=(("c2", "退款周期不超过一星期"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货时限", owner=TEST_OWNER)

    assert hits, "查不到"
    assert hits[0].chunk_id == "c1", f"词面原样命中的块被压到第 {hits[0].chunk_id}"


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


def test_the_first_hit_carries_the_largest_score(conn):
    """R：`score` 越大越好（与顺序同向）——**第一名必须拿最大的分**。

    2026-10-19 实测的反例：实现算的是 `1.0/(total-position)`，于是**第一名拿最小分**，
    与它上一行的注释正好相反。消费方（`dto.py:780`）不重排，所以只错在数值——
    但"越大越好"是 `MaterialHit` 的对外约定（`ports.py` 里明写着），不能反着来。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, "m-1", chunks=(("c1", "退货时限是七天"),))
    _put_material(conn, "m-2", chunks=(("c2", "退货时限与运费"),))

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货时限", owner=TEST_OWNER)

    assert len(hits) >= 2, "至少要有两条才能比大小"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), f"第一名应拿最大分，实得 {scores}"


def test_the_vector_side_gives_the_real_cosine_not_a_rank(conn):
    """R：语义分是**真余弦**（不是名次归一化）。

    为什么：凸组合要的是**可比的分数幅度**。名次归一化把「第 1 名」与「第 200 名」
    压成 1.0 与 0.005——幅度整个丢了，这正是 RRF 一族（"纯名次函数"）的病根
    （TOIS 2023：凸组合 > RRF，in/out-of-domain 都赢）。
    两个 embedder 都已归一（实测 L2 范数 1.0）→ L2 距离 d 换算 `cos = 1 - d²/2`。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, "m-1", chunks=(("c1", "退货时限是七天"),))
    _put_material(conn, "m-2", chunks=(("c2", "机柜布线要走背板槽位"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")
    retriever = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512))

    scores = retriever._knn_scores(str(PROJECT), str(TEST_OWNER), "退货时限")

    assert scores, "向量侧没给出分数"
    for chunk_id, cosine in scores.items():
        assert -1.01 <= cosine <= 1.01, f"余弦越界：{chunk_id} → {cosine}"
    # **不是名次归一化**：名次归一化给第一名**恰好 1.0**（与真实相似度无关），
    # 真余弦不会——查询「退货时限」与「退货时限是七天」并不是同一段，余弦应 < 1。
    assert max(scores.values()) < 1.0, (
        "第一名恰好 1.0 —— 那还是名次归一化，不是真余弦"
    )
    # 词面更贴近查询的那块，余弦应当更高
    assert scores["c1"] > scores["c2"], scores


def test_an_exact_lexical_hit_is_not_knocked_out_by_a_semantic_one(conn):
    """R：**词面完全命中**的块不被语义掀翻。

    这是 RRF 一族的通病（纯名次函数丢掉了分数幅度）：语义第 1、词面第 2 的块
    会赢过词面第 1、语义第 50 的块。实测（真数据）：旧口径让"同词换序"类少 1 条、
    "同义改述"类少 2 条；改成凸组合后两类都与纯 FTS5 持平。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    # 目标：正文**原样**含查询的每个 token（词面完全命中）
    _put_material(conn, "m-1", chunks=(("c1", "退货时限是七天"),))
    # 干扰：语义上像（共享很多字），词面上不含查询的 token
    _put_material(conn, "m-2", chunks=(("c2", "退货的期限与流程说明"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货时限", owner=TEST_OWNER)

    assert hits, "查不到"
    assert hits[0].chunk_id == "c1", f"词面完全命中的块被压到第 {hits[0].chunk_id}"


def test_the_fusion_score_is_a_bounded_combination(conn):
    """R：融合分是**两个有界分量的凸组合**（不是名次的函数）。

    语义是真余弦（[-1,1]），词面是饱和归一（[0,1)），`α·语义 + (1−α)·词面` 落在 [-1,1]。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, "m-1", chunks=(("c1", "退货时限是七天"),))
    _put_material(conn, "m-2", chunks=(("c2", "机柜布线要走背板槽位"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货时限", owner=TEST_OWNER)

    for hit in hits:
        assert -1.01 <= hit.score <= 1.01, f"融合分越界：{hit.score}"


def _rare_term_corpus(conn):
    from tests.conftest import TEST_OWNER

    """一个能放大病灶的语料：**罕见词**在 m1、常见词在别人家。

    为什么要这么造：病灶是「目标的词面覆盖率落在 0.5 上下 → 掉进纯语义档 →
    丢掉 BM25 挣到的名次」。要让覆盖率**低**而 BM25 **高**，就得让目标只命中
    一个**罕见**词（覆盖率 1/4），干扰块命中三个**常见**词（覆盖率 3/4）。
    实测这时的 BM25：m1 = 1.0986（罕见词 IDF 高）vs m2 = 0.3365。
    """
    from pmstudio.infrastructure.sqlite.tokenize import index_source

    docs = {
        "m1": "紫貂 皮草 拍卖 记录",
        "m2": "出口 关税 报关 流程",
        "m3": "出口 关税 物流 规定",
        "m4": "报关 关税 出口 清关",
        "m5": "关税 出口 通关 说明",
    }
    for material_id, text in docs.items():
        conn.execute(
            "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
            " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
            " warnings, content, created_at) VALUES (?, 'p1', ?, 'upload', '手册', 'a',"
            " 'text/plain', 0, ?, 'plain', '1', 1, 0, '[]', X'00', '2026-10-18T00:00:00+00:00')",
            (material_id, str(TEST_OWNER), f"sha-{material_id}"),
        )
        conn.execute(
            "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
            " heading_path, anchor) VALUES (?, ?, ?, 0, ?, '[]', '第1段')",
            (f"{material_id}#c00", material_id, str(TEST_OWNER), text),
        )
        conn.execute(
            "INSERT INTO material_chunks_fts (chunk_id, material_id, tokens) VALUES (?, ?, ?)",
            (f"{material_id}#c00", material_id,
             index_source(title="手册", heading_path=(), text=text)),
        )


def test_a_rare_lexical_hit_survives_the_semantic_branch(conn):
    """R：目标只命中**一个罕见词**时（覆盖率低、BM25 很高），**不许被语义掀翻**。

    这是"离散分档"口径的病根：覆盖率 0.25 → 掉进纯语义档 → BM25 挣到的
    第 1 位被一个只命中常见词的块抢走。实测（真数据）：旧口径让"同词换序"少 1 条、
    "同义改述"少 2 条；凸组合后两类都与纯 FTS5 持平。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    class _Constant(FakeEmbedder):
        """**所有文本同一个向量**——语义项对每块都一样。

        这样测试隔离出"融合有没有保住词面信号"这一件事：语义路不添乱，
        排序就该完全由词面强度决定。旧口径（离散分档）在这里必错：
        m1 覆盖率 0.25 → 掉进纯语义档 → 明明 BM25 第 1 却被排到后面。
        """

        def embed_many(self, texts):
            return [tuple([1.0] + [0.0] * (self.dimensions - 1)) for _ in texts]

    _rare_term_corpus(conn)
    for material_id in ("m1", "m2", "m3", "m4", "m5"):
        SqliteVectorIndex(conn, embedder=_Constant(dimensions=512)).write(material_id)

    hits = SqliteHybridRetriever(conn, embedder=_Constant(dimensions=512)).search(
        project_id=PROJECT, query="紫貂 出口 关税 报关", owner=TEST_OWNER)

    assert hits, "查不到"
    assert hits[0].chunk_id == "m1#c00", (
        f"罕见词命中的块被压到第 {[h.chunk_id for h in hits]}"
    )


def test_a_query_the_lexer_misses_still_finds_the_material_by_substring(conn):
    """R：词面路零命中时，**子串兜底**把材料捞回来。

    为什么要有这条：用户搜的字串与索引里的 token **不一定对得上**——
    搜 "vecto"（英文词没打完），材料里写的是 "vector"（索引成 `vector` 这个 token）
    → 词面**零命中**，可 "vecto" 确实在那段正文里。

    实测（2026-10-20，冻结语料 15 份 + 10 条手写查询）：子串这条路能补上
    词面与语义**都没**找到的材料（子串 10 条 / 单字 AND 13 条 / 词级 AND 9 条；
    取最严的子串版——捞回来的片段里真的出现用户搜的字串，读得通）。

    这里的 embedder **故意坏掉**：假 embedder 按字符分桶，"vecto" 与 "vector"
    有共同字符 → 向量路也会找到它 → 那样就分不清是谁捞回来的。坏掉之后混合检索
    退化成纯 FTS5，**多出来的结果只可能来自兜底**。
    """
    from pmstudio.infrastructure.sqlite.materials import (
        SqliteFtsRetriever,
        SqliteHybridRetriever,
    )
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, chunks=(("c1", "向量检索依赖 vector 数据库的选型"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    lexical = SqliteFtsRetriever(conn).search(
        project_id=PROJECT, query="vecto", owner=TEST_OWNER)
    assert lexical == (), "词面命中了就没有兜底可测了（测试没有鉴别力）"

    hits = SqliteHybridRetriever(conn, embedder=Broken()).search(
        project_id=PROJECT, query="vecto", owner=TEST_OWNER)

    assert [h.chunk_id for h in hits] == ["c1"]
    assert "vecto" in hits[0].excerpt, "片段要能让人看见自己搜的字串"


def test_the_substring_fallback_only_fills_empty_slots(conn):
    """R：兜底**只补空槽**——已有的好结果一个都不许被挤掉。"""
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, "m-1", title="词面能命中",
                  chunks=(("c1", "vecto 存储选型规范"),))
    _put_material(conn, "m-2", title="只能子串命中",
                  chunks=(("c2", "向量检索依赖 vector 数据库的选型"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    hits = SqliteHybridRetriever(conn, embedder=Broken()).search(
        project_id=PROJECT, query="vecto", owner=TEST_OWNER)

    assert [h.chunk_id for h in hits] == ["c1", "c2"], (
        "词面命中的那份必须在前面，兜底的在后面补上"
    )


def test_a_search_that_is_already_full_does_not_use_the_fallback(conn):
    """R：结果已经找满时**不跑兜底**（不往结果里塞弱匹配的噪音）。"""
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(conn, "m-1", chunks=(("c1", "vecto 存储选型规范"),))
    _put_material(conn, "m-2", chunks=(("c2", "vecto 归档规范"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    class Broken(FakeEmbedder):
        def embed_many(self, texts):
            raise RuntimeError("模型下载失败")

    hits = SqliteHybridRetriever(conn, embedder=Broken()).search(
        project_id=PROJECT, query="vecto", limit=2, owner=TEST_OWNER)

    assert len(hits) == 2


def test_one_material_takes_only_one_slot_in_the_results(conn):
    """R：**一份材料只占一个槽**——top5 里不能全是同一份材料的 5 个块。

    为什么：① 界面（`dto.search_hits`）一份材料只出一行，检索器返回 5 个同材料的块
    的话，界面只有 1 行，别的材料被挤出去了；
    ② 它同时治了"部分向量索引"的偏置传导（2026-10-20 实测：N=8 时 KNN 的候选
    都来自每份材料的前 8 块，不去重就占满 top5，**改述召回从 10/14 掉到 4/14**，
    比不用向量还差）。
    """
    from pmstudio.infrastructure.sqlite.materials import SqliteHybridRetriever
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    _put_material(
        conn,
        "m-1",
        chunks=(("c1", "退货流程第一段"), ("c2", "退货流程第二段"), ("c3", "退货流程第三段")),
    )
    _put_material(conn, "m-2", chunks=(("c4", "机柜布线规范"),))
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-1")
    SqliteVectorIndex(conn, embedder=FakeEmbedder(dimensions=512)).write("m-2")

    hits = SqliteHybridRetriever(conn, embedder=FakeEmbedder(dimensions=512)).search(
        project_id=PROJECT, query="退货流程", limit=5, owner=TEST_OWNER)

    materials = [hit.material_id for hit in hits]
    assert len(materials) == len(set(materials)), f"同一份材料占了多个槽：{materials}"
