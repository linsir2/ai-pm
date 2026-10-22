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
    assert field_names(ModelMessage) == ("role", "content")


def test_model_request_carries_schema_and_metadata():
    """`schema_name` + `response_schema` 是结构化输出的全部依据；`metadata` 供留样归堆。

    `max_tokens`（2026-10-22，批 14）：**输出 token 上限**——不给就不设（供应商用
    自己的默认），给了就真的发给供应商（`providers/litellm.py`）。
    """

    assert field_names(ModelRequest) == (
        "purpose",
        "messages",
        "schema_name",
        "response_schema",
        "metadata",
        "max_tokens",
    )


def test_model_reply_carries_text_model_and_usage():
    assert field_names(ModelReply) == ("text", "model", "usage")


def test_request_defaults_are_empty_not_guessing():
    request = ModelRequest(
        purpose="seat_review",
        messages=(ModelMessage(role="user", content="x"),),
        schema_name="SeatPacket",
        response_schema={},
    )
    assert request.metadata == {}
    assert ModelReply(text="{}", model="m").usage == {}


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
