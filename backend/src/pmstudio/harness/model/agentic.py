"""agentic 循环：让**模型自己**决定调哪些工具、调几次。

**它与 `structured.call_structured` 是并列的两条路，不是替代关系**：
- `call_structured`：**单发单 JSON**。5 个现有 agent 走它，行为一字不变。
- `run_tool_loop`：**多轮 + 工具**。模型要工具时回 `tool_calls`，执行完把结果
  回灌给它，它再决定下一步。

**为什么循环放在 harness 而不是 application**：循环本身是"怎么跟模型说话"
（发一次、收一次、带上工具、再发一次），和产品无关。`harness` 是最内层，
放这里它就天然对 5 个 agent 不可见——**新能力不会误伤旧路径**。

**为什么工具执行是注进来的**：`harness` 只许依赖 `pydantic`/`litellm`/`pybreaker`，
MCP SDK 落不进来。执行方由 `bootstrap` 注入一个 `ToolInvoker` 实现
（见 `harness/tools/invoker.py` 的说明）。

**死循环怎么防**：轮数上限（`max_turns`）。到上限**显式标注**"已达轮数上限"，
不静默截断——照实说是本项目的一贯纪律（"截断照实说"）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.model.contracts import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
)
from pmstudio.harness.tools.invoker import ToolInvoker
from pmstudio.harness.tools.spec import ToolCall, ToolSpec, as_mapping

#: agentic 循环的轮数上限。
#:
#: **为什么是 6**：现有"一轮内最多 3 次席位轮"（`02-编排与数据.md` §2）是同一条纪律的
#: 另一处落点——循环必须有上限，否则模型可以一直要工具。3 轮是"多数任务收敛"的量，
#: 6 = 3 轮正常 + 3 轮容错余量。**它不是随手写的魔数**：登记在
#: `02-编排与数据.md §11.0.90` 的上限登记表里，`tests/contracts/test_limits.py` 盯着。
AGENTIC_MAX_TURNS = 6

#: 单轮内**执行**的工具调用数上限。
#:
#: **为什么需要它**：模型一轮里要调几个就调几个，没有上限时一个"执着"的模型
#: 可以在一轮里发几十次调用——每次都是一次真实的外部请求（联网 / 子进程）。
#: **登记在 `02-编排与数据.md §11.0.90` 的上限登记表里。**
MAX_TOOL_CALLS_PER_TURN = 5

#: 全程工具调用数上限。
#:
#: **为什么需要它**：`AGENTIC_MAX_TURNS` 只管**轮数**，管不了**次数**——
#: 最坏情况是 6 轮 × N 次。这一条把总次数钉死。
#: **登记在 `02-编排与数据.md §11.0.90` 的上限登记表里。**
MAX_TOOL_CALLS_PER_LOOP = 12

#: 工具失败时回灌给模型的那句话的**前缀**。为什么要回灌而不是直接抛：
#: `ToolCallFailed` 的语义是"工具失败**不判死整轮**（留痕继续跑）"——
#: 模型换个工具、或者自己收尾，比整轮判死更有用。
#:
#: 但**不能**把失败伪装成正常结果：所以这里明确告诉模型"这个工具失败了"，
#: 模型据此自己决定。失败是失败，只是不判死整轮。
TOOL_FAILURE_PREFIX = "工具 {name} 调用失败：{message}"


@dataclass(frozen=True, slots=True)
class ToolTurn:
    """一轮的轨迹。留样给人看，也是测试的断言对象。"""

    index: int
    #: 模型这轮要调的工具。
    calls: tuple[ToolCall, ...] = ()
    #: 每个工具调用的结果（文本）。与 `calls` **按序号**一一对应。
    results: tuple[str, ...] = ()
    #: 失败的条目：`(序号, 工具名, 原因)`。
    #:
    #: **按序号不按工具名**：同一轮里对同一工具调两次失败时，
    #: 按名字做 key 会让第二条覆盖第一条——留痕就少了一条。
    failures: tuple[tuple[int, str, str], ...] = ()
    #: 模型这轮的最终文本。
    text: str = ""


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    """循环的最终结果。"""

    #: 模型的最终答复。
    text: str
    #: 每一轮的轨迹。
    turns: tuple[ToolTurn, ...] = ()
    #: 是否因为到达轮数上限而停下。
    hit_turn_limit: bool = False
    #: 实际跑了几轮。
    turns_used: int = 0


async def run_tool_loop(
    *,
    gateway: ModelGateway,
    invoker: ToolInvoker,
    specs: Sequence[ToolSpec],
    messages: Sequence[ModelMessage],
    purpose: str,
    metadata: Mapping[str, str] | None = None,
    max_turns: int = AGENTIC_MAX_TURNS,
    schema_name: str = "tool_loop",
) -> ToolLoopResult:
    """让模型自己决定调哪些工具，直到它不再要工具或到达轮数上限。

    `specs` 为空时**直接返回**（不调模型）——没有工具就没有"自主调"可言，
    与其白跑一次模型，不如直接说清楚。
    """
    if not specs:
        return ToolLoopResult(text="", turns=(), hit_turn_limit=False, turns_used=0)

    by_name = {spec.name: spec for spec in specs}
    conversation: list[ModelMessage] = list(messages)
    turns: list[ToolTurn] = []
    hit_limit = False
    final_text = ""
    calls_used = 0

    for index in range(1, max_turns + 1):
        reply = await gateway.complete(
            ModelRequest(
                purpose=purpose,
                messages=tuple(conversation),
                schema_name=schema_name,
                # agentic 路径**不给** response_schema：它要的是"自由文本 + tool_calls"，
                # 不是"吐一个符合 schema 的 JSON"。给两样东西会被部分供应商拒收。
                response_schema={},
                metadata=dict(metadata or {}),
                tools=tuple(specs),
            )
        )
        if not reply.tool_calls:
            final_text = reply.text
            turns.append(ToolTurn(index=index, text=reply.text))
            break

        # **助手轮必须先回灌**：协议要求"带 tool_calls 的助手轮"出现在工具结果之前。
        # 少了它，工具结果就没有可对应的上文（实测：开思考模式时模型会重复要同一个工具）。
        conversation.append(
            ModelMessage(role="assistant", content=reply.text, tool_calls=reply.tool_calls)
        )

        results: list[str] = []
        # 按**序号**记，不按工具名：同一轮里对同一工具调两次失败时，
        # 按名字做 key 会让第二条覆盖第一条。
        failures: list[tuple[int, str, str]] = []
        skipped = 0
        for position, call in enumerate(reply.tool_calls):
            if calls_used + skipped >= MAX_TOOL_CALLS_PER_LOOP:
                # 全程调用数到顶：超出的一律不执行，**但照实记下来**——
                # 静默截断会让模型以为那些工具成功了。
                reason = f"已达全程工具调用上限（{MAX_TOOL_CALLS_PER_LOOP} 次），本轮剩余不再执行"
                failures.append((position, call.name, reason))
                output = TOOL_FAILURE_PREFIX.format(name=call.name, message=reason)
                results.append(output)
                skipped += 1
                conversation.append(
                    ModelMessage(role="tool", content=output, tool_call_id=call.call_id)
                )
                continue
            if len(results) >= MAX_TOOL_CALLS_PER_TURN:
                # 单轮到顶：同上，不执行、照实记、回灌。
                reason = f"已达单轮工具调用上限（{MAX_TOOL_CALLS_PER_TURN} 次），本轮剩余不再执行"
                failures.append((position, call.name, reason))
                output = TOOL_FAILURE_PREFIX.format(name=call.name, message=reason)
                results.append(output)
                skipped += 1
                conversation.append(
                    ModelMessage(role="tool", content=output, tool_call_id=call.call_id)
                )
                continue
            spec = by_name.get(call.name)
            if spec is None:
                # 模型要了一个我们没给的工具——**也要回灌**：不告诉它，
                # 它下一轮还会要同一个（它不知道这次没成）。
                reason = f"没有这个工具：{call.name}"
                failures.append((position, call.name, reason))
                output = TOOL_FAILURE_PREFIX.format(name=call.name, message=reason)
                results.append(output)
                conversation.append(
                    ModelMessage(role="tool", content=output, tool_call_id=call.call_id)
                )
                continue
            # **先记后调**：失败的那次也是一次真实的外部请求（网络/子进程），
            # 不算进额度就等于"失败可以无限重试"——最坏 6 轮 × N 次。
            calls_used += 1
            try:
                output = await invoker.invoke(spec, call)
            except ToolCallFailed as error:
                reason = error.message
                failures.append((position, call.name, reason))
                output = TOOL_FAILURE_PREFIX.format(name=call.name, message=reason)
            results.append(output)
            # 工具结果回灌：role="tool" + tool_call_id 是**协议要求**的形状。
            conversation.append(
                ModelMessage(role="tool", content=output, tool_call_id=call.call_id)
            )

        turns.append(
            ToolTurn(
                index=index,
                calls=tuple(reply.tool_calls),
                results=tuple(results),
                failures=tuple(failures),
                text=reply.text,
            )
        )
        if index == max_turns or calls_used >= MAX_TOOL_CALLS_PER_LOOP:
            hit_limit = True
            # **照实说**：把"为什么停"放进给调用方的文本里——不只留一个布尔标志。
            # 调用方（`mcp_probe.py` 等）不该自己拼这句话。
            final_text = (
                reply.text
                + f"\n\n（已达上限：{MAX_TOOL_CALLS_PER_LOOP} 次工具调用，剩余未执行）"
            )
            # **到顶就停**：再问下去模型只会继续要那些我们必然拒绝的工具，
            # 每一次都是**真实的付费模型调用**。停下不是"放弃"，是不再烧钱。
            break

    return ToolLoopResult(
        text=final_text,
        turns=tuple(turns),
        hit_turn_limit=hit_limit,
        turns_used=len(turns),
    )


def tool_call_from_payload(payload: Mapping[str, Any]) -> ToolCall:
    """从供应商回包的原始形状造 `ToolCall`。

    供应商的 `tool_calls` 元素是 `{"id":..., "function":{"name":..., "arguments":"{...}"}}`——
    `arguments` 是**字符串**（JSON），要解一次。

    **嵌套的 `function` 可能是 pydantic 对象而不是 dict**（litellm 就是这样），
    所以这里用 `as_mapping` 统一处理——不认 pydantic 就会取不到工具名
    （真踩过：模型明明要调工具，我们报"模型返回了空内容"）。
    """
    import json

    function = as_mapping(payload.get("function") or {})
    raw_arguments = function.get("arguments")
    if raw_arguments is None or raw_arguments == "":
        # 没有参数 → 空映射。**不许给 `None`**：`ToolCall.arguments` 声明是
        # `Mapping[str, object]`，给 None 会让后面的 `dict(arguments)` 抛
        # TypeError，失败信息指向"'NoneType' object is not iterable"——
        # 完全指错方向（真实原因是供应商没给参数）。
        arguments = {}
    elif isinstance(raw_arguments, str):
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            # 解不动就当空参数——**不是**编一个：空参数会让工具自己报参数不对，
            # 那比塞一个假参数让它"看起来正常"安全。
            arguments = {}
        else:
            # `null` 解析出来是 None → 同样归一成空映射（类型契约要成立）。
            arguments = dict(parsed) if isinstance(parsed, Mapping) else {}
    elif isinstance(raw_arguments, Mapping):
        arguments = dict(raw_arguments)
    else:
        arguments = {}
    return ToolCall(
        call_id=str(payload.get("id") or ""),
        name=str(function.get("name") or ""),
        arguments=arguments,
    )
