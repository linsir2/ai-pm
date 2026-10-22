"""MCP 客户端的测试。

**这些测试走的是真 MCP 协议**：`mcp.Client(server_instance)` 支持进程内连一个
真的 `MCPServer`——所以**不需要假件**（`test_no_fakes_in_production` 的
`ALLOWED_TEST_FAKES` 表不用动），断言的也是真行为。

⚠️ **SDK 2.x 的两个坑**（照 v1 文档写会直接错）：
1. `FastMCP` 改名成 `MCPServer`（`from mcp.server.mcpserver import MCPServer`）；
2. `Tool` 的 Python 属性名是 `input_schema`（wire 上仍是 `inputSchema`）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.tools.spec import ToolCall
from pmstudio.infrastructure.mcp.client import McpClient, McpServerSpec
from pmstudio.infrastructure.mcp.manager import McpToolManager
from pmstudio.infrastructure.mcp.projector import project_tools, source_label

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEMO_SERVER = BACKEND_ROOT / "scripts" / "mcp_demo_server.py"


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


# —— C（覆盖缺口）：真子进程 / 生命周期 / 一致性 ——


@pytest.mark.asyncio
async def test_a_real_stdio_server_round_trips() -> None:
    """**真子进程**的 stdio 传输（C3）：起一个真的 MCP server 进程，走真协议。

    进程内的 `Client(server_instance)`（上面那条）**证明不了 stdio 传输**——
    而 stdio 才是 Claude Desktop / Cursor 那类配置的**事实标准**。
    这条用例是那个传输的自动化覆盖：`scripts/mcp_demo_server.py` 是独立进程，
    通过 stdin/stdout 通信。
    """

    client = McpClient(
        McpServerSpec(
            name="demo",
            transport="stdio",
            command=sys.executable,
            args=(str(DEMO_SERVER),),
            cwd=str(BACKEND_ROOT),
        )
    )
    try:
        specs = await client.start()
        assert {s.name for s in specs} == {"mcp__demo__power", "mcp__demo__word_count"}
        assert await client.call("power", {"base": 3, "exponent": 17}) == "129140163"
    finally:
        await client.stop()


class _CloseCountingClient:
    """包一个真的 `mcp.Client`，只多记一笔"被关了几次"。

    **不是替身**：里面是进程内的真 MCP server，走的还是真协议。
    包装它只为**观察生命周期**——`start()` 有没有把上一次的连接关掉。
    """

    def __init__(self, inner: Client, closed: list[int]) -> None:
        self._inner = inner
        self._closed = closed

    async def __aenter__(self) -> _CloseCountingClient:
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self._closed.append(1)
        return await self._inner.__aexit__(*exc)

    async def list_tools(self) -> object:
        return await self._inner.list_tools()

    async def call_tool(self, *args: object, **kwargs: object) -> object:
        return await self._inner.call_tool(*args, **kwargs)


@pytest.mark.asyncio
async def test_repeated_start_closes_the_previous_connection() -> None:
    """重复 `start()` 必须先关掉上一次（B4）——否则每次"重启"都泄漏一份
    transport 与一个 stdio 子进程（子进程还活着，stdin/stdout 挂着）。
    """

    closed: list[int] = []
    server = _server()
    client = McpClient(
        McpServerSpec(name="demo", transport="http", url="http://x/mcp"),
        client_factory=lambda _spec: _CloseCountingClient(Client(server), closed),
    )
    try:
        await client.start()
        await client.start()
        assert len(closed) == 1, "第二次 start() 没关掉第一次的连接（泄漏 transport）"
    finally:
        await client.stop()
    assert len(closed) == 2, "stop() 没关掉最后一次连接"


@pytest.mark.asyncio
async def test_a_start_failure_is_not_masked_by_cleanup() -> None:
    """清理时的异常**不许顶替**真实错因（B4）。

    清理发生在 `start()` 的失败路径上（此时正要抛 `ToolCallFailed`）。
    若让 `aclose()` 的异常冒出去，"连不上 server X"就变成"关闭时出错"——排查方向直接跑偏。
    """

    class _EntersThenFails:
        async def __aenter__(self) -> _EntersThenFails:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            raise RuntimeError("关闭时也炸了")

        async def list_tools(self) -> object:
            raise RuntimeError("list 失败：server 没响应")

    client = McpClient(
        McpServerSpec(name="demo", transport="http", url="http://x/mcp"),
        client_factory=lambda _spec: _EntersThenFails(),
    )
    with pytest.raises(ToolCallFailed) as info:
        await client.start()
    assert "list 失败" in str(info.value)
    assert "关闭时也炸了" not in str(info.value), "清理异常顶替了真实错因"


@pytest.mark.asyncio
async def test_all_three_exits_report_the_same_tools() -> None:
    """聚合只有**一套口径**（B5）：`manager.start()` 的返回值与 `manager.tools()`
    必须一致——三处各说各话时，模型看到的工具清单就和路由表对不上
    （调用了却没路由，是静默的）。
    """

    manager = McpToolManager(
        [McpServerSpec(name="demo", transport="http", url="http://x/mcp")],
        client_factory=_factory(_server()),
    )
    try:
        returned = await manager.start()
        assert returned == manager.tools()
        # 路由表是"实际发给模型的那份"的唯一来源：它必须和上面两个出口一致。
        assert set(manager._routes) == {s.name for s in manager.tools()}
    finally:
        await manager.stop()


@pytest.mark.asyncio
async def test_a_tool_failure_carries_its_source() -> None:
    """工具失败的 `tool_id` **带来源**（C4）：两个 server 有同名工具时，
    光看裸名字分不出是谁的。"""

    class _Dead:
        async def __aenter__(self) -> _Dead:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def list_tools(self) -> object:
            class _R:
                tools: list = []

            return _R()

        async def call_tool(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("boom")

    client = McpClient(
        McpServerSpec(name="left", transport="http", url="http://x/mcp"),
        client_factory=lambda _spec: _Dead(),
    )
    try:
        await client.start()
        with pytest.raises(ToolCallFailed) as info:
            await client.call("search", {})
        assert info.value.tool_id == "mcp__left__search"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_a_structured_only_result_is_not_dropped() -> None:
    """server **只**回结构化内容（`content` 空、`structured_content` 有值）时，
    交给模型的必须是那份 JSON，**不是空串**（B2）。

    MCP 协议（2026-07）允许这种形状。只读 `content` 会让模型收到空串——
    那既不是失败（`is_error` 为假、不抛错）也不是内容，是最典型的
    "把失败伪装成正常结果"。
    """

    class _StructuredOnly:
        async def __aenter__(self) -> _StructuredOnly:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def list_tools(self) -> object:
            class _R:
                tools = [_FakeTool("stats", "统计", {"type": "object", "properties": {}})]

            return _R()

        async def call_tool(self, *args: object, **kwargs: object) -> object:
            class _Result:
                content: tuple = ()
                structured_content = {"words": 3, "chars": 5}
                is_error = False

            return _Result()

    client = McpClient(
        McpServerSpec(name="demo", transport="http", url="http://x/mcp"),
        client_factory=lambda _spec: _StructuredOnly(),
    )
    try:
        await client.start()
        assert json.loads(await client.call("stats", {})) == {"words": 3, "chars": 5}
    finally:
        await client.stop()


def test_an_illegal_tool_name_is_skipped() -> None:
    """工具名含非法字符 / 超 64 字符 → **跳过并 warning**，其余工具不受影响（B3）。

    **整份 `tools` 是一次性发出去的**——一个非法名让**整个请求** 400，
    而不是这一个工具坏。所以投影阶段就必须拦住（不改写名字：改写等于骗模型）。
    """

    specs = project_tools(
        [
            _FakeTool("has.dot", "带点的", {"type": "object", "properties": {}}),
            _FakeTool("a" * 65, "超长的", {"type": "object", "properties": {}}),
            _FakeTool("ok_name", "合规的", {"type": "object", "properties": {}}),
        ],
        source=source_label("s"),
    )

    assert [s.name for s in specs] == ["mcp__s__ok_name"]
