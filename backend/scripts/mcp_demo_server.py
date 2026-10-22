"""一个**真的** stdio MCP server（演示用）。

它是独立进程（`python scripts/mcp_demo_server.py`），通过 stdin/stdout 与
MCP 客户端通信——**这是 Claude Desktop / Cursor 那类 MCP 配置的标准形态**。

它存在的意义：`mcp.Client(server_instance)` 走的是进程内连接，
**证明不了 stdio 传输**。而 stdio 才是事实上的主流传输。

用法（当作 MCP server 挂给客户端）：
    python3 scripts/mcp_demo_server.py
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC = BACKEND_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp.server.mcpserver import MCPServer  # noqa: E402

server = MCPServer("pmstudio-demo-stdio")


@server.tool()
def power(base: int, exponent: int) -> int:
    """计算一个整数的正整数次方"""
    return base**exponent


@server.tool()
def word_count(text: str) -> int:
    """数一段文本里有多少个词"""
    return len(text.split())


if __name__ == "__main__":
    server.run()
