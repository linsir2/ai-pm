"""Curator 提示词的容量提示（2026-10-18 · 记忆 P2 批 3/4）。

**为什么单独一条测试**：`MEMORY_CAPACITY=60` 的硬上限在代码侧只决定"到了 60 条再新增
必须带 merge/delete"，但**模型看不到那个常量**——它只看到提示词。上限要在模型眼前存在，
否则它会一直 add，直到写入侧整批作废才发现。这条把"提示词必须点名容量"钉住。
"""

from pathlib import Path

PROMPT = (
    Path(__file__).resolve().parents[2]
    / "src/pmstudio/harness/prompts/memory_curator.md"
)


def test_memory_curator_asks_to_merge_before_growing():
    """R：接近容量时先合并再新增（`MEMORY_CAPACITY=60` 的硬规矩要送到模型眼前）。"""
    text = PROMPT.read_text(encoding="utf-8")
    assert "合并" in text
    assert "接近上限" in text or "快满" in text
