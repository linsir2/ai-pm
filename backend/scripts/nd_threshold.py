#!/usr/bin/env python3
"""近重复检测的**阈值扫描**与**粒度对比**（块级 vs 文档级）。

接 `nd_compare.py` 的结论往下问两个决定性的问题：

1. **阈值定多少？** 上一轮按构造集定的 0.55，在真实数据上 recall 只有 8%——
   说明它抓不到真实的"同源转载"。这里扫一遍，看 P/R/F1 随阈值怎么走。
2. **在什么粒度上比？** 整份文档级比较被各站自己的评论稀释（同一份法条被 5 个站转载，
   文档级 Jaccard 只有 0.22–0.51）。但**入库后本来就是块**（≤500 字），
   同一段法条引用会落在**各自的块**里 → 块级相似度应该高得多。
   这一条决定"近重复去重"到底该不该做、怎么做。

**跑法**：`cd backend && uv run --with datasketch python scripts/nd_threshold.py`
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))
sys.path.insert(0, str(BACKEND / "scripts"))

from nd_compare import (  # noqa: E402
    _fetch_real,
    _label_real,
    grams,
    jaccard,
)

#: 入库时的块宽上界（与 `tools/limits.py:CHUNK_CHARS` 一致）
CHUNK_CHARS = 500

#: 扫描的阈值
THRESHOLDS = (0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.5, 0.6, 0.7, 0.8)


def _log(text: str) -> None:
    print(text, flush=True)


def chunk_like(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """按入库那套切块（简化版：按行累积到 size 就切，不做标题识别）。"""

    chunks: list[str] = []
    buffer: list[str] = []
    width = 0
    for line in text.splitlines():
        if buffer and width + len(line) > size:
            chunks.append("\n".join(buffer))
            buffer, width = [], 0
        buffer.append(line)
        width += len(line) + 1
    if buffer:
        chunks.append("\n".join(buffer))
    return [c for c in chunks if c.strip()]


def sweep(pairs: list[tuple[str, str, bool]], label: str) -> None:
    _log(f"\n=== {label}（{len(pairs)} 对）===")
    _log(f"{'阈值':>6} {'precision':>10} {'recall':>8} {'f1':>8} {'tp':>5} {'fp':>5} {'fn':>5}")
    for threshold in THRESHOLDS:
        tp = fp = fn = 0
        for left, right, truth in pairs:
            score = jaccard(grams(left), grams(right))
            guess = score >= threshold
            if truth and guess:
                tp += 1
            elif truth:
                fn += 1
            elif guess:
                fp += 1
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        _log(f"{threshold:>6.2f} {precision:>10.3f} {recall:>8.3f} {f1:>8.3f} "
             f"{tp:>5} {fp:>5} {fn:>5}")


def main() -> int:
    _log("取真数据…")
    rows = _fetch_real()
    if not rows:
        _log("没有真数据")
        return 2
    _log(f"  {len(rows)} 条真结果")

    # 文档级（弱标签）
    labels = _label_real(rows)
    doc_pairs = [
        (rows[i]["content"], rows[j]["content"], truth)
        for (i, j), truth in labels.items()
        if rows[i]["content"] and rows[j]["content"]
    ]
    sweep(doc_pairs, "文档级（弱标签）")

    # 块级：把每份结果切成块，块对块比
    blocks: list[tuple[str, str]] = []
    for row in rows:
        for chunk in chunk_like(row["content"]):
            blocks.append((row["url"], chunk))
    _log(f"\n切成 {len(blocks)} 块（每份 {len(rows)} 条结果）")

    # 块级标签：同一份结果内部的块**不算**重复（那是同一篇的不同段）；
    # 不同 URL 的块之间比——同源转载的块才会撞。
    by_url: dict[str, list[int]] = {}
    for index, (url, _) in enumerate(blocks):
        by_url.setdefault(url, []).append(index)
    block_pairs: list[tuple[str, str, bool]] = []
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            if blocks[i][0] == blocks[j][0]:
                continue        # 同一份结果内部：不算
            a, b = blocks[i][1], blocks[j][1]
            if not a or not b:
                continue
            # 弱标签：同源（两条结果都带同一部法规的条文编号）算正例
            shared = set(re.findall(r"第[一二三四五六七八九十百零〇\d]+条", a)) & \
                set(re.findall(r"第[一二三四五六七八九十百零〇\d]+条", b))
            block_pairs.append((a, b, bool(shared)))
    positives = sum(1 for _, _, t in block_pairs if t)
    _log(f"  块级候选对 {len(block_pairs)}（弱标签正例 {positives}）")
    sweep(block_pairs, "块级（弱标签）")

    # 块级 vs 文档级：同一个阈值下，谁抓得多
    _log("\n=== 同一阈值下两种粒度的对比 ===")
    for threshold in (0.3, 0.4, 0.5):
        d_hits = sum(1 for a, b, t in doc_pairs if t and jaccard(grams(a), grams(b)) >= threshold)
        d_pos = sum(1 for _, _, t in doc_pairs if t)
        b_hits = sum(1 for a, b, t in block_pairs if t and jaccard(grams(a), grams(b)) >= threshold)
        b_pos = sum(1 for _, _, t in block_pairs if t)
        _log(f"  阈值 {threshold}: 文档级 {d_hits}/{d_pos} 正例命中，"
             f"块级 {b_hits}/{b_pos} 正例命中")

    # 真实规模下的耗时（单次联网 15 条新 × 50 份已有 = 750 对）
    _log("\n=== 真实规模耗时（750 对）===")
    sample = [blocks[i % len(blocks)][1] for i in range(750)]
    start = time.monotonic()
    for i in range(len(sample)):
        for j in range(i + 1, min(i + 12, len(sample))):
            jaccard(grams(sample[i]), grams(sample[j]))
    exact_ms = (time.monotonic() - start) * 1000
    _log(f"  精确 Jaccard：{exact_ms:.0f} ms（750 对里的一个子集 × 重复计算）")

    try:
        from datasketch import MinHash

        def _mh(text: str):
            mh = MinHash(num_perm=128)
            for gram in grams(text):
                mh.update(gram.encode())
            return mh

        start = time.monotonic()
        for i in range(len(sample)):
            for j in range(i + 1, min(i + 12, len(sample))):
                _mh(sample[i]).jaccard(_mh(sample[j]))
        minhash_ms = (time.monotonic() - start) * 1000
        _log(f"  MinHash(128)：{minhash_ms:.0f} ms")
    except ImportError:
        _log("  datasketch 未装")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
