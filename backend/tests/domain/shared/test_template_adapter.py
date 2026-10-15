"""模板适配器：引擎对模板的**唯一**解读入口（`02` §11.0.69）。

为什么要它：模板库只声明"有哪些章、什么角色、有哪些槽、判据是多少"，
不知道"阻塞型章节是哪几章""导出第几章写几""功能挂哪"——那是**引擎**的概念。
两者之间只留这一个类，别在就绪度/汇编/导出各写一遍"锚点/序号/缺省"。

分组：
1. standard 复刻组——逐字钉住今天的行为（就绪度/阻塞/覆盖度/序号/导出章号）；
2. 校验组——成员三闸的消息逐字；
3. custom 组——自由 id 的章、声明的判据、缺省回落；
4. light 组——6 章编号与阻塞集合。
"""

import pytest

from pmstudio.domain.shared.errors import (
    BadTemplateDefinition,
    FeatureNotAllowedHere,
    TargetShapeError,
    UnknownSection,
)
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.template import (
    DocumentTemplate,
    TemplatedSection,
    TemplateRole,
    standard_template,
    template_from_dict,
)
from pmstudio.domain.shared.template_adapter import (
    ROLE_PRESENCE_LABEL,
    TemplateAdapter,
    adapt,
)
from pmstudio.domain.shared.writing import BlockKind


@pytest.fixture(scope="module")
def standard() -> TemplateAdapter:
    return adapt(standard_template())


@pytest.fixture(scope="module")
def light() -> TemplateAdapter:
    from pmstudio.infrastructure.templates import loader

    return adapt(loader.load_template_file(loader.TEMPLATES_DIR / "light.toml"))


def custom(**overrides: object) -> TemplateAdapter:
    """4 章自由 id 模板（`overview/feat/open/history`），给 chapter 参数覆盖字段。"""

    sections = [
        {"id": "overview", "title": "概览", "purpose": "为什么做", "min_entries": 1},
        {"id": "feat", "title": "功能", "purpose": "做什么", "role": "feature"},
        {"id": "open", "title": "未决", "purpose": "还没定的", "role": "open_questions"},
        {"id": "history", "title": "版本", "purpose": "不用手写", "role": "version_history"},
    ]
    if overrides:
        for section in sections:
            if section["id"] == overrides.get("_target", "overview"):
                section.update({k: v for k, v in overrides.items() if not k.startswith("_")})
    return adapt(
        template_from_dict({"template_id": "t", "title": "自建", "sections": sections})
    )


# —— 1. standard 复刻 ——


def test_standard_anchors(standard: TemplateAdapter):
    assert standard.feature_home == SectionId("s5")
    assert standard.open_home == SectionId("s11")
    assert standard.version_home == SectionId("s13")


def test_standard_numbering_matches_section_order(standard: TemplateAdapter):
    assert [standard.number_of(SectionId(f"s{i}")) for i in range(1, 14)] == list(range(1, 14))


def test_standard_blocking_sections_replicate_today(standard: TemplateAdapter):
    assert standard.blocking_sections() == frozenset(
        {"s1", "s2", "s3", "s4", "s5", "s8", "s10", "s12"}
    )


def test_standard_coverage_minimum_replicates_prompt_heuristic(standard: TemplateAdapter):
    expected = {
        "s1": 1, "s2": 2, "s3": 1, "s4": 2, "s5": 2, "s6": 2, "s7": 2, "s8": 6,
        "s9": 2, "s10": 1, "s11": 2, "s12": 1,
    }  # fmt: skip
    assert {sid: standard.coverage_minimum(sid) for sid in expected} == expected


def test_standard_backbone_replicates_six_chapters(standard: TemplateAdapter):
    assert [row[0] for row in standard.backbone()] == ["s1", "s2", "s3", "s4", "s10", "s12"]
    assert standard.backbone()[0][1] == "背景与问题"


def test_standard_steps_and_decision_sections(standard: TemplateAdapter):
    assert standard.steps_section() == SectionId("s6")
    assert standard.decision_section() == SectionId("s9")
    assert standard.only_kind_for(SectionId("s6")) is BlockKind.STEPS
    assert standard.only_kind_for(SectionId("s2")) is None


def test_standard_s8_slots_come_from_the_template(standard: TemplateAdapter):
    assert [slot.name for slot in standard.slots_of(SectionId("s8"))] == [
        "performance",
        "availability",
        "security",
        "compatibility",
        "observability",
        "cost",
    ]
    assert standard.coverage_minimum(SectionId("s8")) == 6


def test_standard_min_entries_come_from_declarations(standard: TemplateAdapter):
    assert standard.min_entries_for(SectionId("s1")).count == 1
    assert standard.required_kind_for(SectionId("s12")) is BlockKind.ACCEPTANCE
    assert standard.required_kind_for(SectionId("s1")) is None
    assert standard.min_entries_for(SectionId("s7")) is None  # 无声明的章不按条数判


