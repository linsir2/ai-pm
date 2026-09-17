"""C10 Registry Entry（注册条目）。

注册中心把所有**定义类**的东西集中管理：角色 / prompt / 工具 / skill / 模板 / 模型 / 编排策略。
两种东西要求不一样：**工具和 skill 要让 AI 自己挑**，所以必须有 `description` / `tags` /
`when_to_use`；角色、prompt、模板这些只是解耦出来统一管理，有名字和本体就够了。
"""

from pydantic import Field, JsonValue, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import RegistryKind, RegistryOwner, RegistryStatus, RoundEntry
from pmstudio.contracts.models.base import ContractModel

# 只有这两种条目是"要给 AI 挑"的（I13）。
KINDS_REQUIRING_DESCRIPTION = frozenset({RegistryKind.TOOL, RegistryKind.SKILL})


class RegistryEntry(ContractModel):
    """一条定义。权限的**声明**在这里，权限的**执行**在 L6（M23）。"""

    id: str
    kind: RegistryKind
    name: str
    content: JsonValue
    owner: RegistryOwner
    description: str | None = None
    tags: tuple[str, ...] = Field(default_factory=tuple)
    when_to_use: str | None = None
    status: RegistryStatus = RegistryStatus.ACTIVE
    allowed_tools: tuple[str, ...] | None = None
    allowed_entries: tuple[RoundEntry, ...] | None = None

    @model_validator(mode="after")
    def _check(self) -> "RegistryEntry":
        if not self.id:
            raise ContractViolation("注册条目的 id 不能为空")
        if not self.name.strip():
            raise ContractViolation("注册条目必须有名字")
        self._check_content()
        self._check_tags()

        if self.kind in KINDS_REQUIRING_DESCRIPTION:
            if not (self.description or "").strip():
                raise ContractViolation(f"{self.kind.value} 没有 description，AI 就不知道它是什么（I13）")
            if not self.tags:
                raise ContractViolation(f"{self.kind.value} 没有 tags，AI 检索不到它（I13）")
            if not (self.when_to_use or "").strip():
                raise ContractViolation(f"{self.kind.value} 没有 when_to_use，AI 没法自己挑（I13）")

        if self.kind is not RegistryKind.ROLE:
            if self.allowed_tools is not None or self.allowed_entries is not None:
                raise ContractViolation("allowed_tools / allowed_entries 只对角色有意义")
        else:
            self._check_allowlist(self.allowed_tools, "allowed_tools")
        return self

    def _check_content(self) -> None:
        if isinstance(self.content, str):
            if not self.content.strip():
                raise ContractViolation("注册条目的 content 不能是空字符串")
        elif isinstance(self.content, dict) and not self.content:
            raise ContractViolation("注册条目的 content 不能是空对象")

    def _check_tags(self) -> None:
        seen: set[str] = set()
        for tag in self.tags:
            if not tag.strip():
                raise ContractViolation("tags 里不能有空标签")
            if tag in seen:
                raise ContractViolation(f"同一个标签写了两遍：{tag}")
            seen.add(tag)

    @staticmethod
    def _check_allowlist(ids: tuple[str, ...] | None, name: str) -> None:
        for value in ids or ():
            if not value:
                raise ContractViolation(f"{name} 里不能有空 id")


class TemplateField(ContractModel):
    """模板里的一个字段分区（C10 `kind = template` 的 `content` 项）。"""

    label: str
    required: bool

    @model_validator(mode="after")
    def _check(self) -> "TemplateField":
        if not self.label.strip():
            raise ContractViolation("模板字段名不能为空——块 label 全是 M20 的定位依据")
        return self


class TemplateBody(ContractModel):
    """模板的本体：一份**有序**的字段清单（C10 `kind = template` 的 `content`）。

    只放 `label` + `required` 两样，理由各是"说得出消费者"：
    `required` 的消费者是 M28——必填字段缺内容时该产补信息卡而不是编一个（PRD 附录 B）。
    「依赖与边界设为常驻上下文」的消费者是 M13（R4）、字段之间的硬依赖提示的消费者是
    M2 / M28（R1.2+）——它们真被读的时候再加，现在加就是没有消费者的字段。
    """

    fields: tuple[TemplateField, ...]

    @model_validator(mode="after")
    def _check(self) -> "TemplateBody":
        if not self.fields:
            raise ContractViolation("模板至少要有一个字段——空模板建出来的文档没有分区")
        seen: set[str] = set()
        for field in self.fields:
            if field.label in seen:
                raise ContractViolation(
                    f"模板里有重复的字段名：{field.label}——块 label 必须唯一（I22）"
                )
            seen.add(field.label)
        return self
