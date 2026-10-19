"""向量适配层：**不联网**也能测的只有协议与假实现；真模型一条单独打标。"""

import math

import pytest

from tests.support.embedding import FakeEmbedder


def _cos(a, b):
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / denom if denom else 0.0


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
