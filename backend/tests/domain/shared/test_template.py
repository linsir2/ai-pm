"""模板值对象：三锚点恰各一、id 唯一、非空；standard 与现行常量同源。"""

import pytest

from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.domain.shared.ids import SectionId
from pmstudio.domain.shared.template import (
    ANCHOR_ROLES,
    STANDARD_TEMPLATE_ID,
    DocumentTemplate,
    MinEntries,
    TemplatedSection,
    TemplateRole,
    standard_template,
    template_from_dict,
)
from pmstudio.domain.shared.writing import BlockKind


def section(
    section_id: str = "s1", *, role: TemplateRole = TemplateRole.PLAIN
) -> TemplatedSection:
    return TemplatedSection(
        section_id=SectionId(section_id), title=f"章{section_id}", purpose="摆设", role=role
    )


def triple() -> tuple[TemplatedSection, ...]:
    return (
        section("s5", role=TemplateRole.FEATURE),
        section("s11", role=TemplateRole.OPEN_QUESTIONS),
        section("s13", role=TemplateRole.VERSION_HISTORY),
    )


def template(
    sections: tuple[TemplatedSection, ...] = triple(),
) -> DocumentTemplate:
    return DocumentTemplate(template_id="t1", title="测", sections=sections)


def test_ok_template_validates_and_resolves_anchors():
    document_template = template()
    document_template.validate()

    assert document_template.anchor(TemplateRole.FEATURE) == SectionId("s5")
    assert document_template.anchor(TemplateRole.OPEN_QUESTIONS) == SectionId("s11")
    assert document_template.anchor(TemplateRole.VERSION_HISTORY) == SectionId("s13")
    assert document_template.section_ids == (
        SectionId("s5"),
        SectionId("s11"),
        SectionId("s13"),
    )


def test_missing_each_anchor_is_rejected():
    for missing in ANCHOR_ROLES:
        kept = tuple(item for item in triple() if item.role is not missing)
        with pytest.raises(BadTemplateDefinition):
            template(kept).validate()


def test_duplicate_anchor_is_rejected():
    with pytest.raises(BadTemplateDefinition):
        template(triple() + (section("s9", role=TemplateRole.FEATURE),)).validate()


def test_duplicate_section_id_is_rejected():
    with pytest.raises(BadTemplateDefinition):
        template(triple() + (section("s5"),)).validate()


def test_empty_template_and_empty_id_are_rejected():
    with pytest.raises(BadTemplateDefinition):
        template(()).validate()
    with pytest.raises(BadTemplateDefinition):
        DocumentTemplate(template_id="  ", title="空", sections=triple()).validate()


def test_standard_template_matches_current_constants():
    standard = standard_template()
    standard.validate()

    assert standard.template_id == STANDARD_TEMPLATE_ID
    assert len(standard.sections) == 13
    assert standard.anchor(TemplateRole.FEATURE) == SectionId("s5")
    assert standard.anchor(TemplateRole.OPEN_QUESTIONS) == SectionId("s11")
    assert standard.anchor(TemplateRole.VERSION_HISTORY) == SectionId("s13")
    assert [str(item) for item in standard.section_ids] == [f"s{i}" for i in range(1, 14)]
    s8 = next(item for item in standard.sections if item.section_id == SectionId("s8"))
    assert [slot.name for slot in s8.slots] == [
        "performance",
        "availability",
        "security",
        "compatibility",
        "observability",
        "cost",
    ]


# —— 判据声明（`02` §11.0.69：min_entries / coverage 是模板自己的声明）——


def test_min_entries_accepts_bare_count_and_count_with_kind():
    bare = template_from_dict(
        {
            "template_id": "t",
            "title": "x",
            "sections": [
                {"id": "s1", "title": "a", "purpose": "p", "min_entries": 2},
                {"id": "s5", "title": "f", "purpose": "p", "role": "feature"},
                {"id": "s11", "title": "q", "purpose": "p", "role": "open_questions"},
                {"id": "s13", "title": "v", "purpose": "p", "role": "version_history"},
            ],
        }
    )
    assert bare.sections[0].min_entries == MinEntries(count=2, kind=None)

    with_kind = template_from_dict(
        {
            "template_id": "t",
            "title": "x",
            "sections": [
                {
                    "id": "s1",
                    "title": "a",
                    "purpose": "p",
                    "min_entries": {"count": 1, "kind": "acceptance"},
                },
                {"id": "s5", "title": "f", "purpose": "p", "role": "feature"},
                {"id": "s11", "title": "q", "purpose": "p", "role": "open_questions"},
                {"id": "s13", "title": "v", "purpose": "p", "role": "version_history"},
            ],
        }
    )
    assert with_kind.sections[0].min_entries == MinEntries(count=1, kind=BlockKind.ACCEPTANCE)


