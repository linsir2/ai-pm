import json

import pytest
from pydantic import ValidationError

from pmstudio.application.agents.analyzer import ClaimAnalyzer, grounded_span
from pmstudio.application.agents.schemas import ClaimsPacket
from pmstudio.application.errors import PacketParseError
from pmstudio.domain.round.round import SeatNomination
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from tests.support.builders import SEAT, make_seat
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway, StaticSeatCatalog

INPUT = "想做个订单导出的功能，运营每天都要用，数据量大概几万。登录页太丑了。下个月必须上线。"

#: `case-a` 那次的真实输入（留样原样抄下来）
CASE_A_INPUT = (
    "想做个订单导出的功能，运营每天都要用，最好能按时间筛选，数据量大概几万吧。"
    "另外登录页太丑了，顺便改一下，支持微信登录。月底必须上线。"
)
#: 真模型在 `case-a` 上稳定产出的那条复述（完整 eval 与单条重跑各留了一次，逐字相同）。
#: 两段都是原话，省略号 3 个字却计进分母：最长匹配 10 字撞上 10.5 字门槛 → 差 0.5 字判死整轮。
CASE_A_SPAN = "想做个订单导出的功能...最好能按时间筛选"


def make_analyzer(gateway, prompts) -> ClaimAnalyzer:  # type: ignore[no-untyped-def]
    """测试里统一用"只有一个席位"的目录：`SEAT`。"""

    return ClaimAnalyzer(model=gateway, prompts=prompts, catalog=StaticSeatCatalog((make_seat(),)))

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文（当前为空文档）与本轮输入",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            },
            {
                "text": "数据量大概几万",
                "raw_span": "数据量大概几万",
                "label": "guess",
                "zone": "body",
                "section_id": "s10",
                "assumption": {
                    "failure_consequence": "若实际是百万级，同步导出会超时",
                    "verification": "上线后看导出耗时分布",
                },
            },
            {
                "text": "登录页太丑",
                "raw_span": "登录页太丑了",
                "label": "preference",
                "zone": "preference",
            },
        ],
        "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
    },
    ensure_ascii=False,
)


async def test_analyzer_returns_claims_with_labels_targets_and_no_eids():
    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    prompts = RecordingPrompts()
    analyzer = make_analyzer(gateway, prompts)

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 订单中台（v1）")
    claims = analysis.claims

    assert [claim.label for claim in claims] == [
        ClaimLabel.NEED,
        ClaimLabel.GUESS,
        ClaimLabel.PREFERENCE,
    ]
    assert [claim.claim_id for claim in claims] == ["c1", "c2", "c3"]
    assert claims[0].target == Target.body("s5")
    assert claims[1].assumption is not None
    assert claims[2].target == Target.preference()
    assert all(claim.eid is None for claim in claims)  # 编号由汇合点分配
    assert analysis.seats == (SeatNomination(seat_id=SEAT, why="你说到了数据量"),)


async def test_analyzer_sends_purpose_schema_and_all_prompt_slots():
    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    prompts = RecordingPrompts()
    analyzer = make_analyzer(gateway, prompts)

    await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert gateway.requests[0].purpose == "claims"
    assert gateway.requests[0].schema_name == "ClaimsPacket"
    assert gateway.requests[0].response_schema["title"] == "ClaimsPacket"
    name, slots = prompts.calls[0]
    assert name == "analyzer"
    assert tuple(sorted(slots)) == tuple(sorted(ClaimAnalyzer.PROMPT_SLOTS))
    assert "猜测" in slots["labels"]
    assert "s5 功能需求" in slots["sections"]
    # 席位目录也要进上下文：模型得看见"有哪些席位"才好挑
    assert SEAT in slots["seat_catalog"]
    assert "管：" in slots["seat_catalog"]
    assert slots["validation_error"] == ""  # 第一次不给错误


async def test_analyzer_passes_validation_error_on_repair():
    """重装时必须把"上一次错在哪"喂回去（与整合者 `validation_error` 同形状）。"""

    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    prompts = RecordingPrompts()
    analyzer = make_analyzer(gateway, prompts)

    await analyzer.analyze(
        user_input=INPUT,
        document_state="# 文档",
        validation_error="第 4 条主张（c4）的 raw_span 在用户输入里找不到对应的连续原话。",
    )

    assert "第 4 条主张（c4）" in prompts.calls[0][1]["validation_error"]


async def test_analyzer_bad_raw_span_must_appear_in_input():
    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "要做订单导出",
                    "raw_span": "帮我搞个导出吧",  # 用户没这么说过
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    with pytest.raises(PacketParseError) as exc:
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")
    assert "原话片段" in exc.value.message
    assert exc.value.details["purpose"] == "claims"


async def test_analyzer_realigns_recombined_raw_span_to_a_real_fragment():
    """模型把两段不连续的原话拼成一句时，取原文里最长的那段真实片段，而不是判死整轮。

    （真模型冒烟实测：raw_span="运营每天都要用，顺便改一下" 是拼接出来的，
    严格子串匹配把整轮判死了。raw_span 的用途是跳回原文，跳到真实的一段就够。）
    """

    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "运营每天都要用，另外登录页要改",
                    "raw_span": "运营每天都要用，顺便改一下",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    claims = (await analyzer.analyze(user_input=INPUT, document_state="# 文档")).claims

    span = claims[0].raw_span
    assert span in INPUT  # 一定是原文里真实存在的一段
    assert len(span) >= 6