def test_standard_titles_and_purposes(standard: TemplateAdapter):
    assert standard.title_of(SectionId("s2")) == "目标与非目标"
    assert standard.purpose_of(SectionId("s2")) == "要达成什么、明确不做什么；目标要能被验收。"
    assert standard.role_of(SectionId("s5")) is TemplateRole.FEATURE


def test_role_presence_labels_cover_the_two_roles():
    assert ROLE_PRESENCE_LABEL == {
        TemplateRole.STEPS: "主流程",
        TemplateRole.DECISION: "技术决策",
    }


# —— 2. 校验三闸 ——


def test_check_target_accepts_chapter_feature_and_slot(standard: TemplateAdapter):
    standard.check_target(SectionId("s2"), None, None)
    standard.check_target(SectionId("s5"), FeatureSlug("order-export"), None)
    standard.check_target(SectionId("s8"), None, "compatibility")


def test_check_target_rejects_unknown_section_naming_the_template(standard: TemplateAdapter):
    with pytest.raises(UnknownSection) as exc:
        standard.check_target(SectionId("s99"), None, None)
    assert exc.value.message == "未知章节：s99（模板「标准版」里没有这一章）"


def test_check_target_rejects_feature_outside_the_anchor(standard: TemplateAdapter):
    with pytest.raises(FeatureNotAllowedHere) as exc:
        standard.check_target(SectionId("s12"), FeatureSlug("x"), None)
    assert exc.value.message == "功能子树只能长在 s5，s12 不行（模板「标准版」）"
    assert exc.value.invariant.value == "D3"


def test_check_target_rejects_unknown_slot(standard: TemplateAdapter):
    with pytest.raises(TargetShapeError) as exc:
        standard.check_target(SectionId("s8"), None, "nope")
    assert exc.value.message == "s8 没有名为 nope 的章节槽（模板「标准版」）"


def test_custom_template_names_its_own_anchors_in_messages():
    adapter = custom()

    with pytest.raises(FeatureNotAllowedHere) as exc:
        adapter.check_target(SectionId("overview"), FeatureSlug("x"), None)
    assert exc.value.message == "功能子树只能长在 feat，overview 不行（模板「自建」）"


# —— 3. custom：自由 id + 声明 ——


def test_custom_chapter_ids_are_free_form_and_numbered_by_position():
    adapter = custom()

    assert [str(sid) for sid in adapter.section_ids] == ["overview", "feat", "open", "history"]
    assert adapter.number_of(SectionId("overview")) == 1
    assert adapter.number_of(SectionId("history")) == 4
    assert adapter.has(SectionId("overview")) is True
    assert adapter.has(SectionId("s1")) is False


def test_custom_blocking_sections_are_anchor_plus_declared():
    assert custom().blocking_sections() == frozenset({"overview", "feat"})


def test_custom_backbone_falls_back_to_one_entry():
    """声明是 BACKBONE 章的兜底：没声明也按"至少 1 条"算（骨架章空着就是缺）。"""

    adapter = custom(_target="overview", role="backbone")

    assert adapter.min_entries_for(SectionId("overview")).count == 1
    assert adapter.coverage_minimum(SectionId("overview")) == 1
    assert adapter.role_of(SectionId("overview")) is TemplateRole.BACKBONE


def test_custom_declared_coverage_wins_over_default():
    adapter = custom(coverage=7)

    assert adapter.coverage_minimum(SectionId("overview")) == 7


def test_custom_slots_drive_coverage_and_blocking():
    adapter = custom(
        _target="overview",
        slots=[
            {"name": "one", "title": "一", "kind": "text"},
            {"name": "two", "title": "二", "kind": "text"},
        ],
    )

    assert adapter.coverage_minimum(SectionId("overview")) == 2
    assert adapter.slot(SectionId("overview"), "two").title == "二"
    assert adapter.slot(SectionId("overview"), "nope") is None


def test_version_history_chapter_refuses_to_be_graded():
    adapter = custom()

    with pytest.raises(BadTemplateDefinition) as exc:
        adapter.coverage_minimum(SectionId("history"))
    assert "版本历史" in exc.value.message


def test_number_of_unknown_section_is_an_error(standard: TemplateAdapter):
    with pytest.raises(UnknownSection):
        standard.number_of(SectionId("s99"))


def test_adapter_is_a_pure_projection_over_the_snapshot(standard: TemplateAdapter):
    assert isinstance(standard, TemplateAdapter)
    assert standard.template.template_id == "standard"
    assert adapt(standard_template()) == standard  # 值相等：没有隐藏状态


