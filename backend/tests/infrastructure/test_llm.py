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


class BadRequestError(Exception):
    """名字与 litellm 那个一致：`is_transient()` 会看 `type(error).__name__`。"""


#: 2026-10-03 供应商**原文**（DashScope 间歇性拒收 `json_schema`，见 `04` G10 M0）。
STRUCTURED_OUTPUT_REFUSAL = (
    "<400> InternalError.Algo.InvalidParameter: "
    "This response_format type is unavailable now"
)

#: 2026-10-02 另一条**真的** 400：`glm-5.3` 与 `.env` 里的 `enable_thinking:false` 互斥。
#: 它必须**继续**被判成不可重试——闸门正是靠这条把"换模型忘了改参数"当场拦下来的。
MODEL_PARAM_MISMATCH = (
    "<400> InternalError.Algo.InvalidParameter: "
    "The value of the enable_thinking parameter is restricted to True."
)


def _ok_response():
    """一次正常回包（只用来让调用成功，内容不重要）。"""

    class _Message:
        content = '{"ok": true}'

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]
        model = "m"
        usage = {}

    return _Response()


def _response_formats(seen: list[dict]) -> list[str]:
    """把每次请求带上去的 `response_format.type` 摘出来（None = 没带）。"""

    return [
        (item.get("response_format") or {}).get("type") for item in seen
    ]

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
        # 工具层 C-2 起，analyzer 多一个外部材料槽（提示词里那节是新的）
        external_material="（这一轮没有外部材料）",
        schema_json="{}",
        validation_error="",
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
        "opinion_dispositions": Integrator.DISPOSITIONS_SLOTS,
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


def test_analyzer_prompt_pins_the_two_raw_span_pitfalls():
    """提示词要把真模型踩过的两个坑写进去（`analyzer.md` 是复述这条口径的第一入口）。

    ① 不许用省略号把分开的两段拼成一条复述；
    ② 没有原话就不许写——「(隐含于上下文，仅提及订单)」这种**说明**被当原话填过一次，
       它比省略号拼段严重得多：那才是真编造。
    ③ 席位 id 要连 `seat.` 前缀一起照抄（真模型写过裸名 `tech_approach`，整轮被判死）。
    """

    text = (PROMPTS / "analyzer.md").read_text(encoding="utf-8")

    assert "省略号" in text
    assert "隐含于上下文" in text
    assert "连续" in text
    assert "`seat.` 前缀" in text


def test_the_conflict_rule_covers_contradictions_inside_the_round():
    """`conflict` 判据要覆盖"本轮输入内部互斥"，并给正反例（报告 F7）。

    真跑里三批 15 道题 `kind` 全是 `gap`：提示词只写了"与**文档既有结论**冲突时 kind=conflict"，
    最常发生的那种（同一轮输入里两句互斥，如"要 100 万行"+"必须 3 秒内"）没写进去。
    另一半同样重要：**只是"还没定"不算矛盾**，别把 gap 全标成 conflict（前端把它们分开显示）。
    """

    from pmstudio.application.agents.integrator import LIMITS_TEXT

    assert "本轮输入" in LIMITS_TEXT
    assert "互斥" in LIMITS_TEXT
    assert "不算矛盾" in LIMITS_TEXT  # 反例：还没定 = gap
    assert "两侧" in LIMITS_TEXT  # 矛盾题要说清是哪两侧，选项才写得出来


def test_analyzer_prompt_gives_the_end_to_end_flow_a_home():
    """跨功能/端到端的流程与异常路径归 **s6**——归位表里要有这一行。

    真跑现场（P30 五条车道 14 个轮次、449 条提案）：`kind=steps` 有 13 条，
    **13 条全落在 `s5/*.main_flow`**；落 `s6` 的只有 1 条、而且还是 `text`。
    归位表原来把"功能做法（主流程…）"整行推给 s5，**没有任何一行提到 s6**，
    于是第 6 章在五条车道上永远是空的（`readiness` 挂着 warn、导出写"（本章为空）"）。
    """

    text = (PROMPTS / "analyzer.md").read_text(encoding="utf-8")

    assert "s6" in text
    assert "端到端" in text
    # 与功能内主流程的分工要写清，否则模型会把每个功能的流程抄进 s6
    assert "main_flow" in text


def test_integrator_limits_tell_how_to_write_the_end_to_end_flow():
    """整合者要知道 s6 怎么写：章节级 + `kind=steps` + 一条提案写完整条流程。"""

    from pmstudio.application.agents.integrator import LIMITS_TEXT

    assert "s6" in LIMITS_TEXT
    assert "steps" in LIMITS_TEXT
    assert "端到端" in LIMITS_TEXT
    assert "main_flow" in LIMITS_TEXT  # 功能内部流程仍写 s5
    assert "不许编" in LIMITS_TEXT  # 依据里看不出来的一步不写，写不全就空着 / 出题
    # P36 真跑：模型把 s6 的流程**写成 text** 过 4/7 —— 这一句钉住"kind 必须是 steps"
    assert "kind 必须写 steps" in LIMITS_TEXT


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


