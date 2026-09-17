"""建项目 —— L5 的应用函数（**不是模块、不是能力工具**，见 directory.md §3.1).

一次事务里做完三件事：建项目、按模板实例化文档、把 9 个字段建成分区块（C16 / §5.3 `createProject`）。
模板是"填哪里"的 schema，归 L0 注册中心；这里只按它实例化，不自己带字段表。

两条容易被"顺手加"的东西，这里都不做：

- **不产生文档版本**：C3 的 `trigger` 只有 `manual` / `submit` / `rollback`，没有"创建"。
- **不发事件**：`doc.changed` 的语义是"文档变了"（AI 写入 / 手改 / 回退），建出来的空骨架不是其中任何一种。
"""

from pmstudio.common.clock import Clock
from pmstudio.common.ids import BLOCK, DOCUMENT, PROJECT, IdGenerator
from pmstudio.contracts.enums import RegistryKind
from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.models.document import Block, Document
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.contracts.models.registry import TemplateBody
from pmstudio.contracts.models.results import CreateProjectResult
from pmstudio.storage.db import Database
from pmstudio.storage.ledger import Ledger

# 预算三个数字的正经出处是模型元数据表（directory.md §11 #6，落在 R1）。在那之前用这套默认值——
# 写在这里是为了让它显眼：谁读到这两个数字，都该知道它们还没有来源（DECISIONS §18 记着这笔账）。
DEFAULT_OUTPUT_RESERVE_TOKENS = 4096
DEFAULT_SYSTEM_OVERHEAD_TOKENS = 512


class ProjectService:
    """`createProject` 的实现。"""

    def __init__(
        self,
        registry: RegistryPort,
        ledger: Ledger,
        db: Database,
        ids: IdGenerator,
        clock: Clock,
    ) -> None:
        self._registry = registry
        self._ledger = ledger
        self._db = db
        self._ids = ids
        self._clock = clock

    async def create_project(self, template_id: str, name: str) -> CreateProjectResult:
        """建项目 + 按模板实例化空骨架，一次事务。"""
        entry = await self._registry.resolve(RegistryKind.TEMPLATE, template_id)
        template = TemplateBody.model_validate(entry.content)

        with self._db.transaction():  # 一次事务：项目、文档、块要么全在，要么全不在（I9）
            project_id = self._ids.new_id(PROJECT)
            doc_id = self._ids.new_id(DOCUMENT)
            self._ledger.create_project(
                Project(
                    project_id=project_id,
                    name=name,
                    template_id=template_id,
                    config=ProjectConfig(
                        output_reserve_tokens=DEFAULT_OUTPUT_RESERVE_TOKENS,
                        system_overhead_tokens=DEFAULT_SYSTEM_OVERHEAD_TOKENS,
                    ),
                    created_at=self._clock.now(),
                )
            )
            self._ledger.create_document(
                Document(doc_id=doc_id, project_id=project_id, template_id=template_id),
                [
                    Block(block_id=self._ids.new_id(BLOCK), schema_label=field.label)
                    for field in template.fields
                ],
            )
        return CreateProjectResult(project_id=project_id, doc_id=doc_id)
