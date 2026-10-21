"""向量适配层：**不联网**也能测的只有协议与假实现；真模型一条单独打标。"""

import logging
import math

import pytest

from tests.support.embedding import FakeEmbedder


def _cos(a, b):
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b, strict=True)) / denom if denom else 0.0


def test_fake_embedder_is_deterministic_and_sized():
    embedder = FakeEmbedder(dimensions=8)

    first = embedder.embed_many(["退货流程", "退货流程"])
    second = embedder.embed_many(["退货流程", "退货流程"])

    assert embedder.dimensions == 8
    assert len(first) == 2 and len(first[0]) == 8
    assert first == second                      # 确定性
    assert first[0] == first[1]                 # 同文本同向量
    assert first[0] != embedder.embed_many(["完全不同的话"])[0]


def test_fake_embedder_puts_similar_texts_close():
    """假的也要**语义上一个方向**：同义改写的距离要显著小于无关文本。

    不然它测不出 RRF 的价值（假得太假 = 测试没有鉴别力）。
    """
    embedder = FakeEmbedder(dimensions=256)
    a, b, c = embedder.embed_many(["怎么把货退回去", "用户退货流程", "服务器机柜布线规范"])
    assert _cos(a, b) > _cos(a, c)


@pytest.mark.slow
def test_real_model_embeds_chinese_and_matches_the_declared_dimension():
    """真模型（联网、首次会下 ~90MB）：只在显式选择它时跑。"""
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder

    embedder = FastEmbedEmbedder()
    vectors = embedder.embed_many(["用户退货流程", "怎么把货退回去"])

    assert embedder.dimensions == 512
    assert len(vectors[0]) == 512
    assert _cos(vectors[0], vectors[1]) > 0.6   # 同义：相似度应明显高于无关对


# ── 向量口径：索引侧喂**自然语言**，不是 jieba 分词串（批 6a Task 2）──────────


def test_the_vector_side_embeds_natural_language_not_a_token_bag():
    """R：喂给 embedder 的是**自然语言**（标题 + 章节链 + 正文，原样）。

    为什么：FTS 要的是**词袋**（分词串），向量要的是**语义**——把自然语言退化成
    jieba 分词后的空格串，等于把 bge 的语义优势扔掉一半。实测（批 6a）：
    改述类 recall@5 34/40（85%）→ 38/40（95%），而同期"假 embedder"对照组几乎
    不变（1/2）——说明变好的是**口径**在削弱真模型，不是噪声。

    判别方式：分词串里 "退货时限" 会被切成 "退货 时限"，自然语言里它是连续的。
    """
    from pmstudio.infrastructure.sqlite.tokenize import index_source, index_text

    body = "退货时限是七天"
    natural = index_text(title="手册", heading_path=("售后",), text=body)
    bag = index_source(title="手册", heading_path=("售后",), text=body)

    assert body in natural, f"自然语言里正文应连续，实得 {natural!r}"
    assert body not in bag, f"分词串里正文被打散了（{bag!r}）——那正是要改掉的"
    assert "售后" in natural, "章节链也要在"
    assert "手册" in natural, "标题也要在"


def test_the_vector_side_actually_embeds_that_natural_text(tmp_path):
    """R：**真的**是向量侧在用自然语言（不是只有那个函数存在、没人调）。"""
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex
    from tests.support.embedding import FakeEmbedder

    connection = database.connect(tmp_path / "t.db")
    connection.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at)"
        " VALUES ('m1', 'p1', 'u1', 'upload', '手册', 'a.pdf', 'application/pdf', 0,"
        " 'sha1', 'plain', '1', 1, 0, '[]', X'00', '2026-10-18T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
        " heading_path, anchor) VALUES ('m1#c00', 'm1', 'u1', 0, '退货时限是七天', '[]', '第1段')"
    )

    seen: list[str] = []

    class Recorder(FakeEmbedder):
        def embed_many(self, texts):
            seen.extend(texts)
            return super().embed_many(texts)

    SqliteVectorIndex(connection, embedder=Recorder(dimensions=512)).write("m1")

    assert seen, "向量索引一条都没算（测试没有鉴别力）"
    assert "退货时限是七天" in seen[0], f"喂进去的不是自然语言：{seen[0]!r}"
    connection.close()


