"""R9 的配对增量与符号检验：**纯函数**（不依赖模型、不依赖语料）→ 可单测。

为什么要有这些函数：R9 要回答的问题是「混合检索相对纯词面的**增量**是多少」。
配对比较 + 精确符号检验是这个问题的最小可辩护做法；把它写成纯函数，
就不必每次都重建语料、跑模型才能验算一遍。
"""

from scripts.retrieval_stats import (
    p_sign_test,
    paired_increment,
    rescue_rate_from_samples,
)


def test_the_sign_test_is_exact_and_two_sided():
    """R：符号检验的 p 值是**精确双侧**的。

    I：混合赢 3 条、词面赢 0 条；
    P：p = 2 · P(X ≤ 0), X~Bin(3, 0.5) = 2/8；
    R：p 正好 0.25（不是 0.125 单侧，也不是 0.5）。
    """
    assert p_sign_test(3, 0) == 0.25
    assert p_sign_test(0, 3) == 0.25, "方向反过来也一样"
    assert p_sign_test(0, 0) == 1.0, "没有方向性证据 → p=1"


def test_the_sign_test_cannot_claim_a_difference_when_the_directions_mix():
    """R：两边都有赢 → p 不该很小（方向性证据不足 → 不许下结论）。"""
    assert p_sign_test(2, 2) == 1.0
    assert p_sign_test(3, 3) == 1.0
    # 5 赢 1 输：方向偏一边，但 p=0.219 → **不能**下结论（0.05 才是门槛）
    assert p_sign_test(5, 1) == 0.21875
    assert p_sign_test(5, 1) > 0.05


def test_the_paired_increment_counts_the_net_change():
    """R：配对增量 = 净增 Δ + 赢/输的条数 + p 值 + n。

    I：4 条查询，混合赢 1 条、词面赢 1 条；
    P：逐条相减再求和；
    R：Δ=0、wins=1、losses=1、n=4、p=1.0。
    """
    result = paired_increment([False, True, False, True], [True, True, False, False])

    assert result["Δ"] == 0
    assert result["wins"] == 1
    assert result["losses"] == 1
    assert result["n"] == 4
    assert result["p"] == 1.0


def test_the_paired_increment_rejects_unpaired_results():
    """R：两组结果必须**逐条配对** → 长度不符就报错（否则 Δ 是假的）。"""
    import pytest

    with pytest.raises(ValueError):
        paired_increment([True], [True, False])


def test_the_rescue_rate_counts_the_queries_the_semantics_saved():
    """R：救回率 = 词面 0 命中而混合命中的那部分；挤掉率 = 反过来。"""
    samples = [
        {"fts_hits": 0, "hybrid_hits": 3},  # 救回
        {"fts_hits": 2, "hybrid_hits": 2},  # 正常
        {"fts_hits": 1, "hybrid_hits": 0},  # 挤掉
    ]

    result = rescue_rate_from_samples(samples)

    assert result["n"] == 3
    assert result["救回率"] == 1 / 3
    assert result["挤掉率"] == 1 / 3


def test_no_samples_means_no_rate():
    """R：一条样本都没有 → 率是 0（**不是**除零崩掉）。"""
    assert rescue_rate_from_samples([]) == {"n": 0, "救回率": 0.0, "挤掉率": 0.0}


def test_lookup_samples_do_not_enter_the_rescue_rate():
    """顶栏搜索的样本没有词面/语义分解，不能混进救回率。"""

    samples = [
        {"hits": 3, "caller": "search"},
        {"fts_hits": 0, "hybrid_hits": 2},
    ]

    result = rescue_rate_from_samples(samples)

    assert result["n"] == 1
    assert result["救回率"] == 1.0
