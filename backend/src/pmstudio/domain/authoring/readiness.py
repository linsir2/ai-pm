"""就绪度：把 `01-规格.md` §4 的合格标准跑成一份可执行的清单。

三条约定（都不是随手定的）：

1. **只有结构缺失算 `blocker`**；语义条款（"事实与推测分开""验收能独立判定"）一律 `warn`
   ——纯代码判不了它们，假装判了比不判更糟。
2. **同一 (章节, code) 只出一条**，明细塞进 `detail`：一个空项目上来报二十几行没人看。
3. **s13 永不参与**：版本历史由 `Version[]` 在导出时生成，不是人写的正文。

这里是纯只读服务：不写文档、不调模型、不进事务。四个消费者见 `02-编排与数据.md` §4.7。
"""

from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.authoring.blocks import Entry
from pmstudio.domain.authoring.views import BlockView, DocumentView, SectionView
from pmstudio.domain.shared.ids import SectionId
from pmstudio.domain.shared.template import MinEntries, TemplateRole
from pmstudio.domain.shared.template_adapter import (
    ROLE_PRESENCE_LABEL,
    TemplateAdapter,
)
from pmstudio.domain.shared.writing import BlockKind

#: 阻塞型未决条目的前缀（`01` §4：命中它的 s11 才挡交付）
BLOCKING_MARK = "〔未决·阻塞〕"
#: 已定记录的前缀（`P28 T5`）：s11 目标题的答案落进来时带这个前缀——
#: 它是"这个问题已经定了"的记录，**不算未决**，不挡交付。
SETTLED_MARK = "〔已定〕"

class Severity(StrEnum):
    BLOCKER = "blocker"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class ReadinessItem:
    section_id: str
    code: str
    title: str
    detail: str
    severity: Severity


