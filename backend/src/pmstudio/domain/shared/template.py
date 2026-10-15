"""文档模板：章节集 + 锚点，随文档走（行内存快照），不是全局常量。

为什么有这个文件：`sections.py` 的 13 章是**其中一套**模板（standard），不是全部。
引擎真正离不开的不是"13"这个数字，是三个**锚点**——功能章（功能子树的家，D3）、
开放问题章（未决的落点）、版本历史章（导出生成、不判）。每套模板必须恰各一（`validate` 断）。

分层：这里只有值对象 + 校验，零外部 import（domain 纯洁，守卫钉着）。
TOML 解析在 `infrastructure/templates/loader.py`；库表在 `schema.sql:templates`。
"""

from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.domain.shared.ids import SectionId
from pmstudio.domain.shared.sections import SECTION_SLOTS, SECTIONS, SlotSpec
from pmstudio.domain.shared.writing import BlockKind


class TemplateRole(StrEnum):
    """章节在引擎里的角色。切片 1 只**强制**三个锚点，其余是给切片 2 的标注。"""

    #: 功能子树的家（D3：只有它能长 feature 子树）
    FEATURE = "feature"
    #: 未决清单的家（未交代的疑问、跳过的题都落这儿）
    OPEN_QUESTIONS = "open_questions"
    #: 版本历史（不用手写，导出时由版本表生成；不判）
    VERSION_HISTORY = "version_history"
    #: 主流程与异常路径按步骤写（s6 的形态）
    STEPS = "steps"
    #: 关键决策与备选（s9 的形态）
    DECISION = "decision"
    #: 骨架章（就绪度"至少 N 条"那类判据的候选）
    BACKBONE = "backbone"
    #: 纯内容章（引擎无特殊语义）
    PLAIN = "plain"


#: 引擎搭架子必须的三个锚点（拍板：每套模板恰各一）
ANCHOR_ROLES = (
    TemplateRole.FEATURE,
    TemplateRole.OPEN_QUESTIONS,
    TemplateRole.VERSION_HISTORY,
)


@dataclass(frozen=True, slots=True)
class MinEntries:
    """这一章"至少几条非空条目"的声明（`02` §11.0.69）。

    `kind` 不为 None 时只数那种块（如 s12 只数验收标准）——"写了一条普通正文"不算达标。
    声明是**模板自己的事实**：standard 在代码里声明（与 TOML 双源钉死），custom 在 TOML 里声明。
    """

    count: int
    kind: BlockKind | None = None


@dataclass(frozen=True, slots=True)
class TemplatedSection:
    section_id: SectionId
    title: str
    purpose: str
    role: TemplateRole = TemplateRole.PLAIN
    slots: tuple[SlotSpec, ...] = ()
    #: 判据声明：缺省 = 不按条数判（引擎按 role 走缺省值，见 `template_adapter.py`）
    min_entries: MinEntries | None = None
    #: 上下文覆盖度的下限（提示词用"这章写满没有"）：缺省按 role 推导
    coverage: int | None = None


