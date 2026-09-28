import asyncio
import re
import time
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
        feature_fields="- acceptance（验收标准，acceptance，可多条，必填）",
        section_slots="- s8.performance（性能，章节槽）",
        seat_catalog="- seat.data_scale · 数据规模与性能 · 管：量级与耗时 · 常驻",
        schema_json="{}",
    )
    assert "# 文档" in text
    assert "想做个导出" in text
    assert "seat.data_scale" in text  # 席位目录要真的落进提示词
    assert "s8.performance" in text  # 章节槽也要真的落进提示词
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
        raise RuntimeError("invalid api key")  # 非抖动错误：立刻失败，不许重试

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
    assert "invalid api key" in exc.value.details["cause"]
    assert exc.value.details["attempts"] == 1


async def test_gateway_retries_transient_errors_then_succeeds(monkeypatch):
    """连接抖动 / 5xx / 限流要重试（真模型冒烟时被一次 connection error 打断过整轮）。"""

    calls = {"count": 0}
    slept: list[float] = []

    async def flaky(**kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("Connection error.")

        class _Message:
            content = '{"ok": true}'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "m"
            usage = {}

        return _Response()

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(
        "pmstudio.infrastructure.llm.litellm_gateway.acompletion", flaky
    )
    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.asyncio.sleep", fake_sleep)
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
    assert calls["count"] == 2
    assert slept == [1.0]


async def test_gateway_gives_up_after_max_attempts_on_transient_errors(monkeypatch):
    calls = {"count": 0}

    async def always_flaky(**kwargs):
        calls["count"] += 1
        raise RuntimeError("503 Service Unavailable")

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "pmstudio.infrastructure.llm.litellm_gateway.acompletion", always_flaky
    )
    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.asyncio.sleep", fake_sleep)
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
    assert calls["count"] == 3
    assert exc.value.details["attempts"] == 3
    assert "重试 2 次后仍失败" in exc.value.message


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


# —— P5c Task 1：超时 ——


async def test_gateway_passes_timeout_and_disables_sdk_retries(monkeypatch):
    """两条都必须传：只传 timeout 会被 SDK 的内部重试放大墙钟（P5c §0.1 实测 4 倍）。"""

    captured: dict = {}

    async def scripted(**kwargs):
        captured.update(kwargs)

        class _Message:
            content = "{}"

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "m"
            usage = {}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", scripted)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None, timeout=7.5)

    await gateway.complete(
        ModelRequest(
            purpose="claims",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="ClaimsPacket",
            response_schema={"type": "object"},
        )
    )

    assert captured["timeout"] == 7.5
    assert captured["num_retries"] == 0


async def _accept_and_hang(reader, writer):  # type: ignore[no-untyped-def]
    """静默服务端：接上就不说话，等客户端自己断开（超时后 httpx 会断）。"""

    try:
        await reader.read()
    finally:
        writer.close()


async def test_gateway_honours_the_timeout_against_a_silent_provider():
    """真 HTTP、真 litellm：1 秒的超时必须在 3 秒内抛出来，不许被放大。

    边界怎么定的：直接量过"只传 timeout、不关 SDK 重试"的样子是 timeout 的约 4 倍
    （P5c §0.1：timeout=2 → 7.8 秒）。这里 timeout=1，正常的 1.1 秒左右结束，
    放大版要到 4 秒上下——3 秒这条线两边都不蹭。
    """

    server = await asyncio.start_server(_accept_and_hang, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    gateway = LiteLLMGateway(
        model="openai/x",
        api_key="k",
        api_base=f"http://127.0.0.1:{port}/v1",
        timeout=1.0,
        max_attempts=1,
        backoff=(0.0,),
    )

    start = time.monotonic()
    try:
        with pytest.raises(ModelCallFailed) as exc:
            await gateway.complete(
                ModelRequest(
                    purpose="claims",
                    messages=(ModelMessage(role="user", content="x"),),
                    schema_name="ClaimsPacket",
                    response_schema={"type": "object"},
                )
            )
    finally:
        server.close()
        await server.wait_closed()

    assert time.monotonic() - start < 3.0
    assert exc.value.details["timeout_seconds"] == 1.0
    assert exc.value.details["purpose"] == "claims"
