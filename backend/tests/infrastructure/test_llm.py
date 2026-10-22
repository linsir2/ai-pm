import asyncio
import re
import time

import pytest

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.discusser import Discusser
from pmstudio.application.agents.inquirer import Inquirer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.memory_curator import MemoryCurator
from pmstudio.application.agents.persona_simulator import PersonaSimulatorAgent
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.agents.sim_inquirer import SimInquiryAgent
from pmstudio.harness.errors import ModelCallFailed
from pmstudio.harness.model.contracts import ModelMessage, ModelRequest
from pmstudio.harness.model.failure import classify
from pmstudio.harness.model.providers import litellm as provider_module
from pmstudio.harness.model.providers.litellm import LiteLLMGateway
from pmstudio.harness.prompt import PROMPTS_DIR, FilePromptLibrary


class BadRequestError(Exception):
    """名字与 litellm 那个一致：`classify()` 会看 `type(error).__name__`。"""


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

#: 提示词目录**从它所属的模块引**，不在测试里重算：重算的话，搬目录时
#: 只有这一处会报错，其余按旧路径算的调用方会静默指向空目录。
PROMPTS = PROMPTS_DIR


def _analyzer_prompt_text() -> str:
    """**渲染后**的分析者提示词（.md 骨架 + 各槽内容）。

    2026-10-15（`02` §11.0.69）：逐章归位表从 `.md` 静态文件搬进了 `$placement` 槽
    （由文档模板渲染）。守"提示词里必须有这条信号"的用例要看**渲染结果**——
    模型收到的是拼好的那段字，不是模板文件。
    """

    from pmstudio.application.context import placement_text, section_slots_text, sections_text
    from tests.support.builders import STD

    body = (PROMPTS / "analyzer.md").read_text(encoding="utf-8")
    return (
        body.replace("$placement", placement_text(STD))
        .replace("$sections", sections_text(STD))
        .replace("$section_slots", section_slots_text(STD))
    )


def test_prompt_library_renders_slots_and_reports_missing():
    library = FilePromptLibrary(PROMPTS)
    text = library.render(
        "analyzer",
        document_state="# 文档",
        user_input="想做个导出",
        labels="- need（需求）：要做出来的能力",
        sections="- s5 功能需求",
        placement="- s5 功能需求：每个功能一棵子树",
        feature_fields="- acceptance（验收标准，acceptance，可多条，必填）",
        section_slots="- s8.performance（性能，章节槽）",
        seat_catalog="- seat.data_scale · 数据规模与性能 · 管：量级与耗时 · 常驻",
        # 工具层 C-2 起，analyzer 多一个外部材料槽（提示词里那节是新的）
        external_material="（这一轮没有外部材料）",
        project_context="（这个项目还没有构建上下文）",
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
        "analyzer": ClaimAnalyzer.definition.slots,
        "seat_review": SeatReviewer.definition.slots,
        "inquiry": Inquirer.definition.slots,
        "sim_inquiry": SimInquiryAgent.definition.slots,
        "sim_review": PersonaSimulatorAgent.definition.slots,
        "integrator": Integrator.definition.slots,
        "opinion_dispositions": Integrator.DISPOSITIONS_SLOTS,
        "memory_curator": MemoryCurator.definition.slots,
        "discussion_say": Discusser.definition.slots,
        "discussion_cards": Discusser.cards_definition.slots,
    }
    for name, slots in expected.items():
        text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
        found = set(re.findall(r"\$([a-z_]+)", text))
        assert found == set(slots), f"{name}.md 的槽位与智能体声明不一致"


