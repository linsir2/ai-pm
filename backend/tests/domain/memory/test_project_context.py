"""L1 项目上下文：从文档现状 + 记忆 + 就绪度构建紧凑的跨轮上下文。

口径：`.codex/docs/设计/05-DDD架构.md` §记忆层设计。
"""

from datetime import datetime

import pytest

from pmstudio.domain.authoring.readiness import ReadinessChecker, Severity
from pmstudio.domain.authoring.views import (
    BlockView,
    DocumentView,
    SectionView,
)
from pmstudio.domain.memory.context import ProjectContext
from pmstudio.application.context import build_project_context, render_project_context
from pmstudio.domain.memory.memory import MemoryEntry, MemoryKind
from pmstudio.domain.shared.ids import (
    EvidenceId,
    ProjectId,
    SectionId,
)

NOW = datetime(2026, 10, 8, 10, 0, 0)
PROJECT = ProjectId("p-ctx-test")


def _entry(text: str, kind: MemoryKind = MemoryKind.DECISION, refs=("E1",)) -> MemoryEntry:
    return MemoryEntry(
        memory_id=f"mem-{hash(text) % 1000}",
        kind=kind,
        text=text,
        refs=tuple(EvidenceId(r) for r in refs),
        round_id="r1",
        created_at=NOW,
        updated_at=NOW,
    )


def _section(section_id: str, *entries: str) -> SectionView:
    blocks = tuple(BlockView(block_id=f"b-{i}", kind="text", entries=(e,)) for i, e in enumerate(entries))
    return SectionView(section_id=SectionId(section_id), title=f"{section_id} 标题", blocks=blocks)


def _document(*sections: SectionView) -> DocumentView:
    return DocumentView(
        document_id="d-test",
        project_id=PROJECT,
        title="测试项目",
        version_no=1,
        sections=tuple(sections),
    )


class TestBuildProjectContext:
    """build_project_context: 纯函数，从文档 + 记忆 + 就绪度构建 L1 上下文。"""

    def test_empty_document_returns_empty_context(self):
        doc = _document()
        ctx = build_project_context(doc, (), ())
        assert ctx.domain == ""
        assert ctx.target_users == ""
        assert ctx.core_value == ""
        assert ctx.sections_covered == ()
        assert ctx.features_defined == ()
        assert ctx.total_confirmed_entries == 0

    def test_coverage_empty_sections(self):
        doc = _document(_section("s1"), _section("s5"))
        ctx = build_project_context(doc, (), ())
        covered = dict(ctx.sections_covered)
        assert covered.get("s1") == "empty"
        assert covered.get("s5") == "empty"

    def test_coverage_partial_and_complete(self):
        doc = _document(
            _section("s1", "背景", "问题"),
            _section("s2", "目标"),
            _section("s5", "功能需求"),
        )
        ctx = build_project_context(doc, (), ())
        covered = dict(ctx.sections_covered)
        assert covered["s1"] == "complete"  # ≥2 entries, meets minimum
        assert covered["s2"] == "partial"  # 1 entry, below minimum
        assert covered["s5"] == "partial"  # 1 entry, below minimum

    def test_features_defined_from_s5_blocks(self):
        s5 = SectionView(
            section_id=SectionId("s5"),
            title="功能需求",
            blocks=(
                BlockView(block_id="b1", kind="feature", entries=("功能A",), feature_slug="feature-a"),
                BlockView(block_id="b2", kind="feature", entries=("功能B",), feature_slug="feature-b"),
            ),
        )
        doc = _document(s5)
        ctx = build_project_context(doc, (), ())
        assert set(ctx.features_defined) == {"feature-a", "feature-b"}

    def test_total_confirmed_entries(self):
        doc = _document(
            _section("s1", "背景", "问题"),
            _section("s2", "目标"),
        )
        ctx = build_project_context(doc, (), ())
        assert ctx.total_confirmed_entries == 3

    def test_top_decisions_from_memory(self):
        entries = tuple(_entry(f"决策{i}", MemoryKind.DECISION) for i in range(15))
        doc = _document(_section("s1", "背景"))
        ctx = build_project_context(doc, entries, ())
        # 最多取 10 条
        assert len(ctx.top_decisions) == 10
        assert all("决策" in d for d in ctx.top_decisions)

    def test_top_decisions_filters_non_decision_kinds(self):
        entries = (
            _entry("教训", MemoryKind.LESSON),
            _entry("决策", MemoryKind.DECISION),
            _entry("否掉", MemoryKind.REJECTED),
        )
        doc = _document(_section("s1", "背景"))
        ctx = build_project_context(doc, entries, ())
        assert ctx.top_decisions == ("决策",)

    def test_open_questions_from_s11(self):
        s11 = SectionView(
            section_id=SectionId("s11"),
            title="风险与开放问题",
            blocks=(
                BlockView(block_id="b-oq", kind="open_questions", entries=("退还周期多长？", "老年用户如何使用？")),
            ),
        )
        doc = _document(s11)
        ctx = build_project_context(doc, (), ())
        assert set(ctx.open_questions) == {"退还周期多长？", "老年用户如何使用？"}


class TestRenderProjectContext:
    """render_project_context: 固定格式，≤30 行。"""

    def test_empty_context_renders_minimal(self):
        ctx = ProjectContext()
        text = render_project_context(ctx)
        assert "<project-context>" in text
        assert "</project-context>" in text

    def test_render_contains_domain(self):
        ctx = ProjectContext(domain="社区团购平台")
        text = render_project_context(ctx)
        assert "社区团购平台" in text

    def test_render_contains_users(self):
        ctx = ProjectContext(target_users="家庭主妇、上班族")
        text = render_project_context(ctx)
        assert "家庭主妇" in text

    def test_render_contains_coverage(self):
        ctx = ProjectContext(sections_covered=(("s1", "partial"), ("s5", "complete")))
        text = render_project_context(ctx)
        assert "s1" in text
        assert "s5" in text

    def test_render_contains_features(self):
        ctx = ProjectContext(features_defined=("group-buying",))
        text = render_project_context(ctx)
        assert "group-buying" in text

    def test_render_contains_decisions(self):
        ctx = ProjectContext(top_decisions=("预售7天退还", "团长保证金"))
        text = render_project_context(ctx)
        assert "预售7天退还" in text
        assert "团长保证金" in text

    def test_render_contains_open_questions(self):
        ctx = ProjectContext(open_questions=("退还周期多长？",))
        text = render_project_context(ctx)
        assert "退还周期多长？" in text

    def test_render_under_30_lines(self):
        ctx = ProjectContext(
            domain="社区团购平台",
            target_users="家庭主妇、上班族",
            core_value="30分钟达",
            key_constraints=("团长流失率15%", "冷链必需"),
            sections_covered=tuple((f"s{i}", "partial") for i in range(1, 13)),
            features_defined=("group-buying", "presale", "delivery"),
            total_confirmed_entries=42,
            top_decisions=tuple(f"决策{i}" for i in range(10)),
            open_questions=tuple(f"问题{i}" for i in range(5)),
        )
        text = render_project_context(ctx)
        lines = text.strip().split("\n")
        assert len(lines) <= 30, f"渲染超过 30 行: {len(lines)} 行"
