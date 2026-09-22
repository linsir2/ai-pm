import re
from pathlib import Path

import pytest

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.errors import ModelCallFailed
from pmstudio.application.ports import ModelMessage, ModelRequest
from pmstudio.infrastructure.llm.litellm_gateway import LiteLLMGateway
from pmstudio.infrastructure.llm.prompt_library import FilePromptLibrary

PROMPTS = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "pmstudio"
    / "infrastructure"
    / "llm"
    / "prompts"
)


def test_prompt_library_renders_slots_and_reports_missing():
    library = FilePromptLibrary(PROMPTS)
    text = library.render(
        "analyzer",
        document_state="# 文档",
        user_input="想做个导出",
        labels="- need（需求）：要做出来的能力",
        sections="- s5 功能需求",
        schema_json="{}",
    )
    assert "# 文档" in text
    assert "想做个导出" in text
    with pytest.raises(KeyError):
        library.render("analyzer", document_state="# 文档")  # 少给槽位要炸，不许静默留空


def test_prompt_library_rejects_unknown_prompt_name():
    library = FilePromptLibrary(PROMPTS)
    with pytest.raises(FileNotFoundError):
        library.render("nope")


def test_every_prompt_placeholder_is_declared_by_its_agent():
    """提示词文件与智能体声明的槽位必须一一对应（防漂移的机械手段）。"""

    expected = {
        "analyzer": ClaimAnalyzer.PROMPT_SLOTS,
        "seat_review": SeatReviewer.PROMPT_SLOTS,
        "integrator": Integrator.PROMPT_SLOTS,
    }
    for name, slots in expected.items():
        text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
        found = set(re.findall(r"\$([a-z_]+)", text))
        assert found == set(slots), f"{name}.md 的槽位与智能体声明不一致"


def test_prompts_do_not_ask_for_chain_of_thought():
    """提示词不许要求模型输出推理过程（界面只展示结构化产出，不展示思维链）。"""

    for name in ("analyzer", "seat_review", "integrator"):
        text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
        assert "思维链" not in text
        assert "一步步思考" not in text


async def test_gateway_passes_model_key_base_and_schema(monkeypatch):
    captured: dict = {}

    async def scripted_acompletion(**kwargs):
        captured.update(kwargs)

        class _Message:
            content = '{"ok": true}'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "provider-model"
            usage = {"total_tokens": 12}

        return _Response()

    monkeypatch.setattr(
        "pmstudio.infrastructure.llm.litellm_gateway.acompletion", scripted_acompletion
    )
    gateway = LiteLLMGateway(model="openai/gpt-4o-mini", api_key="k", api_base="http://x")
    reply = await gateway.complete(
        ModelRequest(
            purpose="claims",
            messages=(ModelMessage(role="user", content="你好"),),
            schema_name="ClaimsPacket",
            response_schema={"type": "object"},
        )
    )

    assert reply.text == '{"ok": true}'
    assert captured["model"] == "openai/gpt-4o-mini"
    assert captured["api_key"] == "k"
    assert captured["api_base"] == "http://x"
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["name"] == "ClaimsPacket"
    assert captured["messages"] == [{"role": "user", "content": "你好"}]


async def test_gateway_strips_markdown_fence_around_json(monkeypatch):
    async def fenced(**kwargs):
        class _Message:
            content = '```json\n{"ok": true}\n```'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "m"
            usage = {}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", fenced)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)
    reply = await gateway.complete(
        ModelRequest(
            purpose="claims",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="ClaimsPacket",
            response_schema={"type": "object"},
        )
    )
    assert reply.text == '{"ok": true}'


async def test_gateway_turns_provider_errors_into_model_call_failed(monkeypatch):
    async def boom(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", boom)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)

    with pytest.raises(ModelCallFailed) as exc:
        await gateway.complete(
            ModelRequest(
                purpose="seat_review",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="SeatPacket",
                response_schema={"type": "object"},
            )
        )
    assert exc.value.details["purpose"] == "seat_review"
    assert "connection reset" in exc.value.details["cause"]


async def test_gateway_rejects_content_that_is_not_json(monkeypatch):
    async def prose(**kwargs):
        class _Message:
            content = "我觉得这个功能应该……"

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "m"
            usage = {}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", prose)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)

    with pytest.raises(ModelCallFailed) as exc:
        await gateway.complete(
            ModelRequest(
                purpose="claims",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="ClaimsPacket",
                response_schema={"type": "object"},
            )
        )
    assert "JSON" in exc.value.message
