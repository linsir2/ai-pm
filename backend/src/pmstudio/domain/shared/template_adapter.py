"""模板适配器：引擎对模板的**唯一**解读入口（`02` §11.0.69）。

分层：住 `domain/shared/`（与 `sections.py` 同理——就绪度/汇编/导出/提示词四个上下文
都要用），只用 domain 类型，零外部 import。

它是什么：一层**纯解读**。模板库只声明"有哪些章、什么角色、有哪些槽、判据多少"，
不知道阻塞型章节是哪几章、导出第几章写几、覆盖度算几——那是**引擎**的概念。
两边的缝就是 `adapt(document.template)` 这一句；`grep -rn "adapt(" src` 的结果
就是本引擎全部的模板接缝点。

它不是什么：不是第二个真相源。它不缓存、不预计算、不改状态——每次调用都是对
行内快照（`Document.template`）的一次 derive。快照不可变，所以没有失效问题。

判据的缺省规则（standard 逐字复刻是它的验收标准，见测试第 1 组）：
- `coverage_minimum`：声明值 > 有槽则槽数 > 骨架章取其条数下限（无声明则 1）> 其余 2；
- `min_entries_for`：只认声明；骨架章无声明回退"至少 1 条"（骨架章空着就是缺）；
- `blocking_sections`：功能锚点 ∪ 有条数判据的章 ∪ 有槽的章；
- `only_kind_for`：步骤章 → `steps`（整章唯一形态），其余 None。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import (
    BadTemplateDefinition,
    FeatureNotAllowedHere,
    TargetShapeError,
    UnknownSection,
)
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.sections import SlotSpec
from pmstudio.domain.shared.template import (
    DocumentTemplate,
    MinEntries,
    TemplatedSection,
    TemplateRole,
)
from pmstudio.domain.shared.writing import BlockKind

#: 两种"有内容就该有某种块"的角色与人话标签（就绪度 warn 用；随模板角色走，
#: 不再住在就绪度模块的 id-keyed 表里）。
ROLE_PRESENCE_LABEL: dict[TemplateRole, str] = {
    TemplateRole.STEPS: "主流程",
    TemplateRole.DECISION: "技术决策",
}

#: 骨架章没声明时按"至少 1 条"算：骨架章空着就是缺（自定义模板里作者忘了写也得算出来）。
_BACKBONE_DEFAULT_MIN = MinEntries(count=1)

#: 非骨架、非功能、非步骤、非决策、非版本历史的章，覆盖度一律按 2 条算
#: （沿用提示词里的启发值：一条多半是残句，两条才算写过）。
_PLAIN_DEFAULT_COVERAGE = 2


@dataclass(frozen=True, slots=True)
class TemplateAdapter:
    """把一套模板翻译成引擎要的那些答案。纯 derive，无状态。"""

    template: DocumentTemplate

    # —— 章节事实 ——

    @property
    def section_ids(self) -> tuple[SectionId, ...]:
        return self.template.section_ids

    @property
    def feature_home(self) -> SectionId:
        """功能子树的家（每套模板恰一，`DocumentTemplate.validate` 断过）。"""

        return self.template.anchor(TemplateRole.FEATURE)

    @property
    def open_home(self) -> SectionId:
        """未决清单的家：未交代的疑问、降级的题、跳过的题都落这儿。"""

        return self.template.anchor(TemplateRole.OPEN_QUESTIONS)

    @property
    def version_home(self) -> SectionId:
        """版本历史章：导出时由版本表生成，不判、不手写。"""

        return self.template.anchor(TemplateRole.VERSION_HISTORY)

    def has(self, section_id: SectionId | str) -> bool:
        return SectionId(section_id) in self.template.section_ids

    def section(self, section_id: SectionId | str) -> TemplatedSection:
        wanted = SectionId(section_id)
        for item in self.template.sections:
            if item.section_id == wanted:
                return item
        raise UnknownSection(section_id, template_title=self.template.title)

    def title_of(self, section_id: SectionId | str) -> str:
        return self.section(section_id).title

    def purpose_of(self, section_id: SectionId | str) -> str:
        return self.section(section_id).purpose

    def role_of(self, section_id: SectionId | str) -> TemplateRole | None:
        """这一章的角色；模板里没有这一章时 None（调用方自己决定是跳过还是报错）。"""

        try:
            return self.section(section_id).role
        except UnknownSection:
            return None

    def number_of(self, section_id: SectionId | str) -> int:
        """章节序号（从 1 起，按模板顺序）：导出章号、功能号、"第 N 章"都走它。

        不能从 id 推（`lstrip("s")` 那套只对 sN 有效，自定义模板的 id 是自由文本）。
        """

        wanted = SectionId(section_id)
        for index, item in enumerate(self.template.sections, start=1):
            if item.section_id == wanted:
                return index
        raise UnknownSection(section_id, template_title=self.template.title)

    def slots_of(self, section_id: SectionId | str) -> tuple[SlotSpec, ...]:
        try:
            return self.section(section_id).slots
        except UnknownSection:
            return ()

    def slot(self, section_id: SectionId | str, name: str) -> SlotSpec | None:
        return self.template.slot(section_id, name)

    def steps_section(self) -> SectionId | None:
        """整章按步骤写的那一章（s6 在 standard 里）；模板没有则 None。"""

        return self._first_with_role(TemplateRole.STEPS)

    def decision_section(self) -> SectionId | None:
        """整章写关键决策的那一章（s9 在 standard 里）。"""

        return self._first_with_role(TemplateRole.DECISION)

    def data_section(self) -> SectionId | None:
        """数据与接口类章节（standard 的 s7）：跨章链接启发式的落点。

        模板没标 `role = "data"` 就给 None——调用方据此**整条不跑**，而不是退到别的章上
        （启发式判据宁可没有，也不能指错地方）。
        """

        return self._first_with_role(TemplateRole.DATA)

    def only_kind_for(self, section_id: SectionId | str) -> BlockKind | None:
        """这一章的内容只有一种块类型时给那个类型（模型写歪或没写都按它落）。"""

        if self.role_of(section_id) is TemplateRole.STEPS:
            return BlockKind.STEPS
        return None

    def _first_with_role(self, role: TemplateRole) -> SectionId | None:
        for item in self.template.sections:
            if item.role is role:
                return item.section_id
        return None

    # —— 判据 ——

    def min_entries_for(self, section_id: SectionId | str) -> MinEntries | None:
        """这一章"至少几条非空条目"的判据；不按条数判的章给 None。"""

        try:
            section = self.section(section_id)
        except UnknownSection:
            return None
        if section.min_entries is not None:
            return section.min_entries
        if section.role is TemplateRole.BACKBONE:
            return _BACKBONE_DEFAULT_MIN
        return None

    def required_kind_for(self, section_id: SectionId | str) -> BlockKind | None:
        """条数判据只数哪种块（s12 只数验收标准）；不挑种类给 None。"""

        declared = self.min_entries_for(section_id)
        return None if declared is None else declared.kind

    def coverage_minimum(self, section_id: SectionId | str) -> int:
        """上下文覆盖度的下限（"这章写满没有"）：与就绪度是两套东西，别混。

        顺序：声明值 > 有槽则槽数 > 骨架章取条数下限 > 其余 2。
        版本历史永不参与判定，调它就炸（模板定义时也拦了，见 `DocumentTemplate`）。
        """

        section = self.section(section_id)
        if section.role is TemplateRole.VERSION_HISTORY:
            raise BadTemplateDefinition(
                "版本历史章不参与覆盖度判定",
                template_id=self.template.template_id,
                section_id=str(section_id),
            )
        if section.coverage is not None:
            return section.coverage
        if section.slots:
            return len(section.slots)
        if section.role is TemplateRole.BACKBONE:
            declared = self.min_entries_for(section_id)
            return _BACKBONE_DEFAULT_MIN.count if declared is None else declared.count
        return _PLAIN_DEFAULT_COVERAGE

    def blocking_sections(self) -> frozenset[str]:
        """"不写就没有交付物"的章节集合（问这类题的默认值）。

        三条来源：功能锚点（有功能子树这件事本身就是阻塞项）、有条数判据的章、
        有槽的章（槽是骨架，缺项等于没表态）。
        """

        out = {str(self.feature_home)}
        for section in self.template.sections:
            if section.min_entries is not None or section.slots:
                out.add(str(section.section_id))
        return frozenset(out)

    def backbone(self) -> tuple[tuple[SectionId, str, str, MinEntries], ...]:
        """骨架章（章节级判据的候选）：`(id, 标题, 这一章是干什么的, 条数判据)`。

        返回 purpose 是给草案卡的告警句用——告警要能说"随手补一条算什么"，
        硬写一句通用的话对不上具体是哪一章。
        """

        rows: list[tuple[SectionId, str, str, MinEntries]] = []
        for section in self.template.sections:
            minimum = self.min_entries_for(section.section_id)
            if section.role is not TemplateRole.BACKBONE or minimum is None:
                continue
            rows.append((section.section_id, section.title, section.purpose, minimum))
        return tuple(rows)

    # —— 去处校验 ——

    def check_target(
        self,
        section_id: SectionId | str,
        feature_slug: FeatureSlug | str | None = None,
        field: str | None = None,
    ) -> None:
        """去处的成员校验三闸：章在不在模板里、功能挂不挂得住、槽有没有这个。

        形状（正文必须给章 / 偏好区不带部件 / 字段名非空）由 `Target.__post_init__`
        管；这里只管"这一套模板认不认"——两件事分开，错误消息才不会互相盖。
        """

        wanted = SectionId(section_id)
        if not self.has(wanted):
            raise UnknownSection(section_id, template_title=self.template.title)
        if feature_slug is not None:
            if wanted != self.feature_home:
                raise FeatureNotAllowedHere(
                    section_id=section_id,
                    feature_slug=feature_slug,
                    feature_home=self.feature_home,
                    template_title=self.template.title,
                )
            if not str(feature_slug).strip():
                raise TargetShapeError("feature_slug 不能为空")
        if field is not None and feature_slug is None:
            if self.slot(wanted, field) is None:
                raise TargetShapeError(
                    f"{section_id} 没有名为 {field} 的章节槽（模板「{self.template.title}」）"
                )
        if field is not None and feature_slug is not None and not field.strip():
            raise TargetShapeError("field 名不能为空")


def adapt(template: DocumentTemplate) -> TemplateAdapter:
    """接缝动作：把模板翻译成适配器。**引擎读模板的唯一入口**（`02` §11.0.69）。"""

    return TemplateAdapter(template)