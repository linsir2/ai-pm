"""模型调用契约搬进 harness 后的形状。

这些断言的作用是**防搬丢字段**：契约是四个 agent + 网关 + container 的共同底座，
少一个字段就是"搬完编译过、运行时 KeyError"。所以逐字段钉死。
"""

from dataclasses import fields

from pmstudio.harness.model.contracts import (
    ModelCallRecorder,
    ModelGateway,
    ModelMessage,
    ModelReply,
    ModelRequest,
    PromptLibrary,
)


def field_names(cls) -> tuple[str, ...]:
    return tuple(item.name for item in fields(cls))


def test_model_message_is_role_and_content():
    """`role` + `content` 是**必有**的两个字段。

    `tool_call_id` / `tool_calls`（MCP 批次）是**可选**字段，默认空：
    OpenAI 兼容的 function-calling 协议要求 `role="tool"` 的消息带 `tool_call_id`、
    且前面要有一条带 `tool_calls` 的助手轮——少了它们，严格实现（OpenAI / vLLM /
    Ollama）直接 400，宽松实现（DashScope）下模型认不出工具结果是给谁的
    （实测开思考模式时**重复要同一个工具**）。可选字段让 5 个现有 agent 一行不改。
    """

    assert field_names(ModelMessage) == (
        "role",
        "content",
        "tool_call_id",
        "tool_calls",
    )


def test_model_request_carries_schema_and_metadata():
    """`schema_name` + `response_schema` 是结构化输出的全部依据；`metadata` 供留样归堆。

    `max_tokens`（2026-10-22，批 14）：**输出 token 上限**——不给就不设（供应商用
    自己的默认），给了就真的发给供应商（`providers/litellm.py`）。

    `tools`（2026-10-22，MCP 批次）：**本次调用可供模型调用的工具**。
    加它而不是另立一个请求类型，是因为**默认值就是空**——现有 5 个 agent
    （Analyzer / 席位 / 整合者 / 讨论 / 记忆）走 `call_structured`，
    它们一个字都不用改，行为逐字不变。带工具的那条路在 `model/agentic.py`，
    与它是**并列**的两条，不是把现有路径改成多态。
    """

    assert field_names(ModelRequest) == (
        "purpose",
        "messages",
        "schema_name",
        "response_schema",
        "metadata",
        "max_tokens",
        "tools",
    )


def test_model_reply_carries_text_model_and_usage():
    """`tool_calls`（2026-10-22，MCP 批次）：**模型请求调用工具**。

    现有全部回复都是空——所以加它，5 个 agent 的行为一字不变。
    空的含义是"模型这次没要调工具"，**不是**"没调成功"。
    """

    assert field_names(ModelReply) == ("text", "model", "usage", "tool_calls")


def test_request_defaults_are_empty_not_guessing():
    request = ModelRequest(
        purpose="seat_review",
        messages=(ModelMessage(role="user", content="x"),),
        schema_name="SeatPacket",
        response_schema={},
    )
    assert request.metadata == {}
    assert request.tools == ()
    assert ModelReply(text="{}", model="m").usage == {}
    assert ModelReply(text="{}", model="m").tool_calls == ()


def test_gateway_is_a_runtime_checkable_protocol():
    class _Fake:
        async def complete(self, request):  # type: ignore[no-untyped-def]
            return ModelReply(text="{}", model="fake")

    assert isinstance(_Fake(), ModelGateway)
    assert not isinstance(object(), ModelGateway)


def test_prompt_library_protocol_takes_named_slots():
    class _Lib:
        def render(self, name: str, **slots: str) -> str:
            return name

    assert isinstance(_Lib(), PromptLibrary)


def test_call_recorder_protocol_keeps_failure_fields():
    """留样口必须有 `attempt` / `ok` / `error`——一次调用记多行，失败那几行是排查命根子。"""

    import inspect

    params = inspect.signature(ModelCallRecorder.record).parameters
    assert set(params) >= {
        "trace_id",
        "purpose",
        "model",
        "prompt_sha",
        "prompt_text",
        "reply_text",
        "usage",
        "duration_ms",
        "created_at",
        "attempt",
        "ok",
        "error",
    }
