"""C1 Scope（范围）。

范围的职责只有一件：**改哪儿**。读范围固定是全文，不在这里表达。
选区在轮内不变（I18），所以这个对象一旦随轮次建好，只读。
"""

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.models.base import ContractModel


class Scope(ContractModel):
    """一轮的写范围 + 开始读文档那一刻的版本快照。"""

    selected_fields: tuple[str, ...] = Field(default_factory=tuple)
    base_versions: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check(self) -> "Scope":
        seen: set[str] = set()
        for field in self.selected_fields:
            if not field.strip():
                raise ContractViolation("选中的字段名不能为空")
            if field in seen:
                raise ContractViolation(f"同一个字段被选了两次：{field}")
            seen.add(field)

        for block_id, version in self.base_versions.items():
            if not block_id:
                raise ContractViolation("版本快照里出现了空的 block_id")
            if version < 1:
                raise ContractViolation(f"块 {block_id} 的版本快照必须 ≥ 1")
        return self

    def covers(self, target_label: str) -> bool:
        """这个字段在不在写范围里。**空选区表示不限制**（I17）。"""
        return not self.selected_fields or target_label in self.selected_fields
