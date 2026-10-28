"""检索验收的纯统计函数：配对增量、精确符号检验、救回/挤掉率。

这些判据原先住在 `scripts/legacy/retrieval_bench.py`；那支 bench 依赖 SQLite，
T10 删除后只把**不依赖存储**的纯函数搬到这里，保持可单测（不依赖模型、不依赖语料）。
"""

from __future__ import annotations

from math import comb


def p_sign_test(wins: int, losses: int) -> float:
    """**精确二项检验（双侧）**：混合赢 `wins` 条、词面赢 `losses` 条时的 p 值。

    为什么用符号检验（而不是 t 检验）：配对差是**二值的**（命中/没命中），
    n 小时 t 检验不靠谱（n=14 的正态近似会给出假显著）；符号检验只看**方向**，
    对小样本是稳的。

    p = 2 · Σ_{k≤min(wins,losses)} C(wins+losses, k) / 2^(wins+losses)
    （**下尾** × 2 = 双侧；超过 1 截到 1。两边都不一致时 p = 1.0：没有方向性证据。）
    """
    if wins <= 0 and losses <= 0:
        return 1.0
    total = wins + losses
    tail = sum(comb(total, k) for k in range(min(wins, losses) + 1))
    return min(1.0, 2 * tail / (2**total))


def paired_increment(lexical_ok: list[bool], hybrid_ok: list[bool]) -> dict[str, float]:
    """配对增量：同一批查询上，混合相对词面的**净变化**。

    `lexical_ok[i]` / `hybrid_ok[i]` = 第 i 条查询在**词面** / **混合**下是否命中目标。
    返回：Δ（净增）、wins（混合赢）、losses（词面赢）、p（符号检验的 p 值）、n。

    为什么必须是**配对**：同一批查询、同一个语料、同一个切法 → 唯一的差别是融合
    → 增量归因干净。分开算两个命中率再相减，会把语料/查询的方差混进去。
    """
    if len(lexical_ok) != len(hybrid_ok):
        raise ValueError("两组结果必须逐条配对")
    wins = sum(1 for lex, hyb in zip(lexical_ok, hybrid_ok, strict=True) if hyb and not lex)
    losses = sum(1 for lex, hyb in zip(lexical_ok, hybrid_ok, strict=True) if lex and not hyb)
    delta = sum(
        (1 if hyb else 0) - (1 if lex else 0)
        for lex, hyb in zip(lexical_ok, hybrid_ok, strict=True)
    )
    return {
        "Δ": delta,
        "wins": wins,
        "losses": losses,
        "p": p_sign_test(wins, losses),
        "n": len(lexical_ok),
    }


def rescue_rate_from_samples(samples: list[dict]) -> dict[str, float]:
    """从**真实检索样本**算救回率与挤掉率（没有 ground truth 也能算）。

    样本来自检索器的观测行（`material_search … fts_hits=… hybrid_hits=…`）：
    - **救回**：词面一个都没命中，混合命中了 → 语义把它捞了回来；
    - **挤掉**：词面命中了，混合一个都没命中 → 融合把好结果掀翻了（倒退风险）。

    注意：救回**不等于**捞对了（可能捞回的是无关材料）→ 精确率那一侧由负样本集
    与合成查询的配对增量负责。
    """
    usable = [item for item in samples if "fts_hits" in item and "hybrid_hits" in item]
    total = len(usable)
    rescued = sum(1 for item in usable if item["fts_hits"] == 0 and item["hybrid_hits"] > 0)
    pushed_out = sum(
        1 for item in usable if item["fts_hits"] > 0 and item["hybrid_hits"] == 0
    )
    return {
        "n": total,
        "救回率": rescued / total if total else 0.0,
        "挤掉率": pushed_out / total if total else 0.0,
    }