def test_prompts_lead_with_what_the_agent_must_process():
    """位置感知（`07-记忆层.md` §7.5 ④）：承重块（本轮要处理的）排在参考块之前。

    **2026-10-10 收窄**：原断言还有一句 `integrator[-1] == "schema_json"`
    （④ 的"输出格式收尾"）。它被 `02 §11.0.55` 的缓存纪律顶掉了——静态块前移之后
    schema 在头部、`validation_error` 落末位，顺序契约改由
    `tests/contracts/test_prompt_slot_order.py` 守。

    换掉它的依据（都是当天实测，不是推理）：
    - 供应商按**前缀**命中缓存；旧布局下整合者可缓存前缀只有 83 个字符，
      而整份提示词 20,016 字里有 8,816 字（44%）是静态块；
    - 静态块前移后 eval **7/10 → 8/10**（失败集是原样的两个已知抖法），无回退。
    承重块优先这一半④仍然成立，下面两条继续钉着。
    """

    integrator = re.findall(
        r"\$([a-z_]+)", (PROMPTS / "integrator.md").read_text(encoding="utf-8")
    )
    assert integrator.index("claims") < integrator.index("document_state")
    assert integrator.index("opinions") < integrator.index("document_state")

    seat = re.findall(
        r"\$([a-z_]+)", (PROMPTS / "seat_review.md").read_text(encoding="utf-8")
    )
    assert seat.index("claims") < seat.index("document_state")
    assert seat.index("opinions_so_far") < seat.index("document_state")
    assert seat[-1] == "validation_error"


def test_prompts_do_not_ask_for_chain_of_thought():
    """提示词不许要求模型输出推理过程（界面只展示结构化产出，不展示思维链）。"""

    for name in ("analyzer", "seat_review", "integrator"):
        text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
        assert "思维链" not in text
        assert "一步步思考" not in text


def test_seat_and_integrator_prompts_carry_the_memory_rule():
    """M6c：席位与整合者都要看得见记忆那一节，并且都写着"冲突以正文为准"。"""

    for name in ("seat_review", "integrator"):
        text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
        assert "$project_memory" in text, name
        assert "以正文为准" in text, name


def test_memory_curator_prompt_shows_a_complete_example_with_refs():
    """M6d：真跑第一趟的失败形状（整批没写出处）要靠提示词顶住——
    提示词里必须有一个**带 refs 的完整 JSON 例子**，且 `refs` 这个词是硬要求。"""

    text = (PROMPTS / "memory_curator.md").read_text(encoding="utf-8")

    assert '"refs": ["E7", "E9"]' in text      # 完整例子带出处
    assert "`refs` 不许省" in text              # 而且写成硬话，不是"最好带上"


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

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

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

    text = _analyzer_prompt_text()

    assert "s6" in text
    assert "端到端" in text
    # 与功能内主流程的分工要写清，否则模型会把每个功能的流程抄进 s6
    assert "main_flow" in text


def test_analyzer_prompt_marks_the_walkthrough_as_main_flow():
    """归位表要把"功能怎么走一遍"指到 **s5 + feature_slug + `field: main_flow`**。

    真跑相关性（跨全部真跑库、按轮统计）：主张里标了 `field: main_flow` 时，
    整合者 **16/18 = 89%** 会写出主流程；没标时只有 **16/71 = 23%**。
    L5（社区食堂）两轮 40 条主张里一条都没标 → 62 条提案里 0 条主流程。
    P38 就是把这条信号补上（只加提示词，不动 `readiness`）。
    """

    text = _analyzer_prompt_text()

    assert "field: main_flow" in text
    assert "怎么走一遍" in text


def test_integrator_limits_tell_how_to_write_the_end_to_end_flow():
    """整合者要知道 s6 怎么写：章节级 + `kind=steps` + 一条提案写完整条流程。"""

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    assert "s6" in LIMITS_TEXT
    assert "steps" in LIMITS_TEXT
    assert "端到端" in LIMITS_TEXT
    assert "main_flow" in LIMITS_TEXT  # 功能内部流程仍写 s5
    # 2026-10-10 第 3 项：祈使否定改正面写法，这句的措辞随之换成"找依据"
    # （规则本身一个字没丢：找得到才写，找不到就出题问用户，或者空着）
    assert "找依据" in LIMITS_TEXT
    assert "或者空着" in LIMITS_TEXT
    # P36 真跑：模型把 s6 的流程**写成 text** 过 4/7 —— 这一句钉住"kind 必须是 steps"
    assert "kind 必须写 steps" in LIMITS_TEXT
    # P36 二次真跑：分析者把端到端流程派给了 s6，整合者却**一条 s6 提案都没出**（各功能已覆盖）
    assert "必须出一条 s6 的 steps 提案" in LIMITS_TEXT
    assert "串起来不算编造" in LIMITS_TEXT


