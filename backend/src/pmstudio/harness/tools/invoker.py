"""工具执行协议：harness 侧"怎么执行一次工具调用"的**形状**。

**为什么是 Protocol 而不是实现**：`harness` 是最内层，只许依赖
`pydantic` / `litellm` / `pybreaker`（`tests/architecture/test_harness_imports.py`）——
**MCP SDK 落不进这里**。所以 harness 只声明"我要一个能执行工具的东西"，
具体是谁（MCP 客户端 / 本地实现 / 测试替身）由 `bootstrap` 注入。

这与本仓已有的 `ModelGateway` / `PromptLibrary` / `ModelCallRecorder`
**是同一套做法**——不是新发明的模式，是复用。

**失败怎么表达**：执行失败抛 `harness.errors.ToolCallFailed`
（`tool_call_failed`，API 层映射 502）。它与 `ModelCallFailed` 分开是有意的：
工具失败**不判死整轮**（留痕继续跑），模型失败才走重试那条路。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pmstudio.harness.tools.spec import ToolCall, ToolSpec


@runtime_checkable
class ToolInvoker(Protocol):
    """执行一次工具调用。"""

    async def invoke(self, spec: ToolSpec, call: ToolCall) -> str:
        """执行 `call` 指向的工具，返回**给模型看**的内容（纯文本）。

        失败时抛 `pmstudio.harness.errors.ToolCallFailed`——
        **不许返回一句"失败了"当正常结果**：那样模型会拿它当工具输出继续推理，
        把它当成事实（这正是本项目"失败必须是失败"要防的那类静默降级）。
        """
        ...
