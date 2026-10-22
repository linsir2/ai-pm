"""把 MCP 的工具列表投影成本项目的 `ToolSpec`。

**投影不是"改写"**：MCP 给的 `inputSchema` 原样进 `ToolSpec.parameters`。
只有一个地方我们**不改**而是**跳过**——schema 不是 object 形状时。
理由：模型读的就是这份 schema；我们替它"修"一个它本来没见过���形状，
等于让它按一份没见过的契约去填参数。跳过 + 记 warning 更诚实。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pmstudio.harness.tools.spec import ToolSpec, qualify

logger = logging.getLogger(__name__)

#: MCP 工具名前缀的来源标识。**必须带**：多个 server 可能有同名工具
#: （都叫 `search` 是常事），不加前缀就撞——撞了模型只能看到一个，
#: 另一个静默消失，用户还以为"这个工具不支持"。
MCP_SOURCE = "mcp"


def source_label(server_name: str) -> str:
    """某个 server 的来源标识。拼出来是 `mcp__<server>`。

    再由 `qualify` 拼上工具名 → `mcp__<server>__<tool>`，**全局唯一**。
    """
    return f"{MCP_SOURCE}__{server_name}"


def _is_object_schema(schema: Any) -> bool:
    """这份 inputSchema 是不是 object 形状。"""

    return isinstance(schema, dict) and schema.get("type") == "object"


def project_tools(tools: Sequence[Any], *, source: str) -> tuple[ToolSpec, ...]:
    """把 MCP 的 tools 投影成 `ToolSpec`。

    `tools` 的元素是 `mcp.types.Tool`（或任何有 `name` / `description` /
    `input_schema` 属性的对象）——**这里不 import MCP 的类型**，
    所以这一层可以被没有装 MCP 的环境直接测。
    """
    specs: list[ToolSpec] = []
    for tool in tools:
        name = str(getattr(tool, "name", "") or "")
        if not name:
            logger.warning("MCP 工具没有名字，跳过：%r", tool)
            continue
        schema = getattr(tool, "input_schema", None)
        if not _is_object_schema(schema):
            logger.warning(
                "MCP 工具 %s 的 inputSchema 不是 object 形状，跳过（不改写 schema）", name
            )
            continue
        description = str(getattr(tool, "description", "") or "")
        if not description.strip():
            # 没有描述 = 模型不知道它能干什么 = 不会调它。
            # 跳过比给它一个空描述强：空描述会让模型去试，试错成本由用户承担。
            logger.warning("MCP 工具 %s 没有描述，跳过（模型要靠描述决定调不调）", name)
            continue
        specs.append(
            ToolSpec(
                name=qualify(source, name),
                description=description,
                parameters=dict(schema),
                source=source,
            )
        )
    return tuple(specs)
