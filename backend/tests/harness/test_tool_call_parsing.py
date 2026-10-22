"""从供应商回包里摘 `tool_calls` 的解析测试。

**为什么单独一个文件**：这里踩过一个**很贵的坑**——
第一版只认 `Mapping`，而 litellm 回的 `tool_calls` 元素是 **pydantic 对象**
（`ChatCompletionMessageToolCall`），`function` 又是**另一个** pydantic 对象。
于是工具名取不到 → 模型明明要调工具，我们当它没要 → 报"模型返回了空内容"。

**真跑才发现的**：回包 `finish_reason=tool_calls`、`content=''`、`tool_calls` 有内容，
我们这边却在抛"空内容"。所以这里的用例**必须用真的对象形状**，不是 dict。
"""

from __future__ import annotations

from pmstudio.harness.model.providers.litellm import _tool_calls_of
from pmstudio.harness.tools.spec import as_mapping


class _Function:
    """litellm 的 `Function` 的形状（属性 + pydantic 的 model_dump）。"""

    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments

    def model_dump(self) -> dict[str, object]:
        return {"name": self.name, "arguments": self.arguments}


class _ToolCall:
    """litellm 的 `ChatCompletionMessageToolCall` 的形状。"""

    def __init__(self, name: str, arguments: str, call_id: str = "call_1") -> None:
        self.id = call_id
        self.type = "function"
        self.function = _Function(name, arguments)
        self.index = 0

    def model_dump(self) -> dict[str, object]:
        return {
            "id": self.id,
            "type": self.type,
            "index": self.index,
            "function": self.function,
        }


class _Message:
    def __init__(self, tool_calls) -> None:
        self.content = ""
        self.tool_calls = tool_calls


def test_tool_calls_are_read_from_pydantic_objects() -> None:
    """**真的供应商形状**：工具名与参数都要拿到。"""

    message = _Message([_ToolCall("mcp__demo__power", '{"base": 3, "exponent": 17}')])

    calls = _tool_calls_of(message)

    assert len(calls) == 1
    assert calls[0].name == "mcp__demo__power"
    assert calls[0].call_id == "call_1"
    assert calls[0].arguments == {"base": 3, "exponent": 17}


def test_tool_calls_are_also_read_from_plain_dicts() -> None:
    """dict 形状也要认（有的供应商/测试给的就是 dict）。"""

    message = _Message(
        [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}]
    )

    calls = _tool_calls_of(message)

    assert calls[0].name == "x"


def test_no_tool_calls_means_an_empty_tuple() -> None:
    message = _Message([])
    assert _tool_calls_of(message) == ()


def test_a_tool_call_without_a_name_is_skipped() -> None:
    """摘不出名字 → 跳过（不猜）。"""

    message = _Message([_ToolCall("", "{}")])
    assert _tool_calls_of(message) == ()


def test_as_mapping_handles_dicts_objects_and_nothing() -> None:
    assert as_mapping({"a": 1}) == {"a": 1}
    assert as_mapping(_Function("n", "{}")) == {"name": "n", "arguments": "{}"}
    assert as_mapping(object()) == {}