def test_is_transient_covers_the_structured_output_refusal():
    """供应商说"**现在**这个 response_format 类型不可用"——那是抖动，值得重试。

    真实现场（2026-10-03 08:15–08:24）：DashScope 在两分钟窗口里对 `json_schema` 连回 400，
    08:17 又全通、08:20 连打 8/8 过；08:24 打到一次真实 analyzer 调用 → **整轮失败**。
    网关原来把 `invalid_parameter` 一律当不可重试，于是一次两分钟的抖动 = 一次调用判死。
    """

    from pmstudio.infrastructure.llm.litellm_gateway import is_transient

    assert is_transient(BadRequestError(STRUCTURED_OUTPUT_REFUSAL))


def test_is_transient_still_rejects_a_real_model_param_mismatch():
    """**别放宽大类**：参数真的写错了要立刻失败（闸门就靠这条把"换模型忘了改参数"拦下来）。"""

    from pmstudio.infrastructure.llm.litellm_gateway import is_transient

    assert not is_transient(BadRequestError(MODEL_PARAM_MISMATCH))
    assert not is_transient(BadRequestError("InvalidParameter: field foo not allowed"))
    assert not is_transient(RuntimeError("invalid api key"))


async def test_gateway_rides_out_one_structured_output_refusal(monkeypatch):
    """端到端：第一次回那条 400，第二次正常——**不该把整轮判死**。

    **2026-10-03 改过一处断言（说清错在哪）**：原来断言 `slept == [1.0]`，
    那是在钉**机制**（"走抖动重试 + 退避"），不是钉**行为**。P34 · M2b 之后，
    这条 400 先由**回落**接住（立刻换 `json_object` 重发，不退避）——行为比原来更好，
    所以那一行不成立了。行为本身（"一次拒收不判死、第二次要成功"）**一个字没放松**：
    改动是加了一条更精确的断言——第二次请求真的换成了 `json_object`。
    抖动退避那条路**没有被删掉**，它的覆盖挪到了下面那条用例
    （`test_gateway_backs_off_when_the_refusal_keeps_coming`）。
    """

    seen: list[dict] = []

    async def flaky(**kwargs):
        seen.append(dict(kwargs))
        if len(seen) == 1:
            raise BadRequestError(STRUCTURED_OUTPUT_REFUSAL)
        return _ok_response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", flaky)
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
    assert _response_formats(seen) == ["json_schema", "json_object"]


async def test_gateway_backs_off_when_the_refusal_keeps_coming(monkeypatch):
    """回落之后**还**被拒 → 回到抖动那条路：退避 1s / 3s，三次都不行才失败。

    这条把 M0 那条用例的"退避"覆盖接过来——**两次机会都要在**：
    先试换写法（回落），换完还不行再按抖动重试。
    """

    calls = {"count": 0}
    slept: list[float] = []

    async def always_refused(**kwargs):
        calls["count"] += 1
        raise BadRequestError(STRUCTURED_OUTPUT_REFUSAL)

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(
        "pmstudio.infrastructure.llm.litellm_gateway.acompletion", always_refused
    )
    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.asyncio.sleep", fake_sleep)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)

    with pytest.raises(ModelCallFailed):
        await gateway.complete(
            ModelRequest(
                purpose="claims",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="ClaimsPacket",
                response_schema={"type": "object"},
            )
        )

    # 第 1 次 json_schema → 回落那次 json_object → 第 2、3 次都是 json_object
    assert calls["count"] == 4
    assert slept == [1.0, 3.0]


async def test_gateway_falls_back_to_json_object_when_json_schema_is_refused(monkeypatch):
    """供应商**稳定**不吃 `json_schema` 时回落 `json_object`（P34 · M2b）。

    真实现场：`openai/deepseek-v4.1-flash` 对 `json_schema` 连续 400
    （`This response_format type is unavailable now`），同模型 `json_object` 正常。
    产品每次调用都带 `json_schema` → 那个模型上**一次都发不出去**。

    契约不变：回包照样过我们自己的 schema 校验，回落只是"请求怎么写"这一层的事。
    """

    seen: list[dict] = []

    async def provider(**kwargs):
        seen.append(dict(kwargs))
        if (kwargs.get("response_format") or {}).get("type") == "json_schema":
            raise BadRequestError(STRUCTURED_OUTPUT_REFUSAL)
        return _ok_response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", provider)
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
    assert _response_formats(seen) == ["json_schema", "json_object"]


