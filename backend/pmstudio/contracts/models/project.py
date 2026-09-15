"""C16 Project（项目）。"""

from datetime import datetime

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.models.base import ContractModel


class ProjectConfig(ContractModel):
    """项目级配置。预算由 L6 自己算，这里是它读的项目侧输入。"""

    output_reserve_tokens: int = Field(ge=0)
    system_overhead_tokens: int = Field(ge=0)


class Project(ContractModel):
    """一个项目。记忆、检索、材料、文档都以它为隔离边界。"""

    project_id: str
    name: str
    template_id: str
    config: ProjectConfig
    created_at: datetime

    @model_validator(mode="after")
    def _check(self) -> "Project":
        if not self.project_id:
            raise ContractViolation("project_id 不能为空")
        if not self.name:
            raise ContractViolation("项目名不能为空")
        if not self.template_id:
            raise ContractViolation("template_id 不能为空——它决定文档的字段分区")
        return self
