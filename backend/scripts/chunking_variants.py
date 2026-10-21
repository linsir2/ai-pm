"""**切点规则实验**：行优先（现状）vs **标点优先、贴近预算**（作者要的）。

作者的原话：「我希望的分块**是有区间的**，就是在**预算周围**找**终结符**（。；这种明显是分隔的），
这样子就不会分断」。

现状（`extract.chunk_text` + `limits.parts_of`）是**行优先**：逐行累积，遇到换行就切
→ 块可能远小于预算（短行多时），切点也不保证是句子边界。

本脚本实现**标点优先**的切法（当实验变体，不动生产代码）：
- **标题行仍然起新块**（保住 `heading_path`——这是我们与英文文档的关键差异）；
- 块内按**句末标点**切句，再贪心装句到上限 → 块贴近预算、不切句子。

然后在同一套指标下比较：块级 r5 · MRR · 跨块完整率 · 上下文层(块/份/字) · 块数。
"""
import sys

sys.path.insert(0, "src")

import re

from pmstudio.infrastructure.tools.limits import _SENTENCE_ENDS

#: 标题行的形状（与 `extract._HEADING` 同源：# / 1.2 / 一、/ 第 3 章）
_HEADING_SHAPE = re.compile(
    r"^\s*(?:#{1,6}\s+\S|\d+(?:\.\d+)*[.、]\s*\S"
    r"|[一二三四五六七八九十]+、\s*\S"
    r"|第[一二三四五六七八九十百\d]+[章节][、.．]?\s*\S)"
)


def _is_heading(line: str) -> bool:
    return bool(_HEADING_SHAPE.match(line))


def chunk_by_sentence(text: str, *, max_chars: int) -> list[str]:
    """标点优先切块：标题行起新块；块内按句末标点切句，贪心装到上限。

    与 `extract.chunk_text` 的差别只有一处：**块内**的切点从"行边界"改成
    "最近的句末标点"（找不到标点才退回行边界/硬切）→ 块更贴近上限、且不切句子。
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal current, size
        if current:
            chunks.append("\n".join(current))
            current = []
            size = 0

    for line in text.splitlines():
        if not line.strip():
            continue
        if _is_heading(line):
            flush()
            current.append(line)
            size += len(line) + 1
            continue
        # 行内按句末标点再切（标点留在句尾）。
        # ⚠️ 用 `split`（在标点**后**切），**不用** `findall`——后者会丢掉
        # 不以标点结尾的行尾（**内容丢失**，第一版就踩了，导致 r5 假跌）。
        pieces = [piece for piece in re.split(rf"(?<=[{_SENTENCE_ENDS}])", line) if piece]
        for piece in pieces:
            if not piece:
                continue
            if size and size + len(piece) > max_chars:
                flush()
            current.append(piece)
            size += len(piece)
        size += 1  # 换行也算一点
    flush()
    return chunks