def _seed_one_chunk(connection) -> None:
    connection.execute(
        "INSERT INTO materials (material_id, project_id, owner_id, origin, title, source_ref,"
        " media_type, bytes_len, sha256, parser_id, parser_version, schema_version, truncated,"
        " warnings, content, created_at)"
        " VALUES ('m1', 'p1', 'u1', 'upload', '手册', 'a.pdf', 'application/pdf', 0,"
        " 'sha1', 'plain', '1', 1, 0, '[]', X'00', '2026-10-18T00:00:00+00:00')"
    )
    connection.execute(
        "INSERT INTO material_chunks (chunk_id, material_id, owner_id, ordinal, text,"
        " heading_path, anchor) VALUES ('m1#c00', 'm1', 'u1', 0, '退货时限是七天', '[]', '第1段')"
    )


class _Tagged(FakeEmbedder):
    """能**区分两次计算**的 embedder：向量第一个分量就是 tag。

    为什么需要它：只断言"表里有 1 条"没有鉴别力——口径变了但**没重算**时，
    旧的那条也还在，表里同样是 1 条，测试照样绿。必须让两次计算**结果不同**，
    才能证明真的重算了。
    """

    def __init__(self, tag: float, dimensions: int = 512) -> None:
        super().__init__(dimensions=dimensions)
        self._tag = tag

    def embed_many(self, texts):
        return [tuple([self._tag] + [0.0] * (self.dimensions - 1)) for _ in texts]


def test_a_changed_embedding_profile_rebuilds_the_vectors(tmp_path):
    """R：口径变了 → 旧向量**必须清掉重算**（不能新旧混在一张表里）。

    口径 = 模型 + 喂进去的文本形态（`embedding.EMBED_PROFILE`）。
    2026-10-19 把文本形态从"jieba 分词串"改成"自然语言"，老库的向量就是分词串口径的
    ——留着就等于一半旧一半新，检索结果不可复现。
    """
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

    path = tmp_path / "t.db"
    connection = database.connect(path)
    _seed_one_chunk(connection)
    SqliteVectorIndex(connection, embedder=_Tagged(1.0)).write("m1")
    first = connection.execute(
        "SELECT embedding FROM material_chunks_vec WHERE chunk_id = 'm1#c00'"
    ).fetchone()[0]
    connection.close()

    # 口径改成别的（模拟"改了文本形态/换了模型"）
    import pmstudio.infrastructure.sqlite.database as db_module

    original = db_module.EMBED_PROFILE
    db_module.EMBED_PROFILE = original + "-other"
    try:
        again = database.connect(path, embedder=_Tagged(2.0))
        # 比**整个 blob**：vec 表存的是序列化 float32，逐字节读第一字节
        # 分不出 1.0 与 2.0（首字节都是 0），必须比整体。
        value = again.execute(
            "SELECT embedding FROM material_chunks_vec WHERE chunk_id = 'm1#c00'"
        ).fetchone()[0]
        assert value != first, "口径变了却没重算——旧向量还在，新旧口径混在一张表里"
        stored = again.execute(
            "SELECT value FROM vector_index_meta WHERE key = ?", (db_module.PROFILE_KEY,)
        ).fetchone()
        assert stored["value"] == original + "-other", "新口径没记上"
        again.close()
    finally:
        db_module.EMBED_PROFILE = original


def test_an_unchanged_profile_keeps_the_existing_vectors(tmp_path):
    """R：口径**没变** → 已有向量**不许被清**（否则每次启动都重算，白烧 ONNX）。

    判别方式：重连时**不传 embedder**——真要重算就必须有 embedder，
    所以"向量还在"就说明它**没有**被清掉重算。
    """
    from pmstudio.infrastructure.sqlite import database
    from pmstudio.infrastructure.sqlite.vector_index import SqliteVectorIndex

    path = tmp_path / "t.db"
    connection = database.connect(path)
    _seed_one_chunk(connection)
    SqliteVectorIndex(connection, embedder=_Tagged(1.0)).write("m1")
    first = connection.execute(
        "SELECT embedding FROM material_chunks_vec WHERE chunk_id = 'm1#c00'"
    ).fetchone()[0]
    connection.close()

    again = database.connect(path)          # **不传 embedder** → 重算不了
    rows = again.execute("SELECT count(*) FROM material_chunks_vec").fetchone()[0]
    assert rows == 1, f"口径没变却把向量清了（{rows} 条）——每次启动都要重算一遍"
    kept = again.execute(
        "SELECT embedding FROM material_chunks_vec WHERE chunk_id = 'm1#c00'"
    ).fetchone()[0]
    assert kept == first, "口径没变却把向量重算了——每次启动都白烧一次 ONNX"
    again.close()


