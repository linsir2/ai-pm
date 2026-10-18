"""记忆机械去重（记忆 P2）：只做**完全重复**，相似度不做。"""

from datetime import datetime

import pytest

from pmstudio.domain.memory.memory import (
    MemoryEntry,
    MemoryKind,
    ProjectMemory,
    normalized_text,
    screen_changes,
)
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId, ProjectId
from tests.domain.memory.test_memory import KNOWN, NOW, SeqIds, add


class TestNormalizedText:
    def test_whitespace_is_ignored(self):
        assert normalized_text("不要  做\n多租户") == normalized_text("不要做多租户")

    def test_trailing_punctuation_is_ignored(self):
        assert normalized_text("不要做多租户。") == normalized_text("不要做多租户")

    def test_leading_punctuation_is_ignored(self):
        # 剥的是 `strip`（两端）而非 `rstrip`——只测末尾会放过写错的实现
        assert normalized_text("。不要做多租户") == normalized_text("不要做多租户")

    def test_leading_and_trailing_only(self):
        # 中间的问号是内容的一部分，不许剥
        assert normalized_text("先定范围？再谈排期") != normalized_text("先定范围再谈排期")

    def test_full_width_and_half_width_punctuation_are_the_same(self):
        assert normalized_text("不要做多租户，") == normalized_text("不要做多租户,")


def _entry(memory_id: str, text: str, kind: MemoryKind = MemoryKind.LESSON) -> MemoryEntry:
    return MemoryEntry(
        memory_id=memory_id,
        kind=kind,
        text=text,
        refs=(EvidenceId("E1"),),
        round_id="r1",
        created_at=datetime(2026, 10, 1),
        updated_at=datetime(2026, 10, 1),
    )


class TestScreenChangesDedup:
    def test_exact_duplicate_is_dropped_with_reason(self):
        memory = ProjectMemory(
            project_id=ProjectId("p1"), entries=(_entry("mem-1", "不要做多租户"),)
        )
        result = screen_changes((add("不要做多租户"),), memory=memory, known_refs=KNOWN)
        assert result.accepted == ()
        assert len(result.dropped) == 1
        assert "完全重复" in result.dropped[0].reason

    def test_normalized_duplicate_is_dropped(self):
        memory = ProjectMemory(
            project_id=ProjectId("p1"), entries=(_entry("mem-1", "不要做多租户。"),)
        )
        result = screen_changes((add(" 不要做多租户 "),), memory=memory, known_refs=KNOWN)
        assert result.accepted == ()

    def test_same_text_different_kind_is_kept(self):
        memory = ProjectMemory(
            project_id=ProjectId("p1"),
            entries=(_entry("mem-1", "不要做多租户", MemoryKind.LESSON),),
        )
        result = screen_changes(
            (add("不要做多租户", kind=MemoryKind.REJECTED),),
            memory=memory,
            known_refs=KNOWN,
        )
        assert len(result.accepted) == 1

    def test_near_duplicate_is_kept(self):
        """R：不引相似度——只在末尾多了三个字的**不是**重复。"""
        memory = ProjectMemory(
            project_id=ProjectId("p1"), entries=(_entry("mem-1", "不要做多租户"),)
        )
        result = screen_changes((add("不要做多租户登录"),), memory=memory, known_refs=KNOWN)
        assert len(result.accepted) == 1

    def test_duplicate_inside_one_batch_is_dropped(self):
        """同一批里两条一模一样的，第二条也丢（工作集在批内长大）。"""
        memory = ProjectMemory.empty(ProjectId("p1"))
        result = screen_changes(
            (add("不要做多租户"), add("不要做多租户")),
            memory=memory,
            known_refs=KNOWN,
        )
        assert len(result.accepted) == 1
        assert len(result.dropped) == 1

    def test_apply_rejects_duplicate_too(self):
        """写入侧用**同一条**判据（一处实现两处用）。"""
        memory = ProjectMemory(
            project_id=ProjectId("p1"), entries=(_entry("mem-1", "不要做多租户"),)
        )
        with pytest.raises(ShapeError):
            memory.apply(
                (add("不要做多租户"),),
                round_id="r1",
                known_refs=KNOWN,
                now=NOW,
                ids=SeqIds(),
            )
