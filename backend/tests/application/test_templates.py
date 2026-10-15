"""模板库切片 1：建模板/列模板/取模板 + 创建项目时选模板。"""

import pytest

from pmstudio.application.errors import UnknownTemplate
from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.infrastructure.templates.loader import TEMPLATES_DIR, load_template_file
from tests.support.harness import Harness


def light():
    return load_template_file(TEMPLATES_DIR / "light.toml")


def section(section_id, title, role, slots=()):
    item: dict = {"id": section_id, "title": title, "purpose": "摆设", "role": role}
    if slots:
        item["slots"] = slots
    return item


def triple_sections():
    return [
        section("s5", "功能", "feature"),
        section("s11", "未决", "open_questions"),
        section("s13", "版本", "version_history"),
    ]


async def test_create_project_with_light_template_builds_six_sections():
    harness = Harness(replies={})
    harness.seed_project()

    summary = await harness.create_project()(
        title="轻项目", template_id=light().template_id
    )

    document = harness.documents.get(summary.project_id)
    assert [str(block.block_id) for block in document.blocks][:6] == [
        "s1",
        "s2",
        "s4",
        "s5",
        "s11",
        "s13",
    ]
    assert document.template.template_id == "light"


async def test_create_project_defaults_to_standard():
    harness = Harness(replies={})
    harness.seed_project()

    summary = await harness.create_project()(title="标准项目")

    document = harness.documents.get(summary.project_id)
    assert len(document.blocks) >= 13
    assert document.template.template_id == "standard"


async def test_create_project_with_unknown_template_is_a_404():
    harness = Harness(replies={})
    harness.seed_project()

    with pytest.raises(UnknownTemplate):
        await harness.create_project()(title="坏项目", template_id="nope")


async def test_create_template_saves_and_lists():
    harness = Harness(replies={})

    template_id = await harness.create_template()(
        title="自建", sections=triple_sections()
    )

    listed = await harness.list_templates()()
    assert template_id in [template.template_id for template in listed]
    got = await harness.get_template()(template_id=template_id)
    assert got.title == "自建"
    assert len(got.sections) == 3


async def test_create_template_with_bad_anchors_is_rejected_and_not_saved():
    harness = Harness(replies={})
    before = len(await harness.list_templates()())

    with pytest.raises(BadTemplateDefinition):
        await harness.create_template()(
            title="坏",
            sections=[section("s5", "功能", "feature")],  # 缺两个锚点
        )

    assert len(await harness.list_templates()()) == before


async def test_create_project_with_custom_template_builds_its_roots():
    """自建模板建项目：文档块即该模板的章节（块数证明归应用层，view 渲染是切片 2）。"""

    from pmstudio.domain.shared.ids import ProjectId

    harness = Harness(replies={})
    template_id = await harness.create_template()(
        title="自建", sections=triple_sections()
    )

    summary = await harness.create_project()(title="自建项目", template_id=template_id)

    document = harness.documents.get(ProjectId(summary.project_id))
    assert [str(block.block_id) for block in document.blocks] == ["s5", "s11", "s13"]
    assert document.template.template_id == template_id


async def test_starting_a_round_on_a_custom_template_is_allowed_now():
    """非 standard 模板跑轮不再被拒（`02` §11.0.69：引擎消费者全部迁完，门删）。

    这条替代 `test_starting_a_round_on_non_standard_template_is_rejected_explicitly`
    ——那条钉的是"迁移完成前先拒绝"，迁移完成即作废（需求变更，不是测试出错）。
    """

    from pmstudio.domain.shared.ids import ProjectId

    harness = Harness(replies={})
    template_id = await harness.create_template()(title="自建", sections=triple_sections())
    summary = await harness.create_project()(title="自建项目", template_id=template_id)

    round_id = await harness.start_round().create(
        project_id=ProjectId(summary.project_id), user_input="写点东西"
    )

    assert len(harness.rounds.all_rounds()) == 1
    assert round_id