# ── token 闸门：喂模型前把输入裁到模型窗口内（批 7，2026-10-21）───────────────


class _FakeTokenizer:
    """**按字符当 token** 的假 tokenizer（只测闸门的逻辑，不测真分词）。

    真 tokenizer 在 `fastembed` 里（`tokenizer.encode(text).ids` / `tokenizer.decode(ids)`），
    它带 `[CLS]`/`[SEP]` 两个特殊 token；这里照做，好让"裁掉尾部"这件事可断言。
    """

    def __init__(self) -> None:
        self.cls, self.sep = 101, 102

    def encode(self, text):  # type: ignore[no-untyped-def]
        return _Encoding([self.cls] + [ord(c) for c in text] + [self.sep])

    def decode(self, ids):  # type: ignore[no-untyped-def]
        return "".join(chr(i) for i in ids if i not in (self.cls, self.sep))


class _Encoding:
    """真 `tokenizers.Encoding` 的最小形状：只带 `.ids`。"""

    def __init__(self, ids: list[int]) -> None:
        self.ids = ids


def test_a_text_inside_the_window_is_untouched():
    """R：装得下的输入**一个字都不动**（闸门不该改变正常输入）。"""
    from pmstudio.infrastructure.sqlite.embedding import fit_to_window

    text = "向量检索在企业知识库里的召回效果"

    trimmed, dropped = fit_to_window(text, _FakeTokenizer(), limit=100)

    assert trimmed == text
    assert dropped == 0


def test_a_text_over_the_window_is_cut_at_a_token_boundary():
    """R：超窗的输入被裁到窗内，**按 token 边界**（不切碎词），并报出裁了多少。"""
    from pmstudio.infrastructure.sqlite.embedding import fit_to_window

    text = "向量检索" * 100        # 400 字 + 2 个特殊 token

    trimmed, dropped = fit_to_window(text, _FakeTokenizer(), limit=50)

    assert len(_FakeTokenizer().encode(trimmed).ids) <= 50, "裁完还超窗"
    assert dropped > 0, "没报裁了多少——裁剪必须是可观测的"
    assert text.startswith(trimmed), "裁的应该是**尾部**（标题/章节链在前，优先保住）"


def test_the_window_limit_comes_from_the_model_contract():
    """R：上限是**模型契约**（512），不是随手写的数——它与 `MODEL_NAME` 同处一个文件。"""
    from pmstudio.infrastructure.sqlite import embedding

    assert embedding.MODEL_MAX_TOKENS == 512
    assert embedding.EMBED_INPUT_MAX_TOKENS < embedding.MODEL_MAX_TOKENS


@pytest.mark.slow
def test_the_real_model_sees_the_tail_of_a_normal_block():
    """R：**正常块**的尾部对向量是**可见的**（2026-10-21 批 7 的根问题）。

    实测过的反例：块宽 500 时，尾部加独特标记词 → 向量余弦 **1.000000**（尾部完全
    不可见）。现在块宽 400 + token 闸门，尾部必须**看得见**。
    """
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder

    body = "企业知识库里的向量检索要同时兼顾召回率与精度。" * 12   # ≈360 字
    marker = "尾部独特标记词：紫貂氢氯"
    embedder = FastEmbedEmbedder()

    plain = embedder.embed_many([body])[0]
    marked = embedder.embed_many([body + marker])[0]

    assert _cos(plain, marked) < 0.999, (
        "尾部对向量完全不可见（余弦 1.000000）——块宽或闸门没生效"
    )


@pytest.mark.slow
def test_the_real_model_gate_cuts_an_oversized_input(caplog):
    """R：超窗的输入被**裁**（而不是静默截断），且裁后仍能算出向量。"""
    from pmstudio.infrastructure.sqlite.embedding import (
        EMBED_INPUT_MAX_TOKENS,
        FastEmbedEmbedder,
    )

    embedder = FastEmbedEmbedder()
    with caplog.at_level(logging.WARNING):
        vectors = embedder.embed_many(["向量检索" * 500])

    assert len(vectors[0]) == 512
    assert any("裁掉尾部" in record.message for record in caplog.records), (
        f"裁了却没告警（静默降级比失败更贵）；上限 {EMBED_INPUT_MAX_TOKENS}"
    )