def test_custom_template_without_steps_or_decision_chapters():
    adapter = custom()

    assert adapter.steps_section() is None
    assert adapter.decision_section() is None


def test_slots_of_chapter_without_slots_is_empty(standard: TemplateAdapter):
    assert standard.slots_of(SectionId("s2")) == ()


# —— 4. light：6 章 ——


def test_light_numbering_follows_its_own_six_sections(light: TemplateAdapter):
    assert light.number_of(SectionId("s1")) == 1
    assert light.number_of(SectionId("s4")) == 3
    assert light.number_of(SectionId("s5")) == 4
    assert light.number_of(SectionId("s11")) == 5
    assert light.number_of(SectionId("s13")) == 6


def test_light_blocking_sections_are_its_four(light: TemplateAdapter):
    assert light.blocking_sections() == frozenset({"s1", "s2", "s4", "s5"})


def test_light_has_no_steps_or_slots(light: TemplateAdapter):
    assert light.steps_section() is None
    assert light.slots_of(SectionId("s5")) == ()
    assert light.coverage_minimum(SectionId("s5")) == 2


def test_light_check_target_rejects_a_standard_only_chapter(light: TemplateAdapter):
    """s6 在 light 里不存在：提示词之外的兜底——去处写错要说清是模板里没有。"""

    with pytest.raises(UnknownSection) as exc:
        light.check_target(SectionId("s6"), None, None)
    assert "轻量版" in exc.value.message


def test_slotted_chapter_without_slots_is_back_to_default(light: TemplateAdapter):
    adapter = adapt(
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {"id": "a", "title": "A", "purpose": "p", "role": "plain"},
                    {"id": "s5", "title": "F", "purpose": "p", "role": "feature"},
                    {"id": "s11", "title": "Q", "purpose": "p", "role": "open_questions"},
                    {"id": "s13", "title": "V", "purpose": "p", "role": "version_history"},
                ],
            }
        )
    )
    assert adapter.coverage_minimum(SectionId("a")) == 2


def test_backbone_rows_carry_purpose_for_hint_text():
    adapter = adapt(
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {
                        "id": "bg",
                        "title": "背景",
                        "purpose": "现状的痛",
                        "role": "backbone",
                        "min_entries": 2,
                    },
                    {"id": "s5", "title": "F", "purpose": "p", "role": "feature"},
                    {"id": "s11", "title": "Q", "purpose": "p", "role": "open_questions"},
                    {"id": "s13", "title": "V", "purpose": "p", "role": "version_history"},
                ],
            }
        )
    )

    sid, title, purpose, minimum = adapter.backbone()[0]
    assert (str(sid), title, purpose, minimum.count) == ("bg", "背景", "现状的痛", 2)


def test_role_of_unknown_section_is_none(standard: TemplateAdapter):
    assert standard.role_of(SectionId("s99")) is None


def test_adapter_handles_a_template_built_in_code(standard: TemplateAdapter):
    """内存构造的模板（老路径回落就是它）同样能用适配器解读。"""

    built = DocumentTemplate(
        template_id="mem",
        title="内存",
        sections=(
            TemplatedSection(
                section_id=SectionId("only"), title="唯一", purpose="p", role=TemplateRole.PLAIN
            ),
            TemplatedSection(
                section_id=SectionId("f"), title="F", purpose="p", role=TemplateRole.FEATURE
            ),
            TemplatedSection(
                section_id=SectionId("q"), title="Q", purpose="p", role=TemplateRole.OPEN_QUESTIONS
            ),
            TemplatedSection(
                section_id=SectionId("v"),
                title="V",
                purpose="p",
                role=TemplateRole.VERSION_HISTORY,
            ),
        ),
    )
    adapter = adapt(built)

    assert adapter.feature_home == SectionId("f")
    assert adapter.number_of(SectionId("v")) == 4
    assert adapter.blocking_sections() == frozenset({"f"})

# —— DATA role（作者 2026-10-15 拍板）：跨章链接启发式的载体 ——


def test_standard_data_section_is_s7(standard: TemplateAdapter):
    assert standard.data_section() == SectionId("s7")


def test_data_section_is_none_when_the_template_has_no_such_chapter(light: TemplateAdapter):
    """light 没有"数据与接口"这类章 → 那条跨章 warn 整条不跑（不是跑到别的章上）。"""

    assert light.data_section() is None


def test_custom_template_may_mark_its_own_data_chapter():
    adapter = adapt(
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {"id": "data", "title": "数据", "purpose": "p", "role": "data"},
                    {"id": "s5", "title": "F", "purpose": "p", "role": "feature"},
                    {"id": "s11", "title": "Q", "purpose": "p", "role": "open_questions"},
                    {"id": "s13", "title": "V", "purpose": "p", "role": "version_history"},
                ],
            }
        )
    )

    assert adapter.data_section() == SectionId("data")
