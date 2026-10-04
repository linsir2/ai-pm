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
from pmstudio.domain.shared.sections import OPEN_QUESTION_SECTION, SECTION_SLOTS
from pmstudio.domain.shared.writing import BlockKind

#: 阻塞型未决条目的前缀（`01` §4：命中它的 s11 才挡交付）
BLOCKING_MARK = "〔未决·阻塞〕"
#: 已定记录的前缀（`P28 T5`）：s11 目标题的答案落进来时带这个前缀——
#: 它是"这个问题已经定了"的记录，**不算未决**，不挡交付。
SETTLED_MARK = "〔已定〕"

#: 章节级"至少几条非空条目"：(条数下限, 只数哪种块；None = 不挑种类)
SECTION_MIN_ENTRIES: dict[str, tuple[int, BlockKind | None]] = {
    "s1": (1, None),
    "s2": (2, None),
    "s3": (1, None),
    "s4": (2, None),
    "s10": (1, None),
    "s12": (1, BlockKind.ACCEPTANCE),
}

#: 有内容就该有的块类型（结构关联，warn）：章节 → 该有的块类型
KIND_PRESENCE: dict[str, tuple[BlockKind, str]] = {
    "s6": (BlockKind.STEPS, "主流程"),
    "s9": (BlockKind.DECISION, "技术决策"),
}


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
    def check(self, view: DocumentView) -> tuple[ReadinessItem, ...]:
        by_id = {str(section.section_id): section for section in view.sections}
        items: list[ReadinessItem] = []
        items += self._section_min_entries(by_id)
        items += self._feature_gaps(view)
        items += self._kind_presence(by_id)
        items += self._cross_links(by_id, view)
        items += self._open_questions(by_id.get(OPEN_QUESTION_SECTION))
        return tuple(items)

    # —— 判据 ——
    def _section_min_entries(self, by_id: dict[str, SectionView]) -> list[ReadinessItem]:
        items: list[ReadinessItem] = []
        for section_id, (minimum, kind) in SECTION_MIN_ENTRIES.items():
            section = by_id.get(section_id)
            if section is None:
                continue
            entries = [
                entry
                for block in section.blocks
                for entry in _entries_with_kind(block, kind)
            ]
            if len(entries) >= minimum:
                continue
            what = "验收标准" if kind is BlockKind.ACCEPTANCE else "内容"
            items.append(
                ReadinessItem(
                    section_id=section_id,
                    code="section_min_entries",
                    title=f"{section.title}还缺 {minimum - len(entries)} 条{what}",
                    detail=_SECTION_DETAIL[section_id],
                    severity=Severity.BLOCKER,
                )
            )
        items += self._nfr_slots(by_id.get("s8"))
        return items

    def _nfr_slots(self, section: SectionView | None) -> list[ReadinessItem]:
        if section is None:
            return []
        slots = SECTION_SLOTS.get(SectionId("s8"), ())
        missing = [
            slot for slot in slots if not _slot_entries(section, slot.name)
        ]
        if not missing:
            return []
        return [
            ReadinessItem(
                section_id="s8",
                code="section_min_entries",
                title=f"非功能需求还缺 {len(missing)} 项表态",
                detail="、".join(slot.title for slot in missing),
                severity=Severity.BLOCKER,
            )
        ]

    def _feature_gaps(self, view: DocumentView) -> list[ReadinessItem]:
        features = _features(view)
        if not features:
            return [
                ReadinessItem(
                    section_id="s5",
                    code="feature_field_empty",
                    title="功能需求还没有任何功能",
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
                section_id="s5",
                code="feature_field_empty",
                title="功能字段还有缺口",
                detail=detail,
                severity=Severity.BLOCKER,
            )
        ]

    def _kind_presence(self, by_id: dict[str, SectionView]) -> list[ReadinessItem]:
        items: list[ReadinessItem] = []
        for section_id, (kind, label) in KIND_PRESENCE.items():
            section = by_id.get(section_id)
            if section is None:
                continue
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
                    section_id=section_id,
                    code="section_kind_present",
                    title=f"{section.title}还没有{label}",
                    detail=f"这类内容用 {kind.value} 块写；拿不准就出题问用户",
                    severity=Severity.WARN,
                )
            )
        return items

    def _cross_links(
        self, by_id: dict[str, SectionView], view: DocumentView
    ) -> list[ReadinessItem]:
        s7 = by_id.get("s7")
        if s7 is None:
            return []
        has_dependencies = any(
            _field_entries(feature, "dependencies") for feature in _features(view)
        )
        has_interface = any(
            entry
            for block in s7.blocks
            if block.kind in (BlockKind.INTERFACE, BlockKind.ENTITY)
            for entry in block.entries
        )
        if not has_dependencies or has_interface:
            return []
        return [
            ReadinessItem(
                section_id="s7",
                code="cross_section_link",
                title="功能写了依赖，但数据与接口还是空的",
                detail="依赖要写清对接方式：用 interface / entity 块补一条",
                severity=Severity.WARN,
            )
        ]

    def _open_questions(self, section: SectionView | None) -> list[ReadinessItem]:
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
                    section_id=OPEN_QUESTION_SECTION,
                    code="open_blocking_question",
                    title=f"有 {len(blocking)} 项阻塞未决",
                    detail=detail,
                    severity=Severity.BLOCKER,
                )
            ]
        return [
            ReadinessItem(
                section_id=OPEN_QUESTION_SECTION,
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


def _features(view: DocumentView) -> list[BlockView]:
    section = next((item for item in view.sections if str(item.section_id) == "s5"), None)
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
