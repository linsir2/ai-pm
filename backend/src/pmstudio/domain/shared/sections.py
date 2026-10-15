"""13 章骨架——**`standard` 模板的原材料**，不是引擎的章节真相源（`02` §11.0.69）。

切割线（`tests/architecture/test_section_tables.py` 守着）：

- `SECTIONS` / `SECTION_SLOTS` / `SlotSpec` / `slot_block_id` 是**原料**：
  `template.py` 拿它们算出 `standard_template()`，`/schema` 吐静态目录，
  块 id 命名用它——照旧可 import；
- `SECTION_IDS` / `SECTION_BY_ID` / `section_spec` / `section_slot` 是**按 id 查
  standard 的表**：
  引擎读它就等于把 standard 当真相源（自定义模板里那一章的标题与槽会被 standard 顶掉），
  所以**只许测试引用**，src 里 import 即红；
- "未决清单的家""功能子树的家"不再有常量：它们是模板的**锚点**，走
  `TemplateAdapter.open_home` / `.feature_home`。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import UnknownSection
from pmstudio.domain.shared.ids import BlockId, SectionId
from pmstudio.domain.shared.writing import BlockKind


@dataclass(frozen=True, slots=True)
class SectionSpec:
    section_id: SectionId
    title: str
    #: 这一章是干什么的（一句话）。界面章节头显示它，来源是本文件的唯一一份。
    purpose: str
    allows_features: bool = False


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        SectionId("s1"),
        "背景与问题",
        "为什么现在要做：现状有什么问题、不做会付出什么代价。",
    ),
    SectionSpec(
        SectionId("s2"),
        "目标与非目标",
        "要达成什么、明确不做什么；目标要能被验收。",
    ),
    SectionSpec(
        SectionId("s3"),
        "用户与受众",
        "给谁用：有哪些角色、各自在什么场景、痛点是什么。",
    ),
    SectionSpec(
        SectionId("s4"),
        "范围与边界",
        "本期做到哪、本期不做哪、边界情况谁来处理。",
    ),
    SectionSpec(
        SectionId("s5"),
        "功能需求",
        "每个功能一棵子树；必填 6 项：名称、一句话描述、服务的目标、面向角色、"
        "异常与边界、验收标准。",
        allows_features=True,
    ),
    SectionSpec(
        SectionId("s6"),
        "业务流程与交互",
        "主流程与异常路径，按步骤写清谁在什么时候做什么。",
    ),
    SectionSpec(SectionId("s7"), "数据与接口", "有哪些实体、和外部怎么对接。"),
    SectionSpec(
        SectionId("s8"),
        "非功能需求",
        "六项表态：性能、可用性、安全、兼容、可观测、成本，各要有内容。",
    ),
    SectionSpec(SectionId("s9"), "技术方案与约束", "关键决策与备选，写清为什么这么选。"),
    SectionSpec(
        SectionId("s10"),
        "假设、约束与依赖",
        "假设要写「错了会怎样」；依赖要写「失联怎么办」。",
    ),
    SectionSpec(
        SectionId("s11"),
        "风险与开放问题",
        "还没定的都放这儿；堵住交付的要标出来。",
    ),
    SectionSpec(
        SectionId("s12"),
        "上线、验收与迭代",
        "怎么上线、怎么算验收通过、后续怎么迭代。",
    ),
    SectionSpec(SectionId("s13"), "版本历史", "不用手写，导出时由版本表生成。"),
)

SECTION_IDS: tuple[SectionId, ...] = tuple(spec.section_id for spec in SECTIONS)
SECTION_BY_ID: dict[SectionId, SectionSpec] = {spec.section_id: spec for spec in SECTIONS}


@dataclass(frozen=True, slots=True)
class SlotSpec:
    """章节槽：章节下的固定子块（骨架，不是内容）。

    只有 s8 有槽（性能 / 可用性 / 安全 / 兼容 / 可观测 / 成本）——"六项表态"这条合格标准
    因此从形容词变成可判据（`01-规格.md` §4、`02-编排与数据.md` §4.8）。
    """

    name: str
    title: str
    kind: BlockKind


SECTION_SLOTS: dict[SectionId, tuple[SlotSpec, ...]] = {
    SectionId("s8"): tuple(
        SlotSpec(name, title, BlockKind.NFR)
        for name, title in (
            ("performance", "性能"),
            ("availability", "可用性"),
            ("security", "安全"),
            ("compatibility", "兼容"),
            ("observability", "可观测"),
            ("cost", "成本"),
        )
    ),
}


def section_slot(section_id: SectionId | str, name: str) -> SlotSpec | None:
    for slot in SECTION_SLOTS.get(SectionId(section_id), ()):
        if slot.name == name:
            return slot
    return None


def slot_block_id(section_id: SectionId | str, name: str) -> BlockId:
    return BlockId(f"{section_id}.{name}")


def section_spec(section_id: SectionId | str) -> SectionSpec:
    spec = SECTION_BY_ID.get(SectionId(section_id))
    if spec is None:
        raise UnknownSection(section_id)
    return spec