async def test_analyzer_survives_an_ellipsis_joined_raw_span():
    """同一件事的省略号写法：整轮不许再判死（完整 eval 里 `case-a` 就是这么红的）。"""

    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "订单导出要能按时间筛选",
                    "raw_span": CASE_A_SPAN,
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    claims = (await analyzer.analyze(user_input=CASE_A_INPUT, document_state="# 文档")).claims

    assert claims[0].raw_span == "想做个订单导出的功能"
    assert claims[0].raw_span in CASE_A_INPUT


async def test_analyzer_still_rejects_invented_spans():
    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "要做订单导出",
                    "raw_span": "帮我搞个导出吧",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    with pytest.raises(PacketParseError):
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")


def test_grounded_span_realigns_an_ellipsis_joined_span_when_both_parts_are_real():
    """省略号是"拼接标记"，不是内容：每段各自得站得住，取最长的那段真实原话。

    真模型实测（完整 eval 的 `case-a`）：`A...B` 两段都在输入里逐字存在，
    却因为省略号 3 个字也计进分母，差 0.5 字把整轮判死（502，用户只能手动重试）。
    """

    assert grounded_span(CASE_A_SPAN, CASE_A_INPUT) == "想做个订单导出的功能"


@pytest.mark.parametrize("joiner", ["...", "……", "。。。", "…"])
def test_grounded_span_accepts_the_common_ellipsis_forms(joiner):
    """模型写省略号的写法不止一种（英文三点、中文省略号、连写句号）——都按拼接标记处理。"""

    span = f"想做个订单导出的功能{joiner}最好能按时间筛选"

    assert grounded_span(span, CASE_A_INPUT) == "想做个订单导出的功能"


def test_grounded_span_rejects_an_ellipsis_joined_span_with_one_invented_part():
    """拼接不放松反编造：只要有一段在原文里找不到，整条还是不认（这段是真模型编过的形状）。"""

    span = "想做个订单导出的功能...帮我搞个导出吧"

    assert grounded_span(span, CASE_A_INPUT) is None


def test_grounded_span_rejects_a_span_that_is_only_an_ellipsis():
    """`……` 这种"只有一个拼接标记"的候选里一句原话都没有，不算复述。"""

    assert grounded_span("……", CASE_A_INPUT) is None
    assert grounded_span("...", CASE_A_INPUT) is None


def test_grounded_span_keeps_a_verbatim_span_that_itself_contains_dots():
    """原文里真的写了三点时，逐字命中优先——不许把它切段（切了反而跳不回原文）。"""

    source = "第一句...第二句都在原文里"

    assert grounded_span(source, source) == source


def test_grounded_span_returns_the_longest_real_fragment():
    span = "月底必须上线...想做个订单导出的功能"

    assert grounded_span(span, CASE_A_INPUT) == "想做个订单导出的功能"


async def test_analyzer_normalizes_preference_target_that_also_carries_a_section():
    """归位只是"建议"：偏好带 section 不再判整轮失败，而是按领域规则落进偏好区。

    （原测试断言这里是 PacketParseError——那是"归位必须严格合法"的旧规则。
    真模型冒烟表明这类笔误会把整轮判死，代价太大；改规则的记录见汇报。）
    """

    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "登录页太丑",
                    "raw_span": "登录页太丑了",
                    "label": "preference",
                    "zone": "preference",
                    "section_id": "s3",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    claims = (await analyzer.analyze(user_input=INPUT, document_state="# 文档")).claims

    assert claims[0].target == Target.preference()  # 偏好区胜出，内容照旧
    assert claims[0].text == "登录页太丑"


def test_claims_from_packet_requires_at_least_one_claim():
    with pytest.raises(ValidationError):
        ClaimsPacket.model_validate({"basis": "读了文档", "claims": []})


async def test_analyzer_reports_the_position_of_the_bad_span():
    """错误细节要能说清"第几条"，重装才点得准（只有 `claim_id=c2` 也说不太清）。"""

    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "要做一个订单导出功能",
                    "raw_span": "想做个订单导出的功能",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                },
                {
                    "text": "要做通知",
                    "raw_span": "帮我搞个通知吧",  # 用户没这么说过
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                },
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    with pytest.raises(PacketParseError) as exc:
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert exc.value.details["claim_id"] == "c2"
    assert exc.value.details["position"] == 2


async def test_analyzer_refuses_a_seat_that_is_not_in_the_catalog():
    """挑了目录里没有的席位 → 整轮失败。

    不"悄悄丢掉"：挑到不存在的席位说明它没看目录，这一轮的编制就不可信了；
    与 raw_span 编造、引用不存在的编号同一套严格度（可重试）。
    """

    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "要做一个订单导出功能",
                    "raw_span": "想做个订单导出的功能",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": "seat.invented", "why": "我编的"}],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    with pytest.raises(PacketParseError) as exc:
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert "目录里没有" in exc.value.message
    assert exc.value.details["seat_id"] == "seat.invented"


async def test_analyzer_dedups_repeated_seat_picks():
    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "要做一个订单导出功能",
                    "raw_span": "想做个订单导出的功能",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [
                {"seat_id": SEAT, "why": "第一次说的理由"},
                {"seat_id": SEAT, "why": "又说了一遍"},
            ],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert analysis.seats == (SeatNomination(seat_id=SEAT, why="第一次说的理由"),)


def test_claims_packet_requires_at_least_one_seat():
    with pytest.raises(ValidationError):
        ClaimsPacket.model_validate({"basis": "读了文档", "claims": [], "seats": []})