class ReadinessChecker:
    def check(
        self, view: DocumentView, *, adapter: TemplateAdapter
    ) -> tuple[ReadinessItem, ...]:
        """判据读**这一份文档的模板视角**（`02` §11.0.69）。

        `adapter` 必填：漏传等于按另一套章节判（同一个检查器同时服务不同模板的文档）。
        走哪几章、缺几条、哪几项表态，全是模板的声明与角色，不是本文件的 id 表。
        """

        by_id = {str(section.section_id): section for section in view.sections}
        items: list[ReadinessItem] = []
        items += self._section_min_entries(by_id, adapter)
        items += self._feature_gaps(view, adapter)
        items += self._kind_presence(by_id, view, adapter)
        items += self._cross_links(by_id, view, adapter)
        items += self._open_questions(by_id.get(str(adapter.open_home)), adapter)
        return tuple(items)

    # —— 判据 ——
    def _section_min_entries(
        self, by_id: dict[str, SectionView], adapter: TemplateAdapter
    ) -> list[ReadinessItem]:
        items: list[ReadinessItem] = []
        # 走**文档自己的章节**（不是全局 13 章表）：模板没声明的章一条都不判。
        for section_id in adapter.section_ids:
            section = by_id.get(str(section_id))
            if section is None:
                continue
            # 槽判据与条数判据是**两件事**：有槽的章（如 standard 的 s8）没声明条数，
            # 照样要逐槽判。顺序不影响去重——同一 (章节, code) 各出一条。
            items += self._slot_gaps(section, adapter)
            declared = adapter.min_entries_for(section_id)
            if declared is None:
                continue
            entries = [
                entry
                for block in section.blocks
                for entry in _entries_with_kind(block, declared.kind)
            ]
            if len(entries) >= declared.count:
                continue
            what = "验收标准" if declared.kind is BlockKind.ACCEPTANCE else "内容"
            items.append(
                ReadinessItem(
                    section_id=str(section_id),
                    code="section_min_entries",
                    title=f"{section.title}还缺 {declared.count - len(entries)} 条{what}",
                    detail=_section_detail(adapter, section_id, declared),
                    severity=Severity.BLOCKER,
                )
            )
        return items

    def _slot_gaps(
        self, section: SectionView, adapter: TemplateAdapter
    ) -> list[ReadinessItem]:
        """有槽的章逐槽判（standard 的 s8「非功能需求还缺 N 项表态」就是它）。

        规则是"有槽即判"、不靠 role：槽是模板声明的骨架，缺项等于这一章没表态
        （加一个 NFR 专属 role 就是没人读的投机——`02` §11.0.68` 的原话）。
        """

        slots = adapter.slots_of(section.section_id)
        if not slots:
            return []
        missing = [slot for slot in slots if not _slot_entries(section, slot.name)]
        if not missing:
            return []
        return [
            ReadinessItem(
                section_id=str(section.section_id),
                code="section_min_entries",
                title=f"{section.title}还缺 {len(missing)} 项表态",
                detail="、".join(slot.title for slot in missing),
                severity=Severity.BLOCKER,
            )
        ]

    def _feature_gaps(
        self, view: DocumentView, adapter: TemplateAdapter
    ) -> list[ReadinessItem]:
        home = str(adapter.feature_home)
        features = _features(view, adapter)
        if not features:
            return [
                ReadinessItem(
                    section_id=home,
                    code="feature_field_empty",
                    title=f"{adapter.title_of(home)}还没有任何功能",
                    detail="还没有任何功能",
                    severity=Severity.BLOCKER,
                )
            ]
        if not view.feature_gaps:
            return []
        grouped: dict[str, list[str]] = {}
        for gap in view.feature_gaps:
            grouped.setdefault(str(gap.feature_slug), []).append(gap.title)
        detail = "；".join(
            f"{slug}：{'、'.join(titles)}" for slug, titles in grouped.items()
        )
        return [
            ReadinessItem(
                section_id=home,
                code="feature_field_empty",
                title="功能字段还有缺口",
                detail=detail,
                severity=Severity.BLOCKER,
            )
        ]

    def _kind_presence(
        self, by_id: dict[str, SectionView], view: DocumentView, adapter: TemplateAdapter
    ) -> list[ReadinessItem]:
        """有内容就该有某种块的章（结构关联，warn）：走模板标了 steps / decision 的章。"""

        items: list[ReadinessItem] = []
        for role, section_id in (
            (TemplateRole.STEPS, adapter.steps_section()),
            (TemplateRole.DECISION, adapter.decision_section()),
        ):
            if section_id is None:
                continue
            section = by_id.get(str(section_id))
            if section is None:
                continue
            kind = role_to_kind(role)
            label = ROLE_PRESENCE_LABEL[role]
            entries = [
                entry for block in section.blocks for entry in _entries_with_kind(block, kind)
            ]
            if entries:
                continue
            # 只在"已经有东西可关联"时才提醒，空文档由 min_entries 报（不重复吵）
            if not _any_entry(by_id):
                continue
            items.append(
                ReadinessItem(
                    section_id=str(section_id),
                    code="section_kind_present",
                    title=f"{section.title}还没有{label}",
                    detail=f"这类内容用 {kind.value} 块写；拿不准就出题问用户",
                    severity=Severity.WARN,
                )
            )
        return items

    def _cross_links(
        self, by_id: dict[str, SectionView], view: DocumentView, adapter: TemplateAdapter
    ) -> list[ReadinessItem]:
        """功能写了依赖、而数据与接口那章还是空的 → warn。

        落点由模板的 `role = "data"` 指（standard 的 s7）；模板没标就**整条不跑**
        （作者 2026-10-15 拍板）——启发式宁可没有，也不能指到别的章上。
        """

        data_id = adapter.data_section()
        if data_id is None:
            return []
        section = by_id.get(str(data_id))
        if section is None:
            return []
        has_dependencies = any(
            _field_entries(feature, "dependencies") for feature in _features(view, adapter)
        )
        has_interface = any(
            entry
            for block in section.blocks
            if block.kind in (BlockKind.INTERFACE, BlockKind.ENTITY)
            for entry in block.entries
        )
        if not has_dependencies or has_interface:
            return []
        return [
            ReadinessItem(
                section_id=str(data_id),
                code="cross_section_link",
                title="功能写了依赖，但数据与接口还是空的",
                detail="依赖要写清对接方式：用 interface / entity 块补一条",
                severity=Severity.WARN,
            )
        ]

    def _open_questions(
        self, section: SectionView | None, adapter: TemplateAdapter
    ) -> list[ReadinessItem]:
        if section is None:
            return []
        # 已定记录不算未决（`P28 T5`）：不带这一句，"答过的问题"会被算成"还开着的问题"，
        # 让就绪度说假话。老文档没有这种条目，行为不变。
        entries = [
            entry
            for block in section.blocks
            for entry in block.entries
            if not entry.text.startswith(SETTLED_MARK)
        ]
        if not entries:
            return []
        blocking = [entry for entry in entries if entry.text.startswith(BLOCKING_MARK)]
        if blocking:
            detail = "；".join(entry.text.removeprefix(BLOCKING_MARK) for entry in blocking[:3])
            return [
                ReadinessItem(
                    section_id=str(adapter.open_home),
                    code="open_blocking_question",
                    title=f"有 {len(blocking)} 项阻塞未决",
                    detail=detail,
                    severity=Severity.BLOCKER,
                )
            ]
        return [
            ReadinessItem(
                section_id=str(adapter.open_home),
                code="open_blocking_question",
                title=f"有 {len(entries)} 项未决（没标阻塞）",
                detail="未决不挡交付，但每条要有待决人",
                severity=Severity.WARN,
            )
        ]


