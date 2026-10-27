"""MCP 真跑验证：让**真模型**自己决定调哪个 MCP 工具。

**它证明什么**：
1. MCP server 的工具能投影成模型看得懂的声明；
2. 模型能**自主**选中工具（不是我们指定它调哪个）；
3. 工具结果能回灌给模型，它据此给最终答复；
4. 每一步都留了样（`model_calls` 表）——**流量可查**。

**它不做什么**：不碰文档、不写库、不进主闭环。MCP 是**只读旁路**
（`04-未决.md` 的裁决）。所以这个脚本是**验收凭据**，不是产品功能。

用法：
    cd backend
    uv run python scripts/mcp_probe.py                    # 用 .env 里的 MCP 配置
    uv run python scripts/mcp_probe.py --builtin          # 用内置的演示 server
    uv run python scripts/mcp_probe.py --list             # 只列工具，不调模型

**它不跑在工作库上**：与 `scripts/eval.py` 同一纪律——每次从干净库开始，
保证可重复。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from scripts.dev import load_env_file  # noqa: E402 —— 复用起服务的那个 .env 读取

from pmstudio.bootstrap.settings import Settings  # noqa: E402
from pmstudio.harness.model.agentic import run_tool_loop  # noqa: E402
from pmstudio.harness.model.contracts import ModelMessage  # noqa: E402
from pmstudio.infrastructure.mcp.client import McpServerSpec  # noqa: E402
from pmstudio.infrastructure.mcp.manager import McpToolManager  # noqa: E402

#: 演示用的提示词。**故意给一个"必须查才知道答案"的问题**——
#: 这样模型"不调工具就答不出来"，能证明它是真的自主决策（不是我们指定它调）。
DEMO_PROMPT = (
    "用可用的工具查一下：3 的 17 次方等于多少？"
    "请用工具算，不要凭记忆答。最后给出答案和一句依据。"
)


def _builtin_specs() -> list[McpServerSpec]:
    """内置的演示 server：一个真的 MCP server（进程内）。"""

    return [McpServerSpec(name="demo", transport="http", url="http://builtin/mcp")]


def _builtin_factory():
    """内置 server 的客户端钩子。

    **注意**：它连的是一个**真的** `MCPServer`——走的是真 MCP 协议，
    只是省掉起子进程。它是"演示用 server"，不是"假实现"。
    """

    from mcp import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("pmstudio-demo")

    @server.tool()
    def power(base: int, exponent: int) -> int:
        """计算一个整数的正整数次方"""
        return base**exponent

    @server.tool()
    def multiply(a: int, b: int) -> int:
        """两个整数相乘"""
        return a * b

    def build(_spec: McpServerSpec) -> Client:
        return Client(server)

    return build


def _specs_from_settings(settings: Settings) -> list[McpServerSpec]:
    """把配置里的 dict 变成 `McpServerSpec`。"""

    out: list[McpServerSpec] = []
    for item in settings.mcp_servers:
        out.append(
            McpServerSpec(
                name=str(item["name"]),
                transport=str(item.get("transport", "stdio")),
                command=str(item.get("command", "")),
                args=tuple(str(a) for a in item.get("args", []) or []),
                env=dict(item.get("env", {}) or {}),
                url=str(item.get("url", "")),
                cwd=str(item.get("cwd", "") or ""),
            )
        )
    return out


async def _run(args: argparse.Namespace) -> int:
    # `.env` 的读法**复用 `scripts/dev.py`**（同一个 loader，不写第二份）
    load_env_file(BACKEND_ROOT / ".env")
    settings = Settings.from_env()
    if args.builtin:
        specs = _builtin_specs()
        factory = _builtin_factory()
    else:
        specs = _specs_from_settings(settings)
        factory = None
        if not specs:
            print(
                "没有配置任何 MCP server。\n"
                "  · 用 --builtin 起一个内置的演示 server；或\n"
                "  · 在 backend/.env 里设 PMSTUDIO_MCP_SERVERS（JSON 数组）。",
                file=sys.stderr,
            )
            return 2

    manager = McpToolManager(specs, client_factory=factory)
    print(f"连 {len(specs)} 个 MCP server …")
    tools = await manager.start()
    if not tools:
        print("一个工具都没拿到——server 都没连上。", file=sys.stderr)
        await manager.stop()
        return 1

    print(f"\n拿到 {len(tools)} 个工具：")
    for spec in tools:
        params = ", ".join(sorted(spec.parameters.get("properties", {}) or {}))
        print(f"  · {spec.name}")
        print(f"      {spec.description}")
        print(f"      参数：{params or '（无）'}")

    if args.list:
        await manager.stop()
        return 0

    # 造网关：**复用生产装配的那一个**（`bootstrap/container.py` 里的 `_model_gateway`）。
    # 留样写进 PostgreSQL 的 `model_calls`。默认用 app URL；可用
    # `PMSTUDIO_MCP_PROBE_DATABASE_URL` 指到一个专用库（仍必须是已 init 的 PG）。
    from pmstudio.bootstrap.container import _model_gateway
    from pmstudio.infrastructure.postgres.database import PgConnection
    from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder

    sample_url = (
        os.environ.get("PMSTUDIO_MCP_PROBE_DATABASE_URL", "").strip()
        or settings.database_url
    )
    connection = PgConnection(sample_url)
    recorder = SqliteModelCallRecorder(connection)
    gateway = _model_gateway(settings, recorder=recorder)
    if gateway is None:
        print("模型网关没造出来——检查 PMSTUDIO_MODEL / PMSTUDIO_API_KEY。", file=sys.stderr)
        await manager.stop()
        return 2

    print(f"\n模型：{settings.model}")
    print(f"问题：{DEMO_PROMPT}\n")
    print("— 模型自己决定调哪个工具 —")

    result = await run_tool_loop(
        gateway=gateway,
        invoker=manager,
        specs=tools,
        messages=(ModelMessage(role="user", content=DEMO_PROMPT),),
        purpose="mcp_probe",
        metadata={"trace_id": "mcp-probe"},
    )

    for turn in result.turns:
        if turn.calls:
            for call in turn.calls:
                args = json.dumps(dict(call.arguments), ensure_ascii=False)
                print(f"  [第 {turn.index} 轮] 模型要调 {call.name}({args})")
            # `failures` 是 `(序号, 工具名, 原因)` 的元组——**按序号不按名字**
            # （同一轮里对同一工具调两次失败时，按名字做 key 会少记一条）。
            for position, name, reason in turn.failures:
                print(f"      失败：第 {position} 次 {name} — {reason}")
            for (call, output) in zip(turn.calls, turn.results, strict=False):
                print(f"      {call.name} → {output[:200]}")
        else:
            print(f"  [第 {turn.index} 轮] 模型给出最终答复")

    print(f"\n最终答复：\n{result.text}")
    print(
        f"\n跑了 {result.turns_used} 轮"
        + ("（**已达轮数上限**）" if result.hit_turn_limit else "")
    )
    print("留样在 PostgreSQL（purpose='mcp_probe'）——可以查每一趟的提示词与回复。")

    await manager.stop()
    connection.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 真跑验证")
    parser.add_argument("--list", action="store_true", help="只列工具，不调模型")
    parser.add_argument("--builtin", action="store_true", help="用内置的演示 server")
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
