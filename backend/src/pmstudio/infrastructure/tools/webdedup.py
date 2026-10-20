"""联网结果的两层去重（批 6a Task 5）：字节级 + 近重复（精确 5-gram Jaccard）。

**为什么是这两层、为什么不用别的**（2026-10-19 真数据实测，50 条 Tavily 结果、
990–1225 对；复算脚本 `backend/scripts/nd_compare.py` / `nd_threshold.py` /
`nd_semantic.py`）：

| 方法 | precision | recall | 真规模耗时 | 结论 |
|---|---|---|---|---|
| sha256（字节级） | 1.000 | 0.061 | 54ms | **留**：便宜且精确 |
| 精确 5-gram Jaccard | 1.000 | **0.159** | 2255ms | **留**：唯一有增量的近重复层 |
| MinHash(128) | 1.000 | 0.093 | **92418ms** | **不引**（datasketch 拖 scipy ~50MB，慢 57 倍） |
| 语义余弦 ≥0.90 | 1.000 | 0.185 | — | **不做**：保精度时对 n-gram **零增量** |
| 语义余弦 ≥0.80 | 0.772 | 0.422 | — | **不做**：放松阈值就误杀不同内容（它是"同主题"检测器） |

**它抓的是"抽到了同一段"，不是"同一主题"**——Tavily 的 `content` 已经是抽取后的
相关片段，两个站引同一条法条时，抽到的段落常常不同（那就不像，抓不到，这是对的：
它们是不同内容）。真集 recall 0.16 就是这个意思。

纯函数：不碰 DB、不碰网络，只在**这一批候选之间**比。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from pmstudio.application.dto import ParsedMaterial

#: 近重复判定阈值（5-gram Jaccard）。**数据给的点**（precision 1.000 / recall 0.159）。
#: 钉死它：真数据上 0.55 的 recall 只有 0.084，等于白做。
NEAR_DUPLICATE_THRESHOLD = 0.2

#: n-gram 的长度（字）。5 是实测定的档位：4 以下太容易撞（不同文档也像），
#: 6 以上太严（同一段转载都不像）。
NGRAM = 5


def web_text(parsed: ParsedMaterial) -> str:
    """一个联网结果的**全部正文**（各块拼起来）。

    去重要比的是"这条结果说了什么"，不是"它第一块说了什么"——
    转载常常在开头一致、后半不同。
    """

    return "\n".join(chunk.text for chunk in parsed.chunks)


def _grams(text: str, n: int = NGRAM) -> set[str]:
    """字符 n-gram 集合（空白归一后）。空文本 → 空集合。"""

    cleaned = re.sub(r"\s+", "", text)
    if not cleaned:
        return set()
    if len(cleaned) <= n:
        return {cleaned}
    return {cleaned[i : i + n] for i in range(len(cleaned) - n + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def dedupe_by_content(
    items: Sequence[ParsedMaterial],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> list[ParsedMaterial]:
    """从一批候选里挑出"内容不重复"的那些，**保留原有顺序**。

    两层判据（先便宜后精确）：
    1. **字节级**：清洗后正文的 sha256 相同 → 重复；
    2. **近重复**：5-gram Jaccard ≥ `threshold` → 重复。

    留的是**第一条**（Tavily 的顺序就是相关序，不能打乱）。
    空正文**不算**和谁都像（否则一批空结果会被并成一条，而且并错了）。
    """

    kept: list[ParsedMaterial] = []
    kept_hashes: set[str] = set()
    kept_grams: list[set[str]] = []
    for item in items:
        text = web_text(item)
        if not text.strip():
            # 空正文：与任何东西都不构成重复（`_jaccard` 对空集合返回 0），
            # 但**自己跟自己**重复——所以只留第一条空的。
            if any(not web_text(k).strip() for k in kept):
                continue
            kept.append(item)
            kept_hashes.add(hashlib.sha256(text.encode("utf-8")).hexdigest())
            kept_grams.append(set())
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest in kept_hashes:
            continue
        grams = _grams(text)
        if any(_jaccard(grams, other) >= threshold for other in kept_grams):
            continue
        kept.append(item)
        kept_hashes.add(digest)
        kept_grams.append(grams)
    return kept
