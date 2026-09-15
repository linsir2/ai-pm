"""L5 工具层对外暴露什么。

**能力工具和应用函数分成两个 Protocol**，因为约束完全不同：

- `CapabilityToolsPort`：进注册中心、要 description / tags / when_to_use、受 M23 权限管、AI 能自己挑
- `AppServicesPort`：流程里固定的一步，不进注册中心、不受权限管

`export`（M21）**故意不在里面**：文档只写了它的返回是"交付物"，没有形状；
声明它就得凭空发明一个返回类型。等 M21 真做时再定（PRD §14 把它列为范围外）。
"""

from typing import Protocol, runtime_checkable

from pmstudio.contracts.enums import VersionTrigger
from pmstudio.contracts.models.document import WritePayload
from pmstudio.contracts.models.material import MaterialPacket
from pmstudio.contracts.models.results import CreateProjectResult, WriteDocumentResult


@runtime_checkable
class CapabilityToolsPort(Protocol):
    """给 AI 用的能力工具。"""

    async def search_web(self, query: str) -> tuple[MaterialPacket, ...]:
        """M18 联网搜索，产出 `source_type = web` 的材料包。"""
        ...

    async def ingest(self, path: str) -> str:
        """M19 上传解析：文件进资料库，返回 `material_id`。**解析不等于来源**。"""
        ...


@runtime_checkable
class AppServicesPort(Protocol):
    """流程里固定的一步。"""

    async def create_project(self, template_id: str, name: str) -> CreateProjectResult:
        """建项目 + 按模板实例化空骨架，一次事务（C16）。"""
        ...

    async def write_document(self, trigger: VersionTrigger, payload: WritePayload) -> WriteDocumentResult:
        """M20 文档写入：AI 写入、手改、回退三种来源共用这一个入口（I1）。"""
        ...