def test_integrator_limits_bound_the_output_per_call_and_keep_the_reasoning_out_of_the_text():
    """输出侧口径两件事（P37 · 量出来的）。

    ① **条数上限是"一次整合"的，不是一轮的**。真跑一轮会整合 2–3 次，累计 93–103 条提案
       （M4 两轮真跑实测），所以原来那句"一轮最多 60 条"跟实际执行对不上。
       （2026-10-22 批 14：那个 60 已按**输出预算**改成 21——`max_tokens` 之前**根本没设**
       → 撞供应商默认 → 60 条装不下 → 17% 的调用事后被截断、最多丢 16 条提案。）
    ② 超限的**后果**要说清：真跑 201 次成功整合里 **17% 的调用写了 >60 条**（最多 90），
       系统先让模型精简一次（约 59% 能修好），其余**真截断**——真跑里最多一次丢掉 16 条提案
       （有留痕，但用户拿不到）。
    ③ **解释别写进正文**：回包体量≈内容体量（中位 17.9K 字符 ≈ 44 秒一次），
       而每条提案的解释在草案卡上本来就有落点（`rationale` → "它为什么这么判断：…"）。
    """

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    from pmstudio.application.agents.integrator import INTEGRATION_MAX_PROPOSALS

    assert f"一次整合最多 {INTEGRATION_MAX_PROPOSALS} 条" in LIMITS_TEXT
    assert f"一轮最多 {INTEGRATION_MAX_PROPOSALS} 条" not in LIMITS_TEXT
    assert "截断" in LIMITS_TEXT  # 超了会发生什么，得让模型知道
    assert "rationale" in LIMITS_TEXT  # 解释去哪儿


def test_integrator_limits_ask_for_a_main_flow_per_feature():
    """每个功能的正常路径要有一条 `main_flow`——**看不出来就出题，别硬编**。

    真跑实测（当前模型 + 当前提示词，三条车道各两轮）：L1 **4/4**、L2 **4/8**、L5 **0/1**
    （合计 8/13 ≈ 62%）；历史 45 份导出合计 48%。而 `main_flow` 在实现里是**非必填**
    （`产品设计说明书` §2.6 写的是必填），所以这些缺口既不挡交付、也没人说。

    处置：**不升必填**——那会把"模型没写"变成"交不出去"（P30 里好不容易到 0 阻塞的车道会立刻破），
    而正确的杠杆是**派活**（与 s6 同一个套路）。
    """

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    assert "每个功能的正常路径要有一条" in LIMITS_TEXT
    assert "main_flow" in LIMITS_TEXT


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

    monkeypatch.setattr(provider_module, "acompletion", scripted_acompletion)
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

    monkeypatch.setattr(provider_module, "acompletion", fenced)
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


def test_the_retry_verdict_covers_the_structured_output_refusal():
    """供应商说"**现在**这个 response_format 类型不可用"——那是抖动，值得重试。

    真实现场（2026-10-03 08:15–08:24）：DashScope 在两分钟窗口里对 `json_schema` 连回 400，
    08:17 又全通、08:20 连打 8/8 过；08:24 打到一次真实 analyzer 调用 → **整轮失败**。
    网关原来把 `invalid_parameter` 一律当不可重试，于是一次两分钟的抖动 = 一次调用判死。
    """

    assert classify(BadRequestError(STRUCTURED_OUTPUT_REFUSAL)).retryable


