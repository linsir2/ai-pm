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

import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import ToolSpec
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
        """连上并取工具列表。**失败抛 `ToolCallFailed`**。"""

        stack = AsyncExitStack()
        try:
            client = self._client_factory(self._spec)
            await stack.enter_async_context(client)
            listed = await client.list_tools()
        except Exception as error:  # noqa: BLE001 —— SDK 抛什么我们都得转译
            await stack.aclose()
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
                tool_id=tool_name,
            ) from error

        if getattr(result, "is_error", False):
            raise ToolCallFailed(
                f"MCP 工具 {tool_name} 返回了错误：{_text_of(result)}",
                tool_id=tool_name,
            )
        return _text_of(result)

    async def stop(self) -> None:
        """关掉连接。**幂等**——没连上直接返回。"""

        stack = self._stack
        self._stack = None
        self._client = None
        self._tools = ()
        if stack is not None:
            await stack.aclose()


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
            )
        )
    return Client(spec.url)


def _text_of(result: object) -> str:
    """从 `CallToolResult` 里取文本。

    `content` 是内容块的列表（文本/图片/…）。我们只取文本——
    **图片给模型看**要走多模态那条路，本项目还没有那条路，
    所以这里照实说"有一块不是文本"，而不是假装它是文本。
    """
    chunks: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            chunks.append(text)
        else:
            chunks.append(f"[{type(block).__name__}，本项目暂不处理非文本内容]")
    return "\n".join(chunks)


def tool_name_for(source: str, tool_name: str) -> str:
    """`mcp__<server>__<tool>` 里的工具名部分还原。"""

    prefix = f"{source}__"
    if tool_name.startswith(prefix):
        return tool_name[len(prefix) :]
    return tool_name


def specs_of(clients: Sequence[McpClient]) -> tuple[ToolSpec, ...]:
    """把多个 client 的工具合成一份（去重按名字，**先来的赢**）。"""

    seen: dict[str, ToolSpec] = {}
    for client in clients:
        for spec in client.tools:
            seen.setdefault(spec.name, spec)
    return tuple(seen.values())


__all__ = [
    "MCP_CALL_TIMEOUT",
    "TRANSPORT_HTTP",
    "TRANSPORT_STDIO",
    "McpClient",
    "McpServerSpec",
    "specs_of",
    "tool_name_for",
]
