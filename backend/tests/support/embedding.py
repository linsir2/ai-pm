"""测试替身：确定性假向量（无模型、无网络、无随机）。

分桶 = 单字 + 双字（字符级）：同字多的两段文本必然共享更多桶，
与真 embedding"同义更近"的方向一致——这是它能驱动 RRF 顺序断言的原因。
纯双字不够：`怎么把货退回去` 与 `用户退货流程` 没有相同的双字（退回 vs 退货），
单字是它们唯一的公共部分。
"""

import hashlib
import math


def _units(text: str) -> list[str]:
    chars = [c for c in text if not c.isspace()]
    unigrams = chars
    bigrams = [chars[i] + chars[i + 1] for i in range(len(chars) - 1)]
    return unigrams + bigrams


def _bucket(unit: str, dimensions: int) -> int:
    digest = hashlib.md5(unit.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % dimensions


class FakeEmbedder:
    """确定性假实现：同文本同向量；同字越多越近。"""

    def __init__(self, dimensions: int = 8) -> None:
        self.dimensions = dimensions

    def embed_many(self, texts):
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> tuple[float, ...]:
        counts = [0.0] * self.dimensions
        for unit in _units(text):
            counts[_bucket(unit, self.dimensions)] += 1.0
        norm = math.sqrt(sum(c * c for c in counts))
        if norm == 0.0:
            return tuple(counts)
        return tuple(c / norm for c in counts)