# —— 内部 ——

_SECTION_DETAIL = {
    "s1": "至少 1 条：现状 / 问题 / 不做的代价",
    "s2": "至少 2 条：目标（可验证）与非目标各一条",
    "s3": "至少 1 条：角色 + 场景 + 痛点",
    "s4": "至少 2 条：本期范围与本期不做各一条",
    "s10": "至少 1 条：假设（带失败后果）或依赖（带失联怎么办）",
    "s12": "至少 1 条验收标准；每条要能独立判定真假",
}


def _section_detail(
    adapter: TemplateAdapter, section_id: SectionId | str, declared: MinEntries
) -> str:
    """缺口明细文案：standard 用原文案（导出与界面都见过它），别的模板用 purpose 兜底。"""

    legacy = _SECTION_DETAIL.get(str(section_id))
    if legacy is not None:
        return legacy
    what = "验收标准" if declared.kind is BlockKind.ACCEPTANCE else "内容"
    return f"至少 {declared.count} 条{what}：{adapter.purpose_of(section_id)}"


def role_to_kind(role: TemplateRole) -> BlockKind:
    """角色 → 这一章该有的块类型（与适配器 `only_kind_for` 同一条映射）。"""

    return BlockKind.STEPS if role is TemplateRole.STEPS else BlockKind.DECISION


def _entries_with_kind(block: BlockView, kind: BlockKind | None) -> list[Entry]:
    entries: list[Entry] = []
    if kind is None or block.kind is kind:
        entries.extend(block.entries)
    for child in block.children:
        entries.extend(_entries_with_kind(child, kind))
    return entries


def _slot_entries(section: SectionView, slot_name: str) -> list[Entry]:
    for block in section.blocks:
        if block.field == slot_name:
            return list(block.entries)
    return []


def _features(view: DocumentView, adapter: TemplateAdapter) -> list[BlockView]:
    """功能子树：找模板的**功能锚点章**（不是 s5——那只是 standard 的锚点 id）。"""

    home = str(adapter.feature_home)
    section = next((item for item in view.sections if str(item.section_id) == home), None)
    if section is None:
        return []
    return [block for block in section.blocks if block.kind is BlockKind.FEATURE]


def _field_entries(feature: BlockView, field: str) -> list[Entry]:
    for child in feature.children:
        if child.field == field:
            return list(child.entries)
    return []


def _any_entry(by_id: dict[str, SectionView]) -> bool:
    return any(
        entry
        for section in by_id.values()
        for block in section.blocks
        for entry in _entries_with_kind(block, None)
    )
