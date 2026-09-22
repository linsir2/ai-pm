"""Target：内容要落到哪儿。它同时是写范围的判定单位。"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import (
    FeatureNotAllowedHere,
    TargetShapeError,
    UnknownSection,
)
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.label import Zone
from pmstudio.domain.shared.sections import FEATURE_HOME, SECTION_BY_ID


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
                raise TargetShapeError("只有功能字段才带 field 名")
            if not self.field.strip():
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