@dataclass(frozen=True, slots=True)
class DocumentTemplate:
    template_id: str
    title: str
    sections: tuple[TemplatedSection, ...]

    def validate(self) -> None:
        """模板定义的合法性：非空、id 唯一、三锚点恰各一。错了在定义时炸。"""

        if not self.template_id.strip():
            raise BadTemplateDefinition("模板 id 不能为空", template_id=self.template_id)
        if not self.sections:
            raise BadTemplateDefinition(
                "模板至少要有一章", template_id=self.template_id
            )
        ids = [str(section.section_id) for section in self.sections]
        if len(set(ids)) != len(ids):
            raise BadTemplateDefinition(
                "模板里章节 id 重复", template_id=self.template_id, section_ids=ids
            )
        for role in ANCHOR_ROLES:
            holders = [str(section.section_id) for section in self.sections if section.role is role]
            if len(holders) != 1:
                raise BadTemplateDefinition(
                    f"模板必须恰有一个 {role.value} 锚点，实际 {len(holders)} 个",
                    template_id=self.template_id,
                    role=role.value,
                )
        self._validate_criteria()

    def _validate_criteria(self) -> None:
        """判据声明的合法性（`02` §11.0.69）。

        三条断，理由都是"两个真相源"：
        1. 三锚点的判据由引擎算（功能必填字段 / 未决阻塞 / 版本历史不判），模板里再声明
           一份就是两份真相——功能章声明"至少 1 条"会和"至少一棵功能子树"打架；
        2. `count = 0` 等于没声明，写不出效果的声明留在定义里只会骗人；
        3. `coverage` 落在版本历史上同理：它永不参与判定。
        """

        for section in self.sections:
            sid, role = str(section.section_id), section.role
            if section.min_entries is not None and role in ANCHOR_ROLES:
                raise BadTemplateDefinition(
                    f"{role.value} 锚点章不能声明 min_entries（它的判据由引擎算）",
                    template_id=self.template_id,
                    section_id=sid,
                )
            if section.coverage is not None and role is TemplateRole.VERSION_HISTORY:
                raise BadTemplateDefinition(
                    "版本历史章永不参与判定，不能声明 coverage",
                    template_id=self.template_id,
                    section_id=sid,
                )
            if section.min_entries is not None and section.min_entries.count < 1:
                raise BadTemplateDefinition(
                    f"{sid} 的 min_entries 必须 ≥ 1",
                    template_id=self.template_id,
                    section_id=sid,
                )
            if section.coverage is not None and section.coverage < 1:
                raise BadTemplateDefinition(
                    f"{sid} 的 coverage 必须 ≥ 1",
                    template_id=self.template_id,
                    section_id=sid,
                )

    @property
    def section_ids(self) -> tuple[SectionId, ...]:
        """章节顺序 = 文档顺序（`_validate` 的顺序闸读它）。"""

        return tuple(section.section_id for section in self.sections)

    def anchor(self, role: TemplateRole) -> SectionId:
        """取某锚点的章节 id。模板已 `validate` 过，这里不再二次校验。"""

        for section in self.sections:
            if section.role is role:
                return section.section_id
        raise BadTemplateDefinition(  # 防御：正常走不到（validate 已断过）
            f"模板没有 {role.value} 锚点", template_id=self.template_id, role=role.value
        )

    def slot(self, section_id: SectionId | str, name: str) -> SlotSpec | None:
        """某章的某槽（`_validate` 的槽闸读它，与 `section_slot` 同形）。"""

        for section in self.sections:
            if section.section_id == SectionId(section_id):
                for slot in section.slots:
                    if slot.name == name:
                        return slot
        return None


def _role(value: object, *, template_id: str) -> TemplateRole:
    try:
        return TemplateRole(str(value))
    except ValueError:
        raise BadTemplateDefinition(
            f"模板里有不认识的 role：{value}", template_id=template_id, role=value
        ) from None


def _slot(item: dict, *, template_id: str, section_id: str) -> SlotSpec:
    try:
        kind = BlockKind(str(item.get("kind")))
    except ValueError:
        raise BadTemplateDefinition(
            f"模板槽 kind 不认识：{item.get('kind')}",
            template_id=template_id,
            section_id=section_id,
        ) from None
    return SlotSpec(name=str(item.get("name")), title=str(item.get("title")), kind=kind)


def _min_entries(item: dict, *, template_id: str, section_id: str) -> MinEntries | None:
    raw = item.get("min_entries")
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool 是 int 的子类，别让 True 变成"至少 1 条"
        raise BadTemplateDefinition(
            f"{section_id} 的 min_entries 不是一个条数", template_id=template_id
        )
    if isinstance(raw, int):
        return MinEntries(count=raw)
    if not isinstance(raw, dict) or "count" not in raw:
        raise BadTemplateDefinition(
            f"{section_id} 的 min_entries 形状不认识（要整数或 {{count, kind}}）",
            template_id=template_id,
            section_id=section_id,
        )
    kind_raw = raw.get("kind")
    kind = None
    if kind_raw is not None:
        try:
            kind = BlockKind(str(kind_raw))
        except ValueError:
            raise BadTemplateDefinition(
                f"{section_id} 的 min_entries.kind 不认识：{kind_raw}",
                template_id=template_id,
                section_id=section_id,
            ) from None
    return MinEntries(count=int(raw["count"]), kind=kind)


