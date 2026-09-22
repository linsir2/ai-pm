"""功能子树字段表：结构的唯一真相源（字段名 / 块类型 / 多值 / 必填）。

字段清单来自 `.codex/docs/设计/01-规格.md` §4；必填标记来自同节的合格标准
（"每个功能有验收标准、异常与边界非空、能关联目标与角色"）。
要改字段，只改这一张表。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import UnknownFeatureField
from pmstudio.domain.shared.writing import BlockKind


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    title: str
    kind: BlockKind
    multiple: bool = False
    required: bool = False


FEATURE_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("name", "名称", BlockKind.TEXT, required=True),
    FieldSpec("one_liner", "一句话描述", BlockKind.TEXT, required=True),
    FieldSpec("priority", "优先级", BlockKind.TEXT),
    FieldSpec("goal", "服务的目标", BlockKind.BULLET_LIST, multiple=True, required=True),
    FieldSpec("audience", "面向角色", BlockKind.BULLET_LIST, multiple=True, required=True),
    FieldSpec("user_story", "用户故事", BlockKind.TEXT),
    FieldSpec("main_flow", "主流程", BlockKind.STEPS, multiple=True),
    FieldSpec("exceptions", "异常与边界", BlockKind.BULLET_LIST, multiple=True, required=True),
    FieldSpec("io", "输入与输出", BlockKind.BULLET_LIST, multiple=True),
    FieldSpec("rules", "状态与规则", BlockKind.BULLET_LIST, multiple=True),
    FieldSpec("acceptance", "验收标准", BlockKind.ACCEPTANCE, multiple=True, required=True),
    FieldSpec("dependencies", "依赖", BlockKind.BULLET_LIST, multiple=True),
)

FEATURE_FIELD_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in FEATURE_FIELDS}
REQUIRED_FEATURE_FIELDS: tuple[FieldSpec, ...] = tuple(
    spec for spec in FEATURE_FIELDS if spec.required
)


def field_spec(name: str) -> FieldSpec:
    spec = FEATURE_FIELD_BY_NAME.get(name)
    if spec is None:
        raise UnknownFeatureField(name)
    return spec