def test_the_retry_verdict_still_rejects_a_real_model_param_mismatch():
    """**别放宽大类**：参数真的写错了要立刻失败（闸门就靠这条把"换模型忘了改参数"拦下来）。"""

    assert not classify(BadRequestError(MODEL_PARAM_MISMATCH)).retryable
    assert not classify(BadRequestError("InvalidParameter: field foo not allowed")).retryable
    assert not classify(RuntimeError("invalid api key")).retryable


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

    monkeypatch.setattr(provider_module, "acompletion", flaky)
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

    monkeypatch.setattr(provider_module, "acompletion", always_refused)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None, sleeper=fake_sleep)

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
    # **需求变更（P2）**：等待值带抖动，不再是死的 [1.0, 3.0]——钉死它等于把
    # 重试风暴钉回来（所有调用方同一刻重试）。这里钉的是"落在基线 ±50% 且有分散"，
    # 性质由 test_gateway_resilience.py 覆盖，这里只确认确实退避过两次。
    assert len(slept) == 2
    assert all(abs(got - base) <= base * 0.5 for got, base in zip(slept, (1.0, 3.0), strict=True))


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

    monkeypatch.setattr(provider_module, "acompletion", provider)
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

    monkeypatch.setattr(provider_module, "acompletion", provider)
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

    monkeypatch.setattr(provider_module, "acompletion", provider)
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

    monkeypatch.setattr(provider_module, "acompletion", provider)
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

    monkeypatch.setattr(provider_module, "acompletion", boom)
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

    monkeypatch.setattr(provider_module, "acompletion", flaky)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None, sleeper=fake_sleep)

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
    # P2 起等待值带抖动，不再是死的 1.0（同一文件上面那条用例已经因为同样的理由
    # 改过一次断言——那是在钉机制不是钉行为）。这里钉"确实退避过一次"。
    assert len(slept) == 1
    assert abs(slept[0] - 1.0) <= 0.5


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

    monkeypatch.setattr(provider_module, "acompletion", flaky)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder, sleeper=fake_sleep)

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

    monkeypatch.setattr(provider_module, "acompletion", prose)
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

    monkeypatch.setattr(provider_module, "acompletion", blank)
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

    monkeypatch.setattr(provider_module, "acompletion", always_flaky)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None, sleeper=fake_sleep)

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

    monkeypatch.setattr(provider_module, "acompletion", prose)
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

    monkeypatch.setattr(provider_module, "acompletion", prose)
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

    monkeypatch.setattr(provider_module, "acompletion", boom)
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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
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


#: 预热那一把要回的最小 chat completion（OpenAI 兼容形状）。
_WARM_BODY = (
    b'{"id":"warm","object":"chat.completion","created":0,"model":"openai/x",'
    b'"choices":[{"index":0,"message":{"role":"assistant","content":"{}"},'
    b'"finish_reason":"stop"}]}'
)


async def _first_answers_then_hangs(state: list[int], reader, writer):  # type: ignore[no-untyped-def]
    """**第一次连接正常回一句，之后每次都静默挂起**。

    ## 为什么要有这"第一次"（2026-10-19 改）

    litellm 首次调用的懒加载 + httpx 客户端初始化，进程内**一次性**约 1.6 秒。
    冷启动直接量超时会把这笔一次性开销算进去：实测同一进程里
    第一次调 2.64 秒、第二次调 1.05 秒（`num_retries=0`），不关 SDK 重试则是 4.40 秒。
    于是"超时有没有被放大"这条判据被**冷启动噪声**盖住——阈值 3.0 秒卡在
    噪声和真值中间，测的是进程热不热，不是超时行为。

    这条测试此前一直是**假绿**：熔断器在 Python 3.13 上恒抛 NameError，
    网关把它转成 `ModelCallFailed` 瞬间返回，计时断言"通过"了——它从没真正
    量过超时。熔断器修好（`6b12021`）之后它第一次跑到真正的超时路径，
    才暴露出自己量错了东西。

    预热之后：正常 1.05 秒、放大版约 3.4 秒，**3.0 秒这条线仍然两边都不蹭**，
    判别力原样保留。
    """

    try:
        await reader.read()
        state[0] += 1
        if state[0] == 1:
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(_WARM_BODY)).encode()
                + b"\r\n\r\n"
                + _WARM_BODY
            )
            await writer.drain()
            return
    except Exception:  # noqa: BLE001 —— 预热失败就当它不存在，测的是超时那条
        pass
    finally:
        if state[0] > 1:
            writer.close()