async def test_gateway_remembers_the_fallback_for_later_calls(monkeypatch):
    """拒过一次之后，**后续调用直接**用 `json_object`——别再每次先撞一次 400。"""

    seen: list[dict] = []

    async def provider(**kwargs):
        seen.append(dict(kwargs))
        if (kwargs.get("response_format") or {}).get("type") == "json_schema":
            raise BadRequestError(STRUCTURED_OUTPUT_REFUSAL)
        return _ok_response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", provider)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)
    request = ModelRequest(
        purpose="claims",
        messages=(ModelMessage(role="user", content="x"),),
        schema_name="ClaimsPacket",
        response_schema={"type": "object"},
    )

    await gateway.complete(request)
    seen.clear()
    await gateway.complete(request)

    assert _response_formats(seen) == ["json_object"], "第二次调用不该再先试 json_schema"


async def test_gateway_does_not_fall_back_for_other_bad_requests(monkeypatch):
    """**别的** 400 不许被回落吞掉（参数真写错了就要立刻失败）。"""

    seen: list[dict] = []

    async def provider(**kwargs):
        seen.append(dict(kwargs))
        raise BadRequestError(MODEL_PARAM_MISMATCH)

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", provider)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)

    with pytest.raises(ModelCallFailed):
        await gateway.complete(
            ModelRequest(
                purpose="claims",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="ClaimsPacket",
                response_schema={"type": "object"},
            )
        )

    assert _response_formats(seen) == ["json_schema"], "只该请求一次，且不该换成 json_object"


async def test_gateway_records_both_the_refusal_and_the_fallback(monkeypatch):
    """留样口径不变：那条 400 记一行（ok=0），回落成功再记一行（ok=1）。"""

    async def provider(**kwargs):
        if (kwargs.get("response_format") or {}).get("type") == "json_schema":
            raise BadRequestError(STRUCTURED_OUTPUT_REFUSAL)
        return _ok_response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", provider)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    await gateway.complete(
        ModelRequest(
            purpose="integration",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="IntegrationPacket",
            response_schema={"type": "object"},
        )
    )

    assert [row["ok"] for row in recorder.rows] == [False, True]
    assert "response_format type is unavailable" in recorder.rows[0]["error"]


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


async def test_gateway_records_every_attempt_including_the_failed_one(monkeypatch):
    """留样要能看到**每一次尝试**（报告 §四）。

    120.4 秒那一次是"超时 90 → 退避 → 重试成功"，只有最后成功那次被记下来，
    于是"刚才为什么慢/为什么重试"在 `model_calls` 里一行都查不到。
    """

    calls = {"count": 0}

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
        return None

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", flaky)
    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.asyncio.sleep", fake_sleep)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    await gateway.complete(
        ModelRequest(
            purpose="claims",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="SeatPacket",
            response_schema={"type": "object"},
        )
    )

    assert [(row["attempt"], row["ok"], bool(row["error"])) for row in recorder.rows] == [
        (1, False, True),
        (2, True, False),
    ]
    assert "Connection error" in recorder.rows[0]["error"]
    assert recorder.rows[1]["reply_text"] == '{"ok": true}'


async def test_gateway_records_a_reply_that_is_not_json(monkeypatch):
    """回包不是 JSON 这条路原来**一行留痕都没有**：错误只剩"契约被破坏"，原文查不到。

    P35 真踩到两次（同一个模型、两条不同用例，各死在一次调用上：整合 / 席位），
    当时留样是关的，连"模型到底回了什么"都补不回来。留样开着就得看得见原文（取头部）。
    还要看得见 `usage` 与 `finish_reason`：真跑那次回包**是截断的 JSON**（尾部断在半句上），
    没有 `finish_reason=length` 就只能猜。
    """

    async def prose(**kwargs):
        class _Message:
            content = "好的，我来分析一下这个需求：\n1. 点检记录提交后不许改……"

        class _Choice:
            message = _Message()
            finish_reason = "length"

        class _Response:
            choices = [_Choice()]
            model = "m"
            usage = {"completion_tokens": 8192}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", prose)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    with pytest.raises(ModelCallFailed):
        await gateway.complete(
            ModelRequest(
                purpose="integration",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="IntegrationPacket",
                response_schema={"type": "object"},
            )
        )

    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert row["ok"] is False
    assert row["purpose"] == "integration"
    assert row["reply_text"].startswith("好的，我来分析")
    assert "不是合法 JSON" in row["error"]
    assert "finish_reason=length" in row["error"]
    assert row["usage"] == {"completion_tokens": 8192}


