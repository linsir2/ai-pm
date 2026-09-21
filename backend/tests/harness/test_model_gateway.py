"""M22 模型接入：把 litellm 的调用细节藏在 Harness 里。"""

import os
from datetime import UTC, datetime

import pytest

# 禁用 litellm 联网拉取价目表（测试环境无网络）
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from pmstudio.contracts.enums import PromptRole, RegistryKind, RegistryOwner  # noqa: E402
from pmstudio.contracts.models.prompt import PromptMessage  # noqa: E402
from pmstudio.contracts.models.registry import RegistryEntry  # noqa: E402
from pmstudio.harness.model_gateway import GenerationFailure, ModelGateway  # noqa: E402
from pmstudio.registry.entries import InMemoryRegistry  # noqa: E402

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


class _Bus:
    async def publish(self, event: object, *, producer: object) -> None:
        raise AssertionError("不该发事件")


class _Clock:
    def now(self) -> datetime:
        return AT


def _make_registry() -> "InMemoryRegistry":
    return InMemoryRegistry(_Bus(), _Clock())


async def _register_model(registry: "InMemoryRegistry", **overrides: object) -> None:
    content = {
        "model": "dashscope/qwen3.8-flash",
        "api_key_env": "DASHSCOPE_API_KEY",
        "context_window_tokens": 32768,
        "timeout_seconds": 60,
    }
    content.update(overrides)
    await registry.register(
        RegistryEntry(
            id="reg_model_default",
            kind=RegistryKind.MODEL,
            name="Test Model",
            owner=RegistryOwner.PRESET,
            content=content,
        )
    )


def _messages() -> list[PromptMessage]:
    return [
        PromptMessage(role=PromptRole.SYSTEM, text="你是产品文档助手。"),
        PromptMessage(role=PromptRole.USER, text="细化功能清单。"),
    ]


def test_messages_map_to_provider_roles() -> None:
    """PromptRole → litellm 认识的角色名。"""
    gateway = ModelGateway.__new__(ModelGateway)
    mapped = gateway._to_litellm_messages(_messages())
    assert [m["role"] for m in mapped] == ["system", "user"]
    assert [m["content"] for m in mapped] == ["你是产品文档助手。", "细化功能清单。"]


def test_parameters_come_from_the_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型名/超时取自条目，不硬编码。"""
    import litellm

    received: dict = {}

    async def fake_completion(**kwargs: object) -> object:
        received.update(kwargs)

        class _Msg:
            content = "复述文本"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-123")

    registry = _make_registry()
    import asyncio

    asyncio.run(_register_model(registry, model="dashscope/custom-model", timeout_seconds=120))
    gateway = ModelGateway(registry)
    result = asyncio.run(gateway.complete("reg_model_default", _messages()))

    assert result == "复述文本"
    assert received["model"] == "dashscope/custom-model"
    assert received["timeout"] == 120


def test_missing_secret_reports_the_variable_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """密钥缺失时报变量名、不报值；retryable=False。"""
    import litellm

    async def fake_completion(**kwargs: object) -> object:
        raise AssertionError("不该调模型")

    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    registry = _make_registry()
    import asyncio

    asyncio.run(_register_model(registry))
    gateway = ModelGateway(registry)

    with pytest.raises(GenerationFailure) as exc_info:
        asyncio.run(gateway.complete("reg_model_default", _messages()))

    assert exc_info.value.retryable is False
    assert "DASHSCOPE_API_KEY" in str(exc_info.value)


def test_response_format_is_passed_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """response_format 参数应透传给 litellm。"""
    import litellm

    received: dict = {}

    async def fake_completion(**kwargs: object) -> object:
        received.update(kwargs)

        class _Msg:
            content = "复述文本"

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    registry = _make_registry()
    import asyncio

    asyncio.run(_register_model(registry))
    gateway = ModelGateway(registry)

    rf = {"type": "json_object", "schema": {"type": "array"}}
    asyncio.run(gateway.complete("reg_model_default", _messages(), response_format=rf))

    assert received["response_format"] == rf
    assert received["enable_json_schema_validation"] is True


def test_empty_completion_is_a_retryable_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """空文本 → GenerationFailure(retryable=True)。"""
    import litellm

    async def fake_completion(**kwargs: object) -> object:
        class _Msg:
            content = "   "

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()

    monkeypatch.setattr(litellm, "acompletion", fake_completion)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    registry = _make_registry()
    import asyncio

    asyncio.run(_register_model(registry))
    gateway = ModelGateway(registry)

    with pytest.raises(GenerationFailure) as exc_info:
        asyncio.run(gateway.complete("reg_model_default", _messages()))

    assert exc_info.value.retryable is True


def test_litellm_error_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    """鉴权错误 → retryable=False；超时/限流 → retryable=True。"""
    import litellm

    async def auth_error(**kwargs: object) -> object:
        raise litellm.AuthenticationError("Invalid API Key", "dashscope/qwen3.8-flash", "llm")

    async def timeout_error(**kwargs: object) -> object:
        raise litellm.Timeout("timed out", "dashscope/qwen3.8-flash", "llm")

    monkeypatch.setattr(litellm, "acompletion", auth_error)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    registry = _make_registry()
    import asyncio

    asyncio.run(_register_model(registry))
    gateway = ModelGateway(registry)

    with pytest.raises(GenerationFailure) as exc_info:
        asyncio.run(gateway.complete("reg_model_default", _messages()))
    assert exc_info.value.retryable is False

    monkeypatch.setattr(litellm, "acompletion", timeout_error)
    with pytest.raises(GenerationFailure) as exc_info:
        asyncio.run(gateway.complete("reg_model_default", _messages()))
    assert exc_info.value.retryable is True
