"""MCP 客户端的测试。

**这些测试走的是真 MCP 协议**：`mcp.Client(server_instance)` 支持进程内连一个
真的 `MCPServer`——所以**不需要假件**（`test_no_fakes_in_production` 的
`ALLOWED_TEST_FAKES` 表不用动），断言的也是真行为。

⚠️ **SDK 2.x 的两个坑**（照 v1 文档写会直接错）：
1. `FastMCP` 改名成 `MCPServer`（`from mcp.server.mcpserver import MCPServer`）；
2. `Tool` 的 Python 属性名是 `input_schema`（wire 上仍是 `inputSchema`）。
"""

from __future__ import annotations

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import ToolCall
from pmstudio.infrastructure.mcp.client import McpClient, McpServerSpec
from pmstudio.infrastructure.mcp.manager import McpToolManager
from pmstudio.infrastructure.mcp.projector import project_tools, source_label


def _server() -> MCPServer:
    """一个真 MCP server，带两个工具。"""

    server = MCPServer("demo")

    @server.tool()
    def add(a: int, b: int) -> int:
        """两数相加"""
        return a + b

    @server.tool()
    def echo(text: str) -> str:
        """原样回显"""
        return text

    return server


def _factory(server: MCPServer):
    """造客户端的钩子：返回进程内的真 client（走的还是真协议）。"""

    def build(_spec: McpServerSpec) -> Client:
        return Client(server)

    return build


class _FakeTool:
    """带 MCP 形状属性的替身（**只用于投影的边界输入**）。"""

    def __init__(self, name, description, schema) -> None:
        self.name = name
        self.description = description
        self.input_schema = schema


# —— R（需求）：投影 ——


def test_the_projection_makes_names_globally_unique() -> None:
    """两个 server 各有 `search` → 投影后名字必须不同，否则模型只能看到一个。"""

    left = project_tools(
        [_FakeTool("search", "搜左边", {"type": "object", "properties": {}})],
        source=source_label("left"),
    )
    right = project_tools(
        [_FakeTool("search", "搜右边", {"type": "object", "properties": {}})],
        source=source_label("right"),
    )

    assert left[0].name == "mcp__left__search"
    assert right[0].name == "mcp__right__search"
    assert left[0].name != right[0].name


def test_a_non_object_input_schema_is_skipped() -> None:
    """不是 object 形状 → **跳过**，不改写 schema（改写等于骗模型）。"""

    specs = project_tools(
        [_FakeTool("weird", "奇怪的那个", {"type": "array", "items": {}})],
        source=source_label("s"),
    )

    assert specs == ()


def test_a_tool_without_a_description_is_skipped() -> None:
    """没有描述 = 模型不知道它能干什么 = 不会调它。跳过比给空描述强。"""

    specs = project_tools(
        [_FakeTool("silent", "", {"type": "object", "properties": {}})],
        source=source_label("s"),
    )

    assert specs == ()


def test_a_tool_without_a_name_is_skipped() -> None:
    specs = project_tools(
        [_FakeTool("", "有描述", {"type": "object", "properties": {}})],
        source=source_label("s"),
    )

    assert specs == ()


# —— I（实现）：失败语义 ——


@pytest.mark.asyncio
async def test_calling_before_start_raises_tool_call_failed() -> None:
    client = McpClient(McpServerSpec(name="demo", transport="http", url="http://x/mcp"))
    with pytest.raises(ToolCallFailed):
        await client.call("whatever", {})


def test_a_server_spec_needs_its_transport_specific_fields() -> None:
    with pytest.raises(ValueError):
        McpServerSpec(name="s", transport="stdio")  # 缺 command
    with pytest.raises(ValueError):
        McpServerSpec(name="s", transport="http")  # 缺 url
    with pytest.raises(ValueError):
        McpServerSpec(name="s", transport="carrier-pigeon")  # 未知传输


# —— P（生产）：真协议往返 ——


@pytest.mark.asyncio
async def test_a_real_in_process_server_round_trips() -> None:
    """**真 MCP 协议**（进程内）：list + call 都通。"""

    client = McpClient(
        McpServerSpec(name="demo", transport="http", url="http://x/mcp"),
        client_factory=_factory(_server()),
    )

    specs = await client.start()
    try:
        assert {s.name for s in specs} == {"mcp__demo__add", "mcp__demo__echo"}
        assert specs[0].parameters["type"] == "object"

        out = await client.call("add", {"a": 2, "b": 3})
        assert out == "5"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_the_manager_aggregates_and_routes() -> None:
    """manager 起好之后：工具清单可用、按名字路由能调到（**用的是去掉前缀的名字**）。"""

    manager = McpToolManager(
        [McpServerSpec(name="demo", transport="http", url="http://x/mcp")],
        client_factory=_factory(_server()),
    )

    await manager.start()
    try:
        names = {s.name for s in manager.tools()}
        assert names == {"mcp__demo__add", "mcp__demo__echo"}

        spec = next(s for s in manager.tools() if s.name.endswith("__add"))
        call = ToolCall(call_id="c1", name=spec.name, arguments={"a": 1, "b": 1})
        out = await manager.invoke(spec, call)
        assert out == "2"
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_a_dead_server_does_not_break_the_others() -> None:
    """一个 server 连不上 → 它的工具不出现，**别的照常**。"""

    def build(spec: McpServerSpec) -> Client:
        if spec.name == "dead":
            return Client("http://127.0.0.1:1/mcp")  # 连不上
        return Client(_server())

    manager = McpToolManager(
        [
            McpServerSpec(name="dead", transport="http", url="http://127.0.0.1:1/mcp"),
            McpServerSpec(name="good", transport="http", url="http://x/mcp"),
        ],
        client_factory=build,
    )

    specs = await manager.start()
    try:
        # 好的那个照常可用，坏的那个不出现
        assert any(s.name.startswith("mcp__good__") for s in specs)
        assert not any(s.name.startswith("mcp__dead__") for s in specs)
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_the_manager_stops_every_server() -> None:
    """stop 之后不留连接。"""

    manager = McpToolManager(
        [McpServerSpec(name="demo", transport="http", url="http://x/mcp")],
        client_factory=_factory(_server()),
    )
    await manager.start()
    await manager.stop()
    assert manager.tools() == ()