def test_min_entries_with_unknown_kind_is_rejected():
    with pytest.raises(BadTemplateDefinition):
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {
                        "id": "s1",
                        "title": "a",
                        "purpose": "p",
                        "min_entries": {"count": 1, "kind": "nope"},
                    },
                    {"id": "s5", "title": "f", "purpose": "p", "role": "feature"},
                    {"id": "s11", "title": "q", "purpose": "p", "role": "open_questions"},
                    {"id": "s13", "title": "v", "purpose": "p", "role": "version_history"},
                ],
            }
        )


def test_zero_min_entries_is_rejected():
    """`min_entries = 0` 等于没写——写不出效果的声明不许留在定义里。"""

    with pytest.raises(BadTemplateDefinition):
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {"id": "s1", "title": "a", "purpose": "p", "min_entries": 0},
                    {"id": "s5", "title": "f", "purpose": "p", "role": "feature"},
                    {"id": "s11", "title": "q", "purpose": "p", "role": "open_questions"},
                    {"id": "s13", "title": "v", "purpose": "p", "role": "version_history"},
                ],
            }
        )


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("feature", {"min_entries": 2}),
        ("open_questions", {"min_entries": 2}),
        ("version_history", {"min_entries": 2}),
        ("version_history", {"coverage": 3}),
    ],
)
def test_criteria_on_anchor_sections_are_rejected(role: str, field: dict):
    """三锚点的判据是引擎算的（功能必填字段 / 未决阻塞 / 版本历史不判），
    在模板里再声明一份就是两个真相源——定义时炸。"""

    with pytest.raises(BadTemplateDefinition):
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {"id": "s1", "title": "a", "purpose": "p"},
                    {"id": "s5", "title": "f", "purpose": "p", "role": "feature", **field},
                    {"id": "s11", "title": "q", "purpose": "p", "role": "open_questions"},
                    {
                        "id": "s13",
                        "title": "v",
                        "purpose": "p",
                        "role": "version_history" if role != "version_history" else "plain",
                        **({} if role != "version_history" else field),
                    },
                ],
            }
        )


def test_standard_template_declares_the_six_backbone_minimums():
    """standard 的判据声明住代码（与 TOML 双源钉死）：s12 只数验收标准。"""

    standard = standard_template()
    declared = {
        str(item.section_id): item.min_entries
        for item in standard.sections
        if item.min_entries is not None
    }
    assert declared == {
        "s1": MinEntries(count=1, kind=None),
        "s2": MinEntries(count=2, kind=None),
        "s3": MinEntries(count=1, kind=None),
        "s4": MinEntries(count=2, kind=None),
        "s10": MinEntries(count=1, kind=None),
        "s12": MinEntries(count=1, kind=BlockKind.ACCEPTANCE),
    }
    assert all(item.coverage is None for item in standard.sections)


def test_custom_chapter_id_is_free_form():
    """章节 id 不限 sN：适配器的序号与文案都从模板派生（`lstrip("s")` 一刀切会错）。"""

    custom = template_from_dict(
        {
            "template_id": "t",
            "title": "x",
            "sections": [
                {"id": "overview", "title": "概览", "purpose": "p", "min_entries": 1},
                {"id": "feat", "title": "功能", "purpose": "p", "role": "feature"},
                {"id": "open", "title": "未决", "purpose": "p", "role": "open_questions"},
                {"id": "history", "title": "版本", "purpose": "p", "role": "version_history"},
            ],
        }
    )

    assert [str(item) for item in custom.section_ids] == ["overview", "feat", "open", "history"]
