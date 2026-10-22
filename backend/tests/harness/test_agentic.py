"""agentic 循环的测试：模型自己决定调哪些工具。

**为什么在这里测**：循环是"怎么跟模型说话"，属于 harness 的事。
现有 5 个 agent 走 `call_structured`（单发单 JSON），**它们一条用例都不该变**——
最后那条回归测试就是钉这个的。
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.model.agentic import (
    AGENTIC_MAX_TURNS,
    run_tool_loop,
    tool_call_from_payload,
)
from pmstudio.harness.model.contracts import ModelMessage, ModelReply, ModelRequest
from pmstudio.harness.tools.spec import ToolCall, ToolSpec


def _spec(name: str = "search") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="搜一下",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )


class _ScriptedGateway:
    """按剧本回话的网关。每回合的回复在构造时就定好了。"""

    def __init__(self, replies: list[ModelReply]) -> None:
        self._replies = list(replies)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("剧本用完了：模型要的轮数比预期多")
        return self._replies.pop(0)


class _RecordingInvoker:
    """记录调用、并按脚本回结果的执行方。"""

    def __init__(self, results: Mapping[str, str] | None = None) -> None:
        self.calls: list[ToolCall] = []
        self._results = dict(results or {})

    async def invoke(self, spec: ToolSpec, call: ToolCall) -> str:
        self.calls.append(call)
        return self._results.get(call.name, f"结果:{call.name}")


def _reply(*, text: str = "", calls: tuple[ToolCall, ...] = ()) -> ModelReply:
    return ModelReply(text=text, model="test", tool_calls=calls)


# —— R（需求）：契约形状 ——


def test_model_request_carries_tool_specs() -> None:
    spec = _spec()
    request = ModelRequest(
        purpose="p",
        messages=(ModelMessage(role="user", content="x"),),
        schema_name="s",
        response_schema={},
        tools=(spec,),
    )
    assert request.tools[0].name == spec.name


def test_model_reply_carries_tool_calls() -> None:
    call = ToolCall(call_id="c1", name="search", arguments={"q": "x"})
    reply = ModelReply(text="", model="m", tool_calls=(call,))
    assert reply.tool_calls[0].arguments == {"q": "x"}


def test_an_empty_tool_list_means_no_tools() -> None:
    """**这条是回归的锚点**：5 个现有 agent 走这条路，它们不传 tools。"""

    request = ModelRequest(
        purpose="p",
        messages=(ModelMessage(role="user", content="x"),),
        schema_name="s",
        response_schema={},
    )
    assert request.tools == ()
    assert ModelReply(text="t", model="m").tool_calls == ()


# —— I（实现）：循环的行为 ——


@pytest.mark.asyncio
async def test_the_loop_stops_when_the_model_asks_for_no_tool() -> None:
    gateway = _ScriptedGateway([_reply(text="结论")])
    invoker = _RecordingInvoker()

    result = await run_tool_loop(
        gateway=gateway,
        invoker=invoker,
        specs=(_spec(),),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert result.text == "结论"
    assert result.turns_used == 1
    assert invoker.calls == []


@pytest.mark.asyncio
async def test_the_loop_executes_the_requested_tool() -> None:
    call = ToolCall(call_id="c1", name="search", arguments={"q": "工单"})
    gateway = _ScriptedGateway([_reply(calls=(call,)), _reply(text="找到了")])
    invoker = _RecordingInvoker({"search": "三条结果"})

    result = await run_tool_loop(
        gateway=gateway,
        invoker=invoker,
        specs=(_spec(),),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert [c.name for c in invoker.calls] == ["search"]
    assert result.text == "找到了"
    assert result.turns_used == 2
    # 工具结果要回灌给模型，否则第二轮它不知道刚才查到了什么。
    assert any(m.role == "tool" and m.content == "三条结果" for m in gateway.requests[1].messages)


@pytest.mark.asyncio
async def test_a_failing_tool_is_fed_back_as_text_not_raised() -> None:
    """工具失败**不判死整轮**（`ToolCallFailed` 的原意），但**必须让模型知道它失败了**。"""

    class _FailingInvoker:
        async def invoke(self, spec: ToolSpec, call: ToolCall) -> str:
            raise ToolCallFailed("上游 500", tool_id=call.name)

    call = ToolCall(call_id="c1", name="search", arguments={})
    gateway = _ScriptedGateway([_reply(calls=(call,)), _reply(text="换个办法")])

    result = await run_tool_loop(
        gateway=gateway,
        invoker=_FailingInvoker(),
        specs=(_spec(),),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert result.text == "换个办法"
    assert result.turns[0].failures["search"] == "上游 500"
    fed_back = gateway.requests[1].messages
    assert any(m.role == "tool" and "失败" in m.content for m in fed_back)


@pytest.mark.asyncio
async def test_an_unknown_tool_is_reported_to_the_model() -> None:
    """模型要了一个没给的工具：记下来并告诉它，**不静默跳过**。"""

    call = ToolCall(call_id="c1", name="nope", arguments={})
    gateway = _ScriptedGateway([_reply(calls=(call,)), _reply(text="好")])

    result = await run_tool_loop(
        gateway=gateway,
        invoker=_RecordingInvoker(),
        specs=(_spec(),),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert "nope" in result.turns[0].failures
    assert any("失败" in m.content for m in gateway.requests[1].messages if m.role == "tool")


@pytest.mark.asyncio
async def test_the_loop_stops_at_the_turn_limit() -> None:
    """死循环防护：到上限就停，且**显式标注**——照实说，不静默截断。"""

    call = ToolCall(call_id="c1", name="search", arguments={})
    gateway = _ScriptedGateway([_reply(calls=(call,))] * (AGENTIC_MAX_TURNS + 3))

    result = await run_tool_loop(
        gateway=gateway,
        invoker=_RecordingInvoker(),
        specs=(_spec(),),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert result.turns_used == AGENTIC_MAX_TURNS
    assert result.hit_turn_limit is True


@pytest.mark.asyncio
async def test_no_tools_means_no_model_call() -> None:
    """没有工具就没有"自主调"可言——不白跑一次模型。"""

    gateway = _ScriptedGateway([])

    result = await run_tool_loop(
        gateway=gateway,
        invoker=_RecordingInvoker(),
        specs=(),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert result.turns_used == 0
    assert gateway.requests == []


@pytest.mark.asyncio
async def test_the_request_carries_the_tools_to_the_gateway() -> None:
    """网关必须真的收到 `tools=`——否则"模型自主调"就是假的。"""

    gateway = _ScriptedGateway([_reply(text="ok")])

    await run_tool_loop(
        gateway=gateway,
        invoker=_RecordingInvoker(),
        specs=(_spec(),),
        messages=(ModelMessage(role="user", content="查一下"),),
        purpose="probe",
    )

    assert len(gateway.requests[0].tools) == 1
    assert gateway.requests[0].tools[0].name == "search"


def test_tool_call_from_payload_reads_the_wire_shape() -> None:
    """供应商给的是 `{"id":..,"function":{"name":..,"arguments":"{...}"}}`——arguments 是字符串。"""

    call = tool_call_from_payload(
        {"id": "call_1", "function": {"name": "search", "arguments": '{"q": "工单"}'}}
    )
    assert call.call_id == "call_1"
    assert call.name == "search"
    assert call.arguments == {"q": "工单"}


def test_tool_call_from_payload_survives_broken_arguments() -> None:
    """参数解不动就当空参数——**不编一个**。空参数会让工具自己报参数不对。"""

    call = tool_call_from_payload(
        {"id": "c", "function": {"name": "search", "arguments": "not json"}}
    )
    assert call.arguments == {}