async def test_gateway_honours_the_timeout_against_a_silent_provider():
    """真 HTTP、真 litellm：1 秒的超时必须在 3 秒内抛出来，不许被放大。

    边界怎么定的：直接量过"只传 timeout、不关 SDK 重试"的样子是 timeout 的约 4 倍
    （P5c §0.1：timeout=2 → 7.8 秒）。这里 timeout=1，正常的 1.1 秒左右结束，
    放大版要到 4 秒上下——3 秒这条线两边都不蹭。

    **量之前先预热一次**（见 `_first_answers_then_hangs`）：litellm/httpx 的
    进程内一次性初始化约 1.6 秒，不预热的话量的是冷启动而不是超时行为。
    """

    state = [0]
    server = await asyncio.start_server(
        lambda r, w: _first_answers_then_hangs(state, r, w), "127.0.0.1", 0
    )
    port = server.sockets[0].getsockname()[1]
    gateway = LiteLLMGateway(
        model="openai/x",
        api_key="k",
        api_base=f"http://127.0.0.1:{port}/v1",
        timeout=1.0,
        max_attempts=1,
        backoff=(0.0,),
    )

    # 预热：先跑一次**不计时的**调用（服务端第一次会正常回一句）。
    # litellm 首次调用的懒加载 + httpx 客户端初始化是进程内一次性开销（约 1.6 秒），
    # 不预热的话量的是冷启动而不是超时行为。
    try:
        await gateway.complete(
            ModelRequest(
                purpose="claims",
                messages=(ModelMessage(role="user", content="x"),),
                schema_name="ClaimsPacket",
                response_schema={"type": "object"},
            )
        )
    except BaseException:  # noqa: BLE001 —— 预热成功与否都不重要
        pass

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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
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
    assert (row["trace_id"], row["purpose"], row["model"]) == (
        "r1",
        "seat_review",
        "provider-model",
    )
    assert row["reply_text"] == '{"ok": true}'
    assert row["prompt_text"] == "提示词"
    assert row["usage"] == {"total_tokens": 7}
    assert row["duration_ms"] >= 0


async def test_gateway_links_an_exploration_call_by_exploration_id(monkeypatch):
    """M8.1：inquiry 的 metadata 只有 exploration_id——留样不能记成 NULL，否则按趟查不到。"""

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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    await gateway.complete(
        ModelRequest(
            purpose="inquiry",
            messages=(ModelMessage(role="user", content="提示词"),),
            schema_name="InquiryPacket",
            response_schema={"type": "object"},
            metadata={"exploration_id": "x-1"},
        )
    )

    assert len(recorder.rows) == 1
    assert recorder.rows[0]["trace_id"] == "x-1"


async def test_gateway_prefers_round_id_when_both_ids_are_present(monkeypatch):
    """两键都在时 round_id 赢——老调用的行为一字不动。"""

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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    await gateway.complete(
        ModelRequest(
            purpose="inquiry",
            messages=(ModelMessage(role="user", content="提示词"),),
            schema_name="InquiryPacket",
            response_schema={"type": "object"},
            metadata={"round_id": "r1", "exploration_id": "x-1"},
        )
    )

    assert len(recorder.rows) == 1
    assert recorder.rows[0]["trace_id"] == "r1"


def test_integrator_prompt_forbids_ledger_talk_in_open_item_titles():
    """未决清单那节要说清两件事（M7 A′ / learnings L13）。

    真跑现场：给整合者看的未决清单原来是裸的 `[r-… #open61]`，它把这串当"引用"抄进标题 →
    正文里出现 `（open61/open67/open112）`；它还会交代"本轮已出题（见题『…』）"这类处置说明，
    而**判重用的正是标题**——于是同一件事每轮换一个说法，判重抓不住。
    """

    text = (PROMPTS / "integrator.md").read_text(encoding="utf-8")

    assert "只写问题本身" in text
    assert "处置说明" in text
    assert "条目地址" in text


def test_integrator_limits_forbid_ledger_talk_in_open_item_titles():
    """s11 标题只写"问题本身"：不许交代"这件事这一轮怎么处置的"（M7 A′）。"""

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    assert "只写问题本身" in LIMITS_TEXT
    assert "本轮已出题" in LIMITS_TEXT
    assert "E…" in LIMITS_TEXT


async def test_gateway_prefers_the_neutral_trace_id_key(monkeypatch):
    """中立键优先：讨论（M9）传的是 `discussion_id`，靠上下文键兜底会漏第三次。"""

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

    monkeypatch.setattr(provider_module, "acompletion", scripted)
    recorder = _RecordingRecorder()
    gateway = LiteLLMGateway(model="m", api_key="k", recorder=recorder)

    await gateway.complete(
        ModelRequest(
            purpose="discussion",
            messages=(ModelMessage(role="user", content="提示词"),),
            schema_name="DiscussionReply",
            response_schema={"type": "object"},
            metadata={"discussion_id": "d-1", "trace_id": "d-1"},
        )
    )

    assert recorder.rows[0]["trace_id"] == "d-1"


