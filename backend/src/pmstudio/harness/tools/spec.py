"""工具规格：一次模型调用可以带哪些工具、模型请求调哪个工具。

**为什么单独一个文件**：它讲的是"工具的形状"，不是"工具怎么被登记"
（那是 `registry.py`）、也不是"工具怎么被执行"（那是 `invoker.py` 的协议）。
三个概念分开，是因为它们的**变化原因不同**：
形状跟着模型协议走，登记跟着目录走，执行跟着实现走。

**为什么不用 `ToolDefinition`**：那个是**应用工具**（联网检索 / 抓页）的声明形状，
字段集被 `tests/harness/test_registry.py::test_the_field_set_is_deliberate` 钉死
（曾经一次拒掉 9 个"看起来该有"的字段）。MCP 工具是**运行时发现**的
（server 起起来才知道它有什么），静态声明装不下，且两者的字段需求本来不同：
应用工具要 `requires`（缺密钥就不出现），模型工具要 JSON Schema（模型要读它）。

**纯数据 + 零外部依赖**：`harness` 只许依赖 `pydantic` / `litellm` / `pybreaker`
（`tests/architecture/test_harness_imports.py`），所以这里不出现任何 MCP SDK 的类型。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

#: 工具名的分隔符。用双下划线是因为工具名会被塞进模型的 function name，
#: 而 function name 的合法字符集比标识符宽——双下划线在任何供应商那边都是安全的。
NAME_SEPARATOR = "__"

#: MCP 工具名前缀。**必须与来源标识一起用**：多个 server 可能有同名工具
#: （都叫 `search` 是常事），不加前缀就会撞——撞了模型只能看到一个，
#: 另一个静默消失。所以"来源 + 名字"合成一个全局唯一的 function name。
MCP_NAME_PREFIX = "mcp" + NAME_SEPARATOR


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """一个可供模型调用的工具。

    `parameters` 是 **JSON Schema**（object 形状）。它会原样进供应商的
    `tools=` 声明里，所以**必须是模型能读的形状**——不是 Python 签名，
    不是 dataclass。
    """

    name: str
    description: str
    parameters: Mapping[str, object] = field(default_factory=dict)
    #: 这个工具来自哪里。**定位信息，不是业务**：模型不按它分支，
    #: 它只用来在失败时说清"哪个来源的工具坏了"、以及在投影时合成唯一名。
    source: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("工具必须有名字")
        if not self.description.strip():
            raise ValueError(f"工具 {self.name!r} 必须有描述：模型要靠它决定调不调")


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型请求调用某个工具。

    `arguments` 是**模型给的那份参数**（未校验）——校验属于执行方的事，
    执行方按自己工具的 schema 决定怎么处置不合法的参数。
    """

    call_id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.call_id.strip():
            raise ValueError("工具调用必须有 id：模型要把结果对上号")
        if not self.name.strip():
            raise ValueError("工具调用必须有工具名")


def qualify(source: str, name: str) -> str:
    """把"来源 + 工具名"合成一个全局唯一的 function name。

    来源为空时**原样返回**——本地工具（不是远端来的）不需要前缀，
    给它加前缀反而让模型看到一堆没来由的 `mcp__`。
    """
    if not source:
        return name
    return f"{source}{NAME_SEPARATOR}{name}"


def as_mapping(value: object) -> Mapping[str, object]:
    """把供应商给的对象变成 dict。

    **坑（真踩过）**：litellm 回的 `tool_calls` 元素是 **pydantic 对象**
    （`ChatCompletionMessageToolCall`），不是 dict；而 `function` 又是**另一个**
    pydantic 对象（`Function`）。只按 `isinstance(x, Mapping)` 判 → 两者都不是 dict →
    工具名取不到 → **模型明明要调工具，我们当它没要**（第一版真踩了：
    回包 `finish_reason=tool_calls`，我们这边报"模型返回了空内容"）。

    所以：先认 dict，再认 pydantic（`model_dump`），最后退回 `__dict__`。

    **为什么放这里**：网关（`providers/litellm.py`）与解析（`model/agentic.py`）
    都要用它——放一处，两处调用；各写一份 → 改一处就漂。
    """
    if isinstance(value, Mapping):
        return value
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        dumped = dump()
        if isinstance(dumped, Mapping):
            return dumped
    return dict(getattr(value, "__dict__", {}) or {})


def tool_payload(specs: Sequence[ToolSpec]) -> list[dict[str, object]]:
    """把 `ToolSpec` 转成供应商认识的 function 声明。

    形状是 OpenAI 的 `{"type":"function","function":{...}}`——litellm 把它
    原样发给各家供应商，是**事实标准**而不是某一家的方言。

    **放这里而不是网关里**：形状是"工具长什么样"，网关只是把它送出去。
    两处各写一份 → 改一处就漂。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": dict(spec.parameters),
            },
        }
        for spec in specs
    ]