async def test_gateway_records_an_empty_reply(monkeypatch):
    """空回包同理：原来也是不落库的（"模型返回了空内容"只有一句话）。"""

    async def blank(**kwargs):
        class _Message:
            content = "   "

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "m"
            usage = {}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", blank)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    with pytest.raises(ModelCallFailed):
        await gateway.complete(
            ModelRequest(
                purpose="seat_review",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="SeatPacket",
                response_schema={"type": "object"},
            )
        )

    assert [(row["ok"], row["reply_text"]) for row in recorder.rows] == [(False, "")]
    assert "空内容" in recorder.rows[0]["error"]


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


async def test_gateway_hands_the_model_a_hint_when_the_reply_is_not_json(monkeypatch):
    """P13 真跑抓到：非 JSON 原来被当成"网关类"、不带提示——可那恰恰是**模型自己能修**的一类。

    带了 hint，重试时它就会变成第一次尝试的 `validation_error`（不再盲调）。
    """

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

    assert exc.value.hint is not None
    assert "不是合法 JSON" in exc.value.hint
    assert "不要代码块围栏" in exc.value.hint


async def test_gateway_hands_no_hint_when_the_provider_itself_failed(monkeypatch):
    """供应商那类（超时 / 5xx）：递进去没用，所以不给 hint。"""

    async def boom(**kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", boom)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None, max_attempts=1)

    with pytest.raises(ModelCallFailed) as exc:
        await gateway.complete(
            ModelRequest(
                purpose="claims",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="ClaimsPacket",
                response_schema={"type": "object"},
            )
        )

    assert exc.value.hint is None


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


async def test_gateway_passes_model_params_but_never_lets_them_win(monkeypatch):
    """供应商参数（如 `extra_body.enable_thinking=false`）原样传下去，
    但**我们自己的键永远赢**：timeout / num_retries / model / messages 不许被配置改掉。"""

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
    gateway = LiteLLMGateway(
        model="m",
        api_key="k",
        timeout=30.0,
        model_params={
            "extra_body": {"enable_thinking": False},
            "timeout": 999,  # 想覆盖我们的墙钟：不许
            "num_retries": 5,  # 想打开 SDK 重试：不许
        },
    )

    await gateway.complete(
        ModelRequest(
            purpose="claims",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="ClaimsPacket",
            response_schema={"type": "object"},
        )
    )

    assert captured["extra_body"] == {"enable_thinking": False}
    assert captured["timeout"] == 30.0
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


class _RecordingRecorder:
    """测试替身：只记下被交上来的那一行。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def record(self, **row) -> None:  # type: ignore[no-untyped-def]
        self.rows.append(row)


class _NotJsonSerializable:
    """litellm 的 usage 里真有这种东西（`CompletionTokensDetailsWrapper`）。"""

    def __repr__(self) -> str:
        return "<wrapper 12>"


async def test_gateway_records_usage_that_is_not_json_serializable(monkeypatch):
    """真模型实测：usage 里是 pydantic 包装对象，直接 json.dumps 会炸。

    留样是旁路，**不许把这一轮搞失败**；转成能写的形状（字符串）记下来就行。
    """

    async def scripted(**kwargs):
        class _Message:
            content = '{"ok": true}'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "provider-model"
            usage = {"total_tokens": 7, "completion_tokens_details": _NotJsonSerializable()}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", scripted)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    reply = await gateway.complete(
        ModelRequest(
            purpose="claims",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="ClaimsPacket",
            response_schema={"type": "object"},
        )
    )

    assert reply.text == '{"ok": true}'
    assert recorder.rows[0]["usage"] == {
        "total_tokens": 7,
        "completion_tokens_details": "<wrapper 12>",
    }


async def test_gateway_records_the_call_when_a_recorder_is_given(monkeypatch):
    """留样：谁、哪一轮、提示词原文、回复、用量、耗时（`02` §5.3）。"""

    async def scripted(**kwargs):
        class _Message:
            content = '{"ok": true}'

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]
            model = "provider-model"
            usage = {"total_tokens": 7}

        return _Response()

    monkeypatch.setattr("pmstudio.infrastructure.llm.litellm_gateway.acompletion", scripted)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    await gateway.complete(
        ModelRequest(
            purpose="seat_review",
            messages=(ModelMessage(role="user", content="提示词"),),
            schema_name="SeatPacket",
            response_schema={"type": "object"},
            metadata={"round_id": "r1", "seat_id": "seat.data_scale"},
        )
    )

    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert (row["round_id"], row["purpose"], row["model"]) == (
        "r1",
        "seat_review",
        "provider-model",
    )
    assert row["reply_text"] == '{"ok": true}'
    assert row["prompt_text"] == "提示词"
    assert row["usage"] == {"total_tokens": 7}
    assert row["duration_ms"] >= 0
