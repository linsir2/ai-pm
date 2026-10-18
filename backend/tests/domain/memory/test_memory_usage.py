"""记忆命中：现算，不存计数器（`ProjectMemory` 的既有口径）。"""

from datetime import datetime

from pmstudio.domain.memory.memory import MemoryEntry, MemoryKind, unused_entries
from pmstudio.domain.shared.ids import EvidenceId

NOW = datetime(2026, 10, 1)


def _entry(memory_id, refs=("E1",)):
    return MemoryEntry(
        memory_id=memory_id, kind=MemoryKind.LESSON, text="不要做多租户",
        refs=tuple(EvidenceId(r) for r in refs), round_id="r0",
        created_at=NOW, updated_at=NOW,
    )


class TestUnusedEntries:
    def test_never_cited_entry_is_unused(self):
        entries = (_entry("mem-1", refs=("E3",)),)
        cited = {"r-1": frozenset({"E1"}), "r-2": frozenset({"E2"})}
        assert unused_entries(entries, cited, window=10) == frozenset({"mem-1"})

    def test_cited_in_any_recent_round_is_used(self):
        entries = (_entry("mem-1", refs=("E3",)),)
        cited = {"r-1": frozenset({"E1"}), "r-2": frozenset({"E3"})}
        assert unused_entries(entries, cited, window=10) == frozenset()

    def test_window_keeps_only_the_last_n_rounds(self):
        """窗口**含端点**：最近 10 轮里引用过就算命中，更早的不算。"""
        entries = (_entry("mem-1", refs=("E3",)),)
        order = [f"r-{i}" for i in range(11)]
        cited = {**{"r-0": frozenset({"E3"})}, **{f"r-{i}": frozenset() for i in range(1, 11)}}
        assert unused_entries(entries, cited, window=10, order=order) == frozenset({"mem-1"})

    def test_no_entries_means_nothing_to_report(self):
        assert unused_entries((), {}, window=10) == frozenset()


def test_the_prompt_never_learns_about_stale_memories():
    """**决定守卫**：标记只进界面，不进提示词。

    ① M6f 真跑——提示词里多一样东西，模型就可能把它当出处（那次 `mem…` 炸了整趟席位）；
    ② 把仍有效的旧教训标成"久未使用"可能让模型误弃它。
    谁要让标记进提示词，先来改这条测试并说清上面两条怎么解决。
    """
    from pmstudio.application.context import render_memory, render_memory_state

    for render in (render_memory, render_memory_state):
        out = render((_entry("mem-1"),))
        assert "久未使用" not in out
        assert "待复核" not in out
