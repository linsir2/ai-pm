"""模板值对象：三锚点恰各一、id 唯一、非空；standard 与现行常量同源。"""

import pytest

from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.domain.shared.ids import SectionId
from pmstudio.domain.shared.template import (
    ANCHOR_ROLES,
    STANDARD_TEMPLATE_ID,
    DocumentTemplate,
    TemplatedSection,
    TemplateRole,
    standard_template,
)


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
