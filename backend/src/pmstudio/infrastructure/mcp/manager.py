"""管理多个 MCP server：生命周期 + 工具聚合 + 按名字路由。

**它实现 `harness/tools/invoker.py` 的 `ToolInvoker` 协议**——
所以 `harness` 那一层不需要知道 MCP 的存在（也不许知道：它只许依赖
`pydantic` / `litellm` / `pybreaker`）。

**一个 server 起不来怎么办**：那个 server 的工具不出现，**其他 server 照常可用**，
并且启动日志里点名是哪个失败、为什么。**不假装全好，也不因为一个坏的就全瘫**。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import ToolCall, ToolSpec
from pmstudio.infrastructure.mcp.client import McpClient, McpServerSpec
from pmstudio.infrastructure.mcp.projector import source_label

logger = logging.getLogger(__name__)


class _Route:
    """一个工具归哪个 client 管、用什么名字调。"""

    __slots__ = ("client", "bare_name", "spec")

    def __init__(self, client: McpClient, bare_name: str, spec: ToolSpec) -> None:
        self.client = client
        self.bare_name = bare_name
        self.spec = spec


class McpToolManager:
    """多个 MCP server 的总管。"""

    def __init__(
        self,
        specs: Sequence[McpServerSpec],
        *,
        client_factory: Callable[[McpServerSpec], Any] | None = None,
    ) -> None:
        self._clients: list[McpClient] = [
            McpClient(spec, client_factory=client_factory) for spec in specs
        ]
        self._routes: dict[str, _Route] = {}

    @property
    def clients(self) -> tuple[McpClient, ...]:
        return tuple(self._clients)

    async def start(self) -> tuple[ToolSpec, ...]:
        """连上所有 server，返回聚合后的工具清单。

        **逐个连，一个坏了不拖累别人**。
        """
        self._routes.clear()
        available: list[ToolSpec] = []
        for client in self._clients:
            try:
                tools = await client.start()
            except ToolCallFailed as error:
                logger.warning(
                    "MCP server %s 不可用，其余 server 不受影响：%s",
                    client.spec.name,
                    error.message,
                )
                continue
            available.extend(tools)
            for spec in tools:
                # 记住"这个工具归谁管"——路由要用。
                self._routes[spec.name] = _Route(
                    client, _bare_name(spec.name, client.spec.name), spec
                )
        return tuple(available)

    async def stop(self) -> None:
        """关掉所有 server。**幂等**。"""

        for client in self._clients:
            await client.stop()
        self._routes.clear()

    def tools(self) -> tuple[ToolSpec, ...]:
        """当前可用的工具。"""

        return tuple(route.spec for route in self._routes.values())

    async def invoke(self, spec: ToolSpec, call: ToolCall) -> str:
        """执行一次工具调用（`ToolInvoker` 协议的实现）。"""

        route = self._routes.get(call.name)
        if route is None:
            raise ToolCallFailed(
                f"没有这个工具：{call.name}",
                tool_id=call.name,
            )
        return await route.client.call(route.bare_name, call.arguments)

    async def call_by_name(self, name: str, arguments: Mapping[str, object]) -> str:
        """按名字调一次（脚本用；不经过 `ToolSpec`）。"""

        route = self._routes.get(name)
        if route is None:
            raise ToolCallFailed(f"没有这个工具：{name}", tool_id=name)
        return await route.client.call(route.bare_name, arguments)


def _bare_name(qualified: str, server_name: str) -> str:
    """`mcp__<server>__<tool>` → `<tool>`（去掉来源前缀）。

    去掉的是**我们加的**前缀，server 那边看到的还是它自己的工具名。
    """

    prefix = f"{source_label(server_name)}__"
    if qualified.startswith(prefix):
        return qualified[len(prefix) :]
    return qualified
