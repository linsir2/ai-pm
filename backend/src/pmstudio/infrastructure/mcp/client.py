"""MCP 客户端：连一个 MCP server，取它的工具，调它的工具。

**为什么用 SDK 的高层 `mcp.Client` 而不用裸 session**：它自己管
session 初始化与传输生命周期（`async with client:`），我们要做的只是
"拿到工具列表"与"调一次工具"。自己用 `ClientSession` + `stdio_client` 拼一遍
是把 SDK 已经做好的事重做一遍。

**失败怎么办**：`ToolCallFailed`（`tool_call_failed`，API 层映射 502）。
**不许降级**——server 连不上就说连不上，不许"假装调用过了"
（`AGENTS.md` 的"失败必须是失败"）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import NAME_SEPARATOR, ToolSpec
from pmstudio.infrastructure.mcp.projector import project_tools, source_label

logger = logging.getLogger(__name__)

#: 两种传输。**都是 MCP 协议规范定义的**，不是我们自造的：
#: - `stdio`：本地子进程（Claude Desktop / Cursor / VS Code 的 MCP 配置几乎全是这种）
#: - `http`：远程（Streamable HTTP）
TRANSPORT_STDIO = "stdio"
TRANSPORT_HTTP = "http"

#: 一次 `tools/call` 的墙钟上限（秒）。
#:
#: **为什么是 30**：工具调用比模型调用快一个量级（模型 p50 18.5s，工具通常百毫秒级），
#: 所以 30s 对工具是极宽松的。而**不能沿用模型的 150s**——那会让一个卡死的
#: server 占住 agentic 循环的一轮（上限 6 轮），最坏 15 分钟。
#:
#: 它最终传给 SDK 的 `call_tool(read_timeout_seconds=...)`——**不自己造超时机制**。
MCP_CALL_TIMEOUT = 30.0


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """一个 MCP server 的配置。**纯数据**，不 import SDK。"""

    name: str
    transport: str = TRANSPORT_STDIO
    #: stdio：可执行命令与参数
    command: str = ""
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    #: stdio：**子进程的工作目录**。缺省 = 继承本进程的 CWD。
    #:
    #: **为什么要有**：SDK 的 `StdioServerParameters.cwd` 留空时子进程继承
    #: **本进程**的 CWD。而配置示例里写的是相对路径（`scripts/mcp_demo_server.py`），
    #: 只有 CWD 恰好是 `backend/` 时才成立——从别处启动就找不到 server。
    #: 显式给一个 cwd（通常是 `backend/`）才不依赖调用者站在哪。
    cwd: str = ""
    #: http：server 地址
    url: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("MCP server 必须有名字：它是工具名的来源标识")
        if self.transport not in (TRANSPORT_STDIO, TRANSPORT_HTTP):
            raise ValueError(f"未知的 MCP 传输：{self.transport}")
        if self.transport == TRANSPORT_STDIO and not self.command.strip():
            raise ValueError(f"MCP server {self.name!r} 是 stdio 传输，必须给 command")
        if self.transport == TRANSPORT_HTTP and not self.url.strip():
            raise ValueError(f"MCP server {self.name!r} 是 http 传输，必须给 url")

    @property
    def source(self) -> str:
        """这个 server 的来源标识（`mcp__<name>`）。"""

        return source_label(self.name)


class McpClient:
    """一个 MCP server 的连接。"""

    def __init__(
        self,
        spec: McpServerSpec,
        *,
        client_factory: Callable[[McpServerSpec], Any] | None = None,
    ) -> None:
        self._spec = spec
        self._stack: AsyncExitStack | None = None
        self._client: Any | None = None
        self._tools: tuple[ToolSpec, ...] = ()
        #: 造 MCP 客户端的钩子。**默认按传输造真的**；测试注入一个进程内的
        #: 真 `mcp.Client(server_instance)`——**走的还是真协议**，
        #: 只是省掉起子进程。这是"注入口"，不是"假实现开关"
        #: （生产路径没有"用假 MCP"的分支，`test_no_fakes_in_production` 盯着）。
        self._client_factory = client_factory or _default_client

    @property
    def spec(self) -> McpServerSpec:
        return self._spec

    @property
    def tools(self) -> tuple[ToolSpec, ...]:
        """这个 server 提供的工具（投影后）。**没连上就是空**。"""

        return self._tools

    async def start(self) -> tuple[ToolSpec, ...]:
        """连上并取工具列表。**失败抛 `ToolCallFailed`**。

        **重复调用是安全的**：先把上一次的连接关掉再开新的。
        不这么做的话，"重启一次 manager" = 泄漏上一轮的全部 transport
        与 stdio 子进程（子进程还活着，stdin/stdout 挂着）。
        """

        # 先关掉旧的：**清理时的异常不许顶替真实错因**。
        await self.stop()

        stack = AsyncExitStack()
        client: Any
        try:
            client = self._client_factory(self._spec)
            await stack.enter_async_context(client)
            listed = await client.list_tools()
        except Exception as error:  # noqa: BLE001 —— SDK 抛什么我们都得转译
            await _close_quietly(stack)
            raise ToolCallFailed(
                f"连不上 MCP server {self._spec.name!r}：{error}",
                tool_id=self._spec.source,
            ) from error

        self._stack = stack
        self._client = client
        self._tools = project_tools(listed.tools, source=self._spec.source)
        logger.info(
            "MCP server %s 就绪：%d 个工具", self._spec.name, len(self._tools)
        )
        return self._tools

    async def call(self, tool_name: str, arguments: Mapping[str, object]) -> str:
        """调一次工具，返回**给模型看**的文本。"""

        if self._client is None:
            raise ToolCallFailed(
                f"MCP server {self._spec.name!r} 还没连上",
                tool_id=self._spec.source,
            )
        try:
            result = await self._client.call_tool(
                tool_name,
                dict(arguments),
                read_timeout_seconds=MCP_CALL_TIMEOUT,
            )
        except ToolCallFailed:
            raise
        except Exception as error:  # noqa: BLE001
            raise ToolCallFailed(
                f"MCP 工具 {tool_name} 调用失败：{error}",
                # **带来源**：两个 server 有同名工具时，光看裸名字分不出是谁的。
                tool_id=self._spec.source + NAME_SEPARATOR + tool_name,
            ) from error

        if getattr(result, "is_error", False):
            raise ToolCallFailed(
                f"MCP 工具 {tool_name} 返回了错误：{_text_of(result)}",
                tool_id=self._spec.source + NAME_SEPARATOR + tool_name,
            )
        return _text_of(result)

    async def stop(self) -> None:
        """关掉连接。**幂等**——没连上直接返回。"""

        stack = self._stack
        self._stack = None
        self._client = None
        self._tools = ()
        if stack is not None:
            await _close_quietly(stack)


async def _close_quietly(stack: AsyncExitStack) -> None:
    """关掉一个 exit stack，**失败只记 warning**。

    **为什么吞掉清理异常**：清理发生在两个地方——`start()` 的失败路径
    （此时正要抛 `ToolCallFailed`）与 `stop()`。若让 `aclose()` 的异常冒出去，
    它会**顶替掉**原本那个真实错因（"连不上 server X"变成"关闭时出错"），
    排查方向直接跑偏。清理失败本身也确实不值得盖过真实问题。
    """

    try:
        await stack.aclose()
    except Exception as error:  # noqa: BLE001
        logger.warning("关闭 MCP 连接时出错（不影响主流程）：%s", error)


def _default_client(spec: McpServerSpec) -> Any:
    """按传输造一个真的 MCP 客户端。"""

    from mcp import Client

    if spec.transport == TRANSPORT_STDIO:
        from mcp.client.stdio import StdioServerParameters

        return Client(
            StdioServerParameters(
                command=spec.command,
                args=list(spec.args),
                env=dict(spec.env) or None,
                cwd=spec.cwd or None,
            )
        )
    return Client(spec.url)


def _text_of(result: object) -> str:
    """从 `CallToolResult` 里取文本。

    `content` 是内容块的列表（文本/图片/…）。我们只取文本——
    **图片给模型看**要走多模态那条路，本项目还没有那条路，
    所以这里照实说"有一块不是文本"，而不是假装它是文本。

    **`structured_content` 不能丢**（MCP 批次实测）：2026-07 的协议允许 server
    **只**回结构化内容（`content` 为空、`structured_content` 有值）。
    只读 `content` 会让模型收到一个**空串**——那既不是失败（`is_error` 为假、
    不抛错）也不是内容，是最典型的"把失败伪装成正常结果"。
    所以：有结构化内容就把它序列化成 JSON 回灌。
    """
    chunks: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
        else:
            chunks.append(f"[{type(block).__name__}，本项目暂不处理非文本内容]")

    structured = getattr(result, "structured_content", None)
    if structured is not None and not chunks:
        try:
            chunks.append(json.dumps(structured, ensure_ascii=False))
        except (TypeError, ValueError):
            chunks.append(str(structured))
    return "\n".join(chunks)


__all__ = [
    "MCP_CALL_TIMEOUT",
    "TRANSPORT_HTTP",
    "TRANSPORT_STDIO",
    "McpClient",
    "McpServerSpec",
]
