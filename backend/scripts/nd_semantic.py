#!/usr/bin/env python3
"""近重复三层里的**语义层**（余弦）能补上 n-gram 抓不到的那部分吗？

前两轮量到的：
- 字节级（sha256）：真集 recall 0.061，precision 1.0 —— 只抓"逐字相同"
- 近重复（5-gram Jaccard，阈值 0.2）：recall 0.159，precision 1.0
  ——**弱标签下 84% 的"同源"对没抓到**（各站抽到的段落本就不同）

语义层是第三层，也是唯一能跨"不同措辞"的那层。本脚本用**真模型**量它：
同一批真 Tavily 结果，同一批弱标签对，扫余弦阈值的 P/R/F1，并**与 n-gram 的
命中集合对比**（三层到底是不是互补）。

**跑法**：
    cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
      python scripts/nd_semantic.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND / "scripts"))

from nd_compare import _fetch_real, _label_real, grams, jaccard  # noqa: E402

#: 余弦阈值扫描
COS_THRESHOLDS = (0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.99)


def _log(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder

    _log("取真数据…")
    rows = _fetch_real()
    if not rows:
        _log("没有真数据")
        return 2
    _log(f"  {len(rows)} 条真结果")

    labels = _label_real(rows)
    pairs = [
        (i, j, rows[i]["content"], rows[j]["content"], truth)
        for (i, j), truth in labels.items()
        if rows[i]["content"] and rows[j]["content"]
    ]
    positives = sum(1 for *_, t in pairs if t)
    _log(f"  {len(pairs)} 对（弱标签正例 {positives}）")

    _log("算 embedding（真模型）…")
    embedder = FastEmbedEmbedder()
    texts = [row["content"] for row in rows]
    start = time.monotonic()
    vectors = [list(v) for v in embedder.embed_many(texts)]
    _log(f"  {len(vectors)} 条向量，用时 {time.monotonic() - start:.1f}s，维度 {len(vectors[0])}")

    def cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    _log("\n=== 语义层（余弦）阈值扫描 ===")
    _log(f"{'阈值':>6} {'precision':>10} {'recall':>8} {'f1':>8} {'tp':>5} {'fp':>5} {'fn':>5}")
    hits_at: dict[float, set[tuple[int, int]]] = {}
    for threshold in COS_THRESHOLDS:
        tp = fp = fn = 0
        hits: set[tuple[int, int]] = set()
        for i, j, _, _, truth in pairs:
            guess = cosine(vectors[i], vectors[j]) >= threshold
            if truth and guess:
                tp += 1
                hits.add((i, j))
            elif truth:
                fn += 1
            elif guess:
                fp += 1
        hits_at[threshold] = hits
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        _log(f"{threshold:>6.2f} {precision:>10.3f} {recall:>8.3f} {f1:>8.3f} "
             f"{tp:>5} {fp:>5} {fn:>5}")

    # ── 三层命中集合的互补性 ────────────────────────────────────
    _log("\n=== 三层命中集合对比（互补性）===")
    jaccard_hits = {
        (i, j) for i, j, _, _, truth in pairs
        if truth and jaccard(grams(rows[i]["content"]), grams(rows[j]["content"])) >= 0.2
    }
    byte_hits = {
        (i, j) for i, j, _, _, truth in pairs
        if truth and rows[i]["content"] == rows[j]["content"]
    }
    _log(f"  字节级命中 {len(byte_hits)}、n-gram 命中 {len(jaccard_hits)}")
    for threshold in COS_THRESHOLDS:
        cos_hits = hits_at[threshold]
        union = byte_hits | jaccard_hits | cos_hits
        _log(f"  余弦>={threshold:.2f}：命中 {len(cos_hits)}，"
             f"与前两层的并集 {len(union)}，"
             f"余弦独有 {len(cos_hits - byte_hits - jaccard_hits)}，"
             f"n-gram 独有 {len(jaccard_hits - byte_hits - cos_hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
