"""Target：内容要落到哪儿。它同时是写范围的判定单位。"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import (
    DomainError,
    FeatureNotAllowedHere,
    TargetShapeError,
    UnknownSection,
)
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.label import Zone
from pmstudio.domain.shared.sections import FEATURE_HOME, SECTION_BY_ID, section_slot


@dataclass(frozen=True, slots=True)
class Target:
    zone: Zone = Zone.BODY
    section_id: SectionId | None = None
    feature_slug: FeatureSlug | None = None
    field: str | None = None

    def __post_init__(self) -> None:
        if self.zone is Zone.PREFERENCE:
            has_body_parts = any(
                part is not None for part in (self.section_id, self.feature_slug, self.field)
            )
            if has_body_parts:
                raise TargetShapeError("偏好区 target 不带章节、功能与字段")
            return
        if self.section_id is None:
            raise TargetShapeError("正文 target 必须给 section_id")
        if self.section_id not in SECTION_BY_ID:
            raise UnknownSection(self.section_id)
        if self.feature_slug is not None:
            if self.section_id != FEATURE_HOME:
                raise FeatureNotAllowedHere(
                    section_id=self.section_id, feature_slug=self.feature_slug
                )
            if not str(self.feature_slug).strip():
                raise TargetShapeError("feature_slug 不能为空")
        if self.field is not None:
            if self.feature_slug is None:
                # 没有 feature_slug 时，field 只能取该章的章节槽（如 s8 的 performance）
                if section_slot(self.section_id, self.field) is None:
                    raise TargetShapeError(
                        f"{self.section_id} 没有名为 {self.field} 的章节槽"
                    )
            elif not self.field.strip():
                raise TargetShapeError("field 名不能为空")

    @classmethod
    def body(
        cls,
        section_id: SectionId | str,
        *,
        feature_slug: FeatureSlug | str | None = None,
        field: str | None = None,
    ) -> "Target":
        return cls(
            zone=Zone.BODY,
            section_id=SectionId(section_id),
            feature_slug=FeatureSlug(feature_slug) if feature_slug is not None else None,
            field=field,
        )

    @classmethod
    def feature_field(cls, feature_slug: FeatureSlug | str, field: str) -> "Target":
        return cls.body(FEATURE_HOME, feature_slug=feature_slug, field=field)

    @classmethod
    def preference(cls) -> "Target":
        return cls(zone=Zone.PREFERENCE)

    def is_within(self, scope: "Target") -> bool:
        """self 是否落在 scope 这棵子树里。用于判断"跑出本轮去处"。"""

        if self.zone is not scope.zone:
            return False
        if self.zone is Zone.PREFERENCE:
            return True
        if self.section_id != scope.section_id:
            return False
        if scope.feature_slug is not None and self.feature_slug != scope.feature_slug:
            return False
        if scope.field is not None and self.field != scope.field:
            return False
        return True

    def label(self) -> str:
        if self.zone is Zone.PREFERENCE:
            return "preference"
        text = str(self.section_id)
        if self.feature_slug is not None:
            text = f"{text}/{self.feature_slug}"
        if self.field is not None:
            text = f"{text}.{self.field}"
        return text


def target_from_label(label: str) -> Target | None:
    """`Target.label()` 的反函数（2026-09-29 P11 C1）。

    为什么放在领域里：`label()` 的形态是**领域词汇表**的一部分（卡片、提示词、导出的 s11
    条目都用它），反函数跟它住一起才不会两边漂。

    两件事分得很清：
    - **形状不认识**（章节不存在、`s5/` 这种缺一半、空串）→ 返回 `None`，调用方回落 `s11`，绝不猜；
    - **字段名对不对**是**内容层**的事（`field_spec` 管），这里不替它判死。
    """

    text = label.strip()
    if not text:
        return None
    if text == "preference":
        return Target.preference()
    head, dot, field = text.partition(".")
    if dot and not field:
        return None  # 写了点却没写字段名（`s5/order-export.`）——形状不完整，不猜
    section_id, slash, slug = head.partition("/")
    if not section_id or (slash and not slug):
        return None
    try:
        return Target.body(section_id, feature_slug=slug or None, field=field or None)
    except DomainError:
        return None
