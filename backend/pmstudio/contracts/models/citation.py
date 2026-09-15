"""C15 Citation（引用）。

每一句 AI 说的话都要能回答"依据什么"（AC3）。形状只在这里定义一次，
被 C4 主张、C7 提案与备选、C9 记忆、C11 主张留痕共用。

`claim` 这一档只在编排层内部流转的对象及其留痕里出现（C4 与 C11）；
C7 / C9 里出现 `claim` 引用是非法的，见 `invariants.check_citation_targets`。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import CitationTargetType
from pmstudio.contracts.models.base import ContractModel


class Citation(ContractModel):
    """一条引用：指到哪个对象、哪个版本。"""

    target_type: CitationTargetType
    target_id: str
    target_version: int | None = None
    quote: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "Citation":
        if not self.target_id:
            raise ContractViolation("引用的 target_id 不能为空——指不到东西的引用不算依据（I21）")

        if self.target_type is CitationTargetType.BLOCK:
            if self.target_version is None:
                raise ContractViolation("引用文档块必须带 target_version——记忆失效判定（I6）靠它")
            if self.target_version < 1:
                raise ContractViolation("target_version 必须 ≥ 1")
        elif self.target_version is not None:
            raise ContractViolation("target_version 只有引用 block 时才有")
        return self