def test_no_prompt_tells_the_model_to_copy_a_bracketed_token():
    """方括号是出处编号 `[E…]` 的专用格式（`render_materials` / `render_claims`）。

    曾经 `sim_review.md` 写着"文档里 `[条目编号]` 那种，抄原样"，模型照办，
    把方括号抄进 `entry_id`，机械筛一律判"指向不存在的条目"。
    这条钉住：任何提示词都不许再教模型照抄方括号。
    """

    offenders = []
    for path in sorted(PROMPTS.glob("*.md")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "抄原样" in line and "`[" in line:
                offenders.append(f"{path.stem}:{number}")

    assert offenders == [], f"这些行在教模型照抄方括号（方括号只归出处编号用）：{offenders}"


# ── 硬规矩只说"要怎么做"，不说"不许怎么做"（`07 §7.12` 第 3 项）──────────────
#
# 依据（2026-10 联网调研）：否定问句下 **37.4–71.1%** 的案例里近期开源与闭门模型会重复原答案
# （arXiv:2610.09571），且呈**逆规模律**（越大越差，arXiv:2209.12711）；"提供更多信息"在多数
# 情况下反而降低表现（arXiv:2306.08189）。Anthropic 的方向同向：tell it what to do instead of
# what not to do。所以祈使否定（不许/不要/不准 + 动词）全部改写成正面写法。
#
# **条件后果句（"否则…/…会被丢掉"）不在此列**——它不是禁令，是"为什么要守这条"的理由，
# 而"给每条规则理由"同样是官方建议。


def test_the_integrator_rules_say_what_to_do_not_what_to_avoid():
    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    # 「拿不准」是"不确定"，不是禁令——先剔掉再数
    scannable = LIMITS_TEXT.replace("拿不准", "")
    offenders = {
        word: scannable.count(word) for word in ("不许", "不要", "不准") if word in scannable
    }
    assert offenders == {}, f"这些祈使否定还在（改写成正话）：{offenders}"


def test_the_rewrite_keeps_every_hard_number_and_consequence():
    """改写只为更可能被遵守，**不许顺丢信息**：硬数字与"不守会怎样"都要在原地。"""

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    from pmstudio.application.agents.integrator import INTEGRATION_MAX_PROPOSALS

    assert "5 道题" in LIMITS_TEXT        # 每批题数上限
    assert f"{INTEGRATION_MAX_PROPOSALS} 条" in LIMITS_TEXT   # 提案上限
    assert "84 条" in LIMITS_TEXT         # 留痕上限
    assert "替你留着" in LIMITS_TEXT       # 超出名额的后果
    assert "悄悄丢掉" in LIMITS_TEXT       # 重复题的后果（这条理由正是规则的价值）
    assert "答得上来" in LIMITS_TEXT       # 留痕的理由


def test_the_dropped_opinion_rule_is_stated_once():
    """同一条规矩说两遍就是给自己减分（指令条数本身就是成本，N=80 时全线归零）。"""

    from pmstudio.application.agents.integrator import limits_text
    from tests.support.builders import STD

    LIMITS_TEXT = limits_text(STD)

    hits = LIMITS_TEXT.count("dropped_opinions")
    assert hits == 1, f"dropped_opinions 出现了 {hits} 次——同一件事只讲一遍"


# ── 第 4 项的结论：试过，被真模型实验否掉（2026-10-10，`07 §7.13`）──────────
#
# 原假设（来自联网调研）：① Veseli 2025 的 distance-to-end bias → 载荷贴输出；
# ② OpenAI GPT-4.1 官方 → 指令首尾各一份。据此把 $user_input/$external_material
# 挪到所有参照段与指令之后，并加了一段末尾指令卡。
#
# 隔离变量的真模型实验（5 种布局 × 2 输入 × 2 次；再 L0 vs L2 × 5 次）证明：
# - **载荷位移无可测收益**：主张数、章节覆盖、真互斥检出在各布局间无差异；
# - **末尾指令卡有害**：无互异的输入上误报互斥从 0/4 升到 2/4，且没有任何补偿收益。
#   机制上说得通：卡里复述"两条规则不能同时成立"这类措辞，把模型的互斥阈值调低了。
#
# 所以这里钉的是**旧顺序**，并且明确禁止末尾指令卡回来。


def _slots_of(name: str) -> list[str]:
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    found = re.findall(r"\$([a-z_]+)", text)
    return [item for index, item in enumerate(found) if found.index(item) == index]


def test_the_analyzer_keeps_the_payload_beside_the_project_context():
    """载荷留在原处（项目上下文之后、席位目录之前）——位移实验证明没有收益。"""

    slots = _slots_of("analyzer")

    assert slots.index("user_input") < slots.index("seat_catalog")
    assert slots.index("document_state") < slots.index("user_input")


def test_no_prompt_gets_a_tail_instruction_card():
    """末尾指令卡被试成有害（误报互斥 0/4 → 2/4），禁止回来。

    判据是"user_input 之后不存在第二个 ## 节"——那正是末尾卡的形状。
    """

    for name in ("analyzer", "sim_inquiry"):
        text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
        after = text[text.index("$user_input") if "$user_input" in text else text.index("$claims"):]

        assert "## 动手前" not in after, f"{name}.md 又冒出了末尾指令卡"


def test_the_analyzer_wording_matches_where_the_sections_actually_are():
    """提示词里"上面「当前文档」"这类指向必须和节的实际顺序一致。

    搬过家就改口；**没搬家但措辞改了也要同步**——2026-10-10（§J-A）改检索词阶梯时
    把「上面「当前文档」」写成「「当前文档」那节」（那一节仍在它前面，两种说法都成立），
    这条钉的是"指示代词与真实顺序不打架"，不是钉某一种说法。
    """

    text = (PROMPTS / "analyzer.md").read_text(encoding="utf-8")

    assert "当前文档" in text and ("上面「当前文档」" in text or "「当前文档」那节" in text)
    assert "本轮的外部材料" in text


async def test_the_gateway_passes_max_tokens_when_the_request_asks_for_it(monkeypatch):
    """R：请求里给了 `max_tokens` → **真的发给供应商**。

    为什么（批 14，M5）：`max_tokens` 之前**根本没设** → 撞的是**供应商默认**输出上限；
    而 60 条常常装不下（实测 17% 的整合调用写了 >60 条 → 事后截断 → 最多丢 16 条提案，
    用户拿不到）。要**显式**给一个算好的上限，参数就得真的到得了供应商 ✓

    I：带 `max_tokens=8457` 的请求；
    P：网关构造 kwargs；
    R：供应商收到的 kwargs 里有 `max_tokens=8457`。
    """
    seen: list[dict] = []

    async def provider(**kwargs):
        seen.append(dict(kwargs))
        return _ok_response()

    monkeypatch.setattr(provider_module, "acompletion", provider)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)

    await gateway.complete(
        ModelRequest(
            purpose="integration",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="IntegrationPacket",
            response_schema={"type": "object"},
            max_tokens=8457,
        )
    )

    assert seen[0].get("max_tokens") == 8457, seen[0]


async def test_the_gateway_does_not_send_max_tokens_when_nobody_asked(monkeypatch):
    """R：没给 `max_tokens` → **不往供应商发这个参数**（保持现状，不替别人做主）。

    I：不带 `max_tokens` 的请求；
    P：网关构造 kwargs；
    R：kwargs 里**没有** `max_tokens`（供应商继续用它自己的默认）。
    """
    seen: list[dict] = []

    async def provider(**kwargs):
        seen.append(dict(kwargs))
        return _ok_response()

    monkeypatch.setattr(provider_module, "acompletion", provider)
    gateway = LiteLLMGateway(model="m", api_key="k", api_base=None)

    await gateway.complete(
        ModelRequest(
            purpose="integration",
            messages=(ModelMessage(role="user", content="x"),),
            schema_name="IntegrationPacket",
            response_schema={"type": "object"},
        )
    )

    assert "max_tokens" not in seen[0], seen[0]
