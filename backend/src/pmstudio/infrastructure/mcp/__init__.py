"""MCP 客户端：把外部 MCP server 提供的工具接进本项目的工具层。

**为什么 SDK 只出现在这个包里**：`harness` 是最内层，只许依赖
`pydantic` / `litellm` / `pybreaker`（`tests/architecture/test_harness_imports.py`）。
所以 `harness/tools/invoker.py` 只声明一个 **Protocol**，
真正跟 MCP 说话的实现落在这里。

**它做什么**：把 MCP server 的工具列表投影成 `harness` 的 `ToolSpec`，
并按名字路由执行。**它不做什么**：不碰文档、不碰轮次、不写库——
MCP 是**只读**的旁路能力（`04-未决.md` 的裁决：本轮只做读侧）。
"""

from pmstudio.infrastructure.mcp.client import McpClient, McpServerSpec
from pmstudio.infrastructure.mcp.manager import McpToolManager
from pmstudio.infrastructure.mcp.projector import project_tools

__all__ = ["McpClient", "McpServerSpec", "McpToolManager", "project_tools"]
