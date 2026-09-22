"""13 章骨架。三个上下文都要用这套 id 说话，所以放在 shared。"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import UnknownSection
from pmstudio.domain.shared.ids import SectionId


@dataclass(frozen=True, slots=True)
class SectionSpec:
    section_id: SectionId
    title: str
    allows_features: bool = False


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(SectionId("s1"), "背景与问题"),
    SectionSpec(SectionId("s2"), "目标与非目标"),
    SectionSpec(SectionId("s3"), "用户与受众"),
    SectionSpec(SectionId("s4"), "范围与边界"),
    SectionSpec(SectionId("s5"), "功能需求", allows_features=True),
    SectionSpec(SectionId("s6"), "业务流程与交互"),
    SectionSpec(SectionId("s7"), "数据与接口"),
    SectionSpec(SectionId("s8"), "非功能需求"),
    SectionSpec(SectionId("s9"), "技术方案与约束"),
    SectionSpec(SectionId("s10"), "假设、约束与依赖"),
    SectionSpec(SectionId("s11"), "风险与开放问题"),
    SectionSpec(SectionId("s12"), "上线、验收与迭代"),
    SectionSpec(SectionId("s13"), "版本历史"),
)

SECTION_IDS: tuple[SectionId, ...] = tuple(spec.section_id for spec in SECTIONS)
SECTION_TITLES: dict[SectionId, str] = {spec.section_id: spec.title for spec in SECTIONS}
SECTION_BY_ID: dict[SectionId, SectionSpec] = {spec.section_id: spec for spec in SECTIONS}
FEATURE_HOME = SectionId("s5")


def section_spec(section_id: SectionId | str) -> SectionSpec:
    spec = SECTION_BY_ID.get(SectionId(section_id))
    if spec is None:
        raise UnknownSection(section_id)
    return spec
