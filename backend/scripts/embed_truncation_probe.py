#!/usr/bin/env python3
"""bge 的 512-token 静默截断到底伤多重？（2026-10-19 实测）

**实测到的截断**：`embed(700字)` 与 `embed(前510字)` 余弦 = **1.000000**；
尾部加一个独特标记词，向量**完全不变**（标记词根本没进模型）。
→ 长块的**尾部对向量不可见**。

本脚本量这个事实对**检索**的实际影响：把"答案"放在块的尾部，用改述问它。

三种块宽各测一遍：
- 500 字（现状的 `CHUNK_CHARS`）+ 标题 → token 数超 512 → 尾部被截
- 400 字 → 加上标题仍 < 512 → 全文进向量
- 300 字

**跑法**：
    cd backend && HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \\
      python scripts/embed_truncation_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / "src"))

FILLER = (
    "机柜内网线要走背板槽位，电源线与信号线必须分开走，标签朝外便于巡检核对。"
    "导出记录必须能追溯到具体的人，日志留存期限按监管要求执行。"
)
#: 埋在尾部的那句话（改述问它，看能不能被召回来）
ANSWER = "服务器机房的温湿度必须每十五分钟采样一次并留存曲线。"
QUESTION = "机房的温湿度多久测一次"


def _log(text: str) -> None:
    print(text, flush=True)


def main() -> int:
    import numpy as np

    from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder

    embedder = FastEmbedEmbedder()
    _log("造语料：每块 尾部埋一句答案，其余是填充…")

    for width in (900, 700, 500, 400):
        blocks = []
        for index in range(12):
            # **每块的填充必须不同**：填充相同的话，截断后 12 个向量会一模一样，
            # 排名纯靠运气，量不出"尾部不可见"这件事。
            body = (FILLER + f"第{index}份材料讲的是主题{index}的内容。") * 20
            body = body[: width - len(ANSWER) - 1]
            blocks.append(body + ANSWER)      # 答案在尾部
        queries = [QUESTION]

        vectors = [np.array(v) for v in embedder.embed_many(blocks)]
        qv = np.array(list(embedder.embed_many(queries))[0])
        sims = [(float(v @ qv / (np.linalg.norm(v) * np.linalg.norm(qv))), i)
                for i, v in enumerate(vectors)]
        best = max(sims)
        rank = sorted(sims, reverse=True).index(best) + 1
        _log(f"  块宽 {width:3} 字：答案块余弦 {best[0]:.4f}，排名 {rank}/12"
             f"{'  ← 召不回（尾部被截）' if rank > 3 else '  ✔ 召得回'}")

    _log("\n结论：块越宽，尾部越可能进不了向量 → 问尾部的问题就召不回来。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