def _coverage(item: dict, *, template_id: str, section_id: str) -> int | None:
    raw = item.get("coverage")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise BadTemplateDefinition(
            f"{section_id} 的 coverage 不是一个整数", template_id=template_id, section_id=section_id
        )
    return raw


def _section(item: dict, *, template_id: str) -> TemplatedSection:
    section_id = str(item.get("id"))
    slots = tuple(
        _slot(raw, template_id=template_id, section_id=section_id)
        for raw in item.get("slots", ())
    )
    return TemplatedSection(
        section_id=SectionId(section_id),
        title=str(item.get("title")),
        purpose=str(item.get("purpose")),
        role=_role(item.get("role", "plain"), template_id=template_id),
        slots=slots,
        min_entries=_min_entries(item, template_id=template_id, section_id=section_id),
        coverage=_coverage(item, template_id=template_id, section_id=section_id),
    )


def template_from_dict(data: dict) -> DocumentTemplate:
    """字典形状 → `DocumentTemplate`（含 `validate`）。

    TOML 解析完调它，API 建模板也调它——两种入口同一套校验，不各写一遍。
    住 domain：只用 domain 类型，application 可直接调（分层守卫不拦）。
    """

    template = DocumentTemplate(
        template_id=str(data.get("template_id", "")),
        title=str(data.get("title", "")),
        sections=tuple(
            _section(item, template_id=str(data.get("template_id", "")))
            for item in data.get("sections", ())
        ),
    )
    template.validate()
    return template


#: 标准模板的 id（创建缺省；老行回落即它）
STANDARD_TEMPLATE_ID = "standard"

#: 现行 13 章各章的引擎角色（切片 1 只标注，消费在切片 2；三锚点见 ANCHOR_ROLES）。
_ROLE_BY_SECTION_ID = {
    "s1": TemplateRole.BACKBONE,
    "s2": TemplateRole.BACKBONE,
    "s3": TemplateRole.BACKBONE,
    "s4": TemplateRole.BACKBONE,
    "s5": TemplateRole.FEATURE,
    "s6": TemplateRole.STEPS,
    "s7": TemplateRole.PLAIN,
    "s8": TemplateRole.PLAIN,
    "s9": TemplateRole.DECISION,
    "s10": TemplateRole.BACKBONE,
    "s11": TemplateRole.OPEN_QUESTIONS,
    "s12": TemplateRole.BACKBONE,
    "s13": TemplateRole.VERSION_HISTORY,
}


#: standard 六章的"至少 N 条"声明（`01-规格.md` §4 的判据表；住这里而不是就绪度里，
#: 因为它是**模板的事实**——适配器读它，缺了它就绪度就没有判据来源）。
#: s12 只数验收标准（写一条普通正文不算达标）。
_STANDARD_MIN_ENTRIES: dict[str, MinEntries] = {
    "s1": MinEntries(1),
    "s2": MinEntries(2),
    "s3": MinEntries(1),
    "s4": MinEntries(2),
    "s10": MinEntries(1),
    "s12": MinEntries(1, BlockKind.ACCEPTANCE),
}


def standard_template() -> DocumentTemplate:
    """现行 13 章的标准模板：与 `SECTIONS`/`SECTION_SLOTS` 同源算出，不复写第二份。

    s8 标 PLAIN 但槽照带——槽走 `TemplatedSection.slots`，不靠 role 表达
    （NFR 专属 role 现在加就是没人读的投机，等切片 2 真要 breaking s8 硬编码时再加）。
    """

    return DocumentTemplate(
        template_id=STANDARD_TEMPLATE_ID,
        title="标准版",
        sections=tuple(
            TemplatedSection(
                section_id=spec.section_id,
                title=spec.title,
                purpose=spec.purpose,
                role=_ROLE_BY_SECTION_ID[str(spec.section_id)],
                slots=SECTION_SLOTS.get(spec.section_id, ()),
                min_entries=_STANDARD_MIN_ENTRIES.get(str(spec.section_id)),
            )
            for spec in SECTIONS
        ),
    )
