import json

import pytest
from pydantic import ValidationError

from pmstudio.application.agents.analyzer import ClaimAnalyzer, grounded_span
from pmstudio.application.agents.schemas import ClaimsPacket
from pmstudio.domain.round.round import DropSource, SeatNomination
from pmstudio.domain.round.web_query import WebQuery
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.harness.errors import PacketParseError

#: 提示词本体：归位规则这类"模型每轮都看得见"的东西，钉在文件上比钉在槽里准
from pmstudio.harness.prompt import PROMPTS_DIR  # 路径跟随资源，别在这里重算
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


async def test_analyzer_keeps_known_seats_and_reports_unknown_ones():
    """真跑实测：分析者提名 `seat.observability`（目录里没有）→ 两次重装都失败 → 轮次创建不了。

    新口径：目录外的那条**丢提名、留痕**，剩下的席位照跑；只有一条都不剩才判死。
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
            "seats": [
                {"seat_id": "seat.observability", "why": "s8 有可观测性槽位"},
                {"seat_id": SEAT, "why": "你说到了数据量"},
            ],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert analysis.seats == (SeatNomination(seat_id=SEAT, why="你说到了数据量"),)
    assert [item.handle for item in analysis.dropped] == ["席位提名 seat.observability"]
    assert "目录里没有" in analysis.dropped[0].reason


def contradictions_reply(*, claim_a: int, claim_b: int) -> str:
    """既有报文 + 一对互斥（写的是**第几条主张**，从 1 开始——不是 `c1`）。"""

    payload = json.loads(CLAIMS_REPLY)
    payload["contradictions"] = [
        {"claim_a": claim_a, "claim_b": claim_b, "why": "一个说不能改，一个说要能改"}
    ]
    return json.dumps(payload, ensure_ascii=False)


def test_the_claims_packet_accepts_contradiction_pairs():
    packet = ClaimsPacket.model_validate(json.loads(contradictions_reply(claim_a=1, claim_b=2)))

    assert packet.contradictions[0].claim_a == 1
    assert packet.contradictions[0].claim_b == 2
    assert packet.contradictions[0].why


def test_the_claims_packet_defaults_to_no_contradictions():
    """没报互斥就是空数组——这是常态，不是缺字段。"""

    assert ClaimsPacket.model_validate(json.loads(CLAIMS_REPLY)).contradictions == []


async def test_the_analyzer_turns_claim_numbers_into_claim_ids():
    """序号 → `c{i}`：编号是我们按数组下标发的，模型只报"第几条"。"""

    gateway = ScriptedModelGateway(replies={"claims": contradictions_reply(claim_a=1, claim_b=3)})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert [item.claim_id for item in analysis.claims] == ["c1", "c2", "c3"]
    assert [(item.claim_id_a, item.claim_id_b) for item in analysis.contradictions] == [
        ("c1", "c3")
    ]
    assert analysis.dropped == ()


async def test_a_contradiction_pointing_at_an_unknown_claim_is_dropped_with_a_trace():
    """写歪的序号丢**那一条** + 留痕，不判死整轮（与未知席位提名同一个口径）。"""

    gateway = ScriptedModelGateway(replies={"claims": contradictions_reply(claim_a=1, claim_b=9)})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert analysis.contradictions == ()
    assert [item.handle for item in analysis.dropped] == ["互斥对子 1↔9"]
    assert "只有 3 条主张" in analysis.dropped[0].reason


async def test_a_contradiction_of_a_claim_with_itself_is_dropped_too():
    """自己跟自己不叫互斥——按"写歪"处理，丢那一条并留痕。"""

    gateway = ScriptedModelGateway(replies={"claims": contradictions_reply(claim_a=2, claim_b=2)})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert analysis.contradictions == ()
    assert [item.handle for item in analysis.dropped] == ["互斥对子 2↔2"]


def web_queries_reply(*queries: dict) -> str:
    """在最小可用报文上加一串检索词提名（`P17b` §5）。"""

    payload = json.loads(CLAIMS_REPLY)
    payload["web_queries"] = list(queries)
    return json.dumps(payload, ensure_ascii=False)


async def test_analyzer_parses_the_queries_the_model_nominated():
    """闸门 A：分析者只**提名**，一条都不许自己查（联网要用户在复述卡上拍板）。"""

    gateway = ScriptedModelGateway(
        replies={
            "claims": web_queries_reply(
                {"query": "RBAC 权限模型的主流做法", "why": "要查行业通行的权限模型"}
            )
        }
    )
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert analysis.web_queries == (
        WebQuery(query="RBAC 权限模型的主流做法", why="要查行业通行的权限模型"),
    )
    assert analysis.dropped == ()


async def test_no_web_queries_in_the_packet_is_an_empty_tuple():
    """没提名是常态（老报文里根本没有这把键），不许当成缺字段。"""

    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert analysis.web_queries == ()


async def test_a_fourth_query_is_dropped_with_a_trace_not_a_restart():
    """越界丢**多出来的那几条** + 留痕，不判死整轮（与席位提名同一个口径）。"""

    gateway = ScriptedModelGateway(
        replies={
            "claims": web_queries_reply(
                *[
                    {"query": f"第{index}条检索词", "why": "要查外部客观事实"}
                    for index in range(1, 5)
                ]
            )
        }
    )
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert [item.query for item in analysis.web_queries] == [
        "第1条检索词",
        "第2条检索词",
        "第3条检索词",
    ]
    assert [item.handle for item in analysis.dropped] == ["检索词 第4条检索词"]
    assert "最多 3 条" in analysis.dropped[0].reason


async def test_a_query_without_a_reason_is_dropped_with_a_trace():
    """没说清"为什么要查外部事实"的那一条丢掉，其余照留。"""

    gateway = ScriptedModelGateway(
        replies={
            "claims": web_queries_reply(
                {"query": "RBAC", "why": "   "},
                {"query": "订单导出的行业规范", "why": "要查行业标准"},
            )
        }
    )
    analyzer = make_analyzer(gateway, RecordingPrompts())

    analysis = await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert [item.query for item in analysis.web_queries] == ["订单导出的行业规范"]
    assert [item.handle for item in analysis.dropped] == ["检索词 RBAC"]
    assert "为什么" in analysis.dropped[0].reason


ASSUMPTION_ON_CONSTRAINT_INPUT = "点检记录不能改，出了事故要能查是谁点的、几点点的。"


async def test_assumption_on_a_non_guess_claim_is_stripped_not_fatal():
    """真跑 `.codex/runs/2026-10-01/报告.md` F16：第 6 条主张带 `assumption` 但标签是
    `constraint` → 领域校验 `只有猜测才带假设` → 整轮判死、起轮 502、没有 `/retry` 可点。

    口径与 P8 的字段级容错一致：**丢字段、不丢主张**——主张是用户原话在文档里的落点，
    丢了它等于把用户说过的一句话从证据链里挖掉。
    """

    reply = json.dumps(
        {
            "basis": "读了文档与本轮输入",
            "claims": [
                {
                    "text": "点检记录不许改",
                    "raw_span": "点检记录不能改",
                    "label": "constraint",
                    "zone": "body",
                    "section_id": "s5",
                    "assumption": {
                        "failure_consequence": "改不了会说不清责任",
                        "verification": "看审计日志",
                    },
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "涉及留痕"}],
        },
        ensure_ascii=False,
    )
    analyzer = make_analyzer(ScriptedModelGateway(replies={"claims": reply}), RecordingPrompts())

    analysis = await analyzer.analyze(
        user_input=ASSUMPTION_ON_CONSTRAINT_INPUT, document_state="# 文档"
    )

    assert len(analysis.claims) == 1
    assert analysis.claims[0].assumption is None
    assert analysis.claims[0].label is ClaimLabel.CONSTRAINT
    assert [item.handle for item in analysis.dropped] == ["主张 c1 的假设"]
    assert [item.reason for item in analysis.dropped] == [
        "只有猜测才带假设：这条主张标的是 constraint，假设字段已丢掉；主张本身保留"
    ]
    assert analysis.dropped[0].source is DropSource.ASSEMBLY


async def test_guess_without_assumption_is_still_fatal():
    """反方向不放松：标了猜测却不给假设，仍是模型的错（它该补的是 `assumption`）。"""

    reply = json.dumps(
        {
            "basis": "读了文档",
            "claims": [
                {
                    "text": "可能要做离线点检",
                    "raw_span": "想做个订单导出的功能",
                    "label": "guess",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "涉及离线"}],
        },
        ensure_ascii=False,
    )
    analyzer = make_analyzer(ScriptedModelGateway(replies={"claims": reply}), RecordingPrompts())

    with pytest.raises(PacketParseError):
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")


def test_analyzer_prompt_routes_backbone_sentences_to_their_chapters():
    """真跑实测（`.codex/runs/2026-10-01/报告.md` F18）：一份叙述式输入里 12 条主张
    有 **10 条**落进 `s5/*.user_story`，s1/s2/s3/s4 **零条** → 整合者拿不到写那几章的授权
    → 8 个阻塞；而带材料那条车道反而归得准，说明是提示词没说清，不是模型不会。
    """

    body = (PROMPTS_DIR / "analyzer.md").read_text(encoding="utf-8")

    # 锚点用归位表的表头（不是某个魔法串）：表在，规则就在
    assert "这段话在说什么" in body
    table = body[body.index("这段话在说什么") :]
    for chapter, phrase in (
        ("**s1**", "现状、痛点、不做的代价"),
        ("**s2**", "要达成什么、成功标准"),
        ("**s3**", "谁在用、什么角色"),
        ("**s4**", "本期做什么、本期**不做**什么"),
        ("**s10**", "假设、依赖、外部系统"),
        ("**s12**", "什么时候上线、怎么验收"),
        ("**s8**", "性能/可用性/安全/兼容/可观测/成本"),
        ("**s5**", "section_id + feature_slug"),
    ):
        assert phrase in table, f"{chapter} 那一行没写"
        assert chapter in table, f"{chapter} 没出现在归位表里"
    assert "各切一条" in body  # 规则句在表前一句，表里只要六章都在


async def test_analyzer_still_sends_the_section_table_to_the_model():
    """归位规则改了，槽没变：`sections` 照旧要进上下文（防止改提示词时把槽删掉）。"""

    prompts = RecordingPrompts()
    analyzer = make_analyzer(ScriptedModelGateway(replies={"claims": CLAIMS_REPLY}), prompts)

    await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert "s5 功能需求" in prompts.calls[0][1]["sections"]


# —— 容器里的"非对象元素"：主张这一包曾是唯一没有 `/retry` 的失败路 ——


def claims_with_a_placeholder(*, contradiction: tuple[int, int] | None = None) -> str:
    """三条主张的第 2 条写成占位符字符串（形状错），第 1、3 条是真主张。"""

    payload = {
        "basis": "读了 s1–s13 全文（当前为空文档）与本轮输入",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            },
            "c2_placeholder",
            {
                "text": "登录页太丑",
                "raw_span": "登录页太丑了",
                "label": "preference",
                "zone": "preference",
            },
        ],
        "seats": [{"seat_id": SEAT, "why": "你说到了导出"}],
    }
    if contradiction is not None:
        payload["contradictions"] = [
            {
                "claim_a": contradiction[0],
                "claim_b": contradiction[1],
                "why": "一个说不能改，一个说要能改",
            }
        ]
    return json.dumps(payload, ensure_ascii=False)


async def test_a_non_object_claim_is_dropped_with_a_trace():
    """元素不是对象 → 丢掉那一条 + 留痕；其余主张照收，**起轮不判死**。"""

    analyzer = make_analyzer(
        ScriptedModelGateway(replies={"claims": claims_with_a_placeholder()}), RecordingPrompts()
    )

    analysis = await analyzer.analyze(user_input=CASE_A_INPUT, document_state="# 文档")

    assert [item.text for item in analysis.claims] == ["要做一个订单导出功能", "登录页太丑"]
    assert [item.claim_id for item in analysis.claims] == ["c1", "c2"]
    assert [item.handle for item in analysis.dropped] == ["主张 #2（不是对象）"]
    assert "不是对象" in analysis.dropped[0].reason
    assert analysis.dropped[0].source is DropSource.ASSEMBLY


async def test_a_contradiction_ordinal_is_remapped_after_a_dropped_claim():
    """丢了一条主张之后，模型写的"第 3 条"必须还能指到第 3 条**它自己**说的主张。

    序号是模型按它交回的那个数组报的；丢一条会让后面的整体前移，
    所以要用"原始序号 → 落下来的第几条"这张表换算，而不是直接拿下标去取。
    """

    analyzer = make_analyzer(
        ScriptedModelGateway(replies={"claims": claims_with_a_placeholder(contradiction=(1, 3))}),
        RecordingPrompts(),
    )

    analysis = await analyzer.analyze(user_input=CASE_A_INPUT, document_state="# 文档")

    assert [(item.claim_id_a, item.claim_id_b) for item in analysis.contradictions] == [
        ("c1", "c2")
    ]
    assert analysis.contradictions[0].why


async def test_a_contradiction_pointing_at_a_dropped_claim_is_dropped_too():
    """序号指到那条被丢掉的占位符上 → 这对互斥没有两侧，丢那一条 + 留痕。"""

    analyzer = make_analyzer(
        ScriptedModelGateway(replies={"claims": claims_with_a_placeholder(contradiction=(2, 3))}),
        RecordingPrompts(),
    )

    analysis = await analyzer.analyze(user_input=CASE_A_INPUT, document_state="# 文档")

    assert analysis.contradictions == ()
    assert [item.handle for item in analysis.dropped] == [
        "主张 #2（不是对象）",
        "互斥对子 2↔3",
    ]
    assert "没切进来" in analysis.dropped[1].reason


MATERIAL_CLAIM_REPLY = json.dumps(
    {
        "basis": "读了本轮输入与带进来的材料块 E1",
        "claims": [
            {
                "text": "按这份材料写",
                "raw_span": "按这份材料写",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
                "material_eids": ["E1"],
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "材料里有量级"}],
    },
    ensure_ascii=False,
)


async def test_claim_with_material_eids_passes_when_chunk_was_picked():
    """主张说读过 E1，而 E1 正是本轮带进来的块 → 过，Claim 上记下来源。"""

    from pmstudio.domain.shared.ids import EvidenceId

    gateway = ScriptedModelGateway(replies={"claims": MATERIAL_CLAIM_REPLY})
    analyzer = make_analyzer(gateway, RecordingPrompts())
    analysis = await analyzer.analyze(
        user_input="按这份材料写",
        document_state="# 文档",
        material_eids=frozenset({EvidenceId("E1")}),
    )
    assert analysis.claims[0].material_eids == (EvidenceId("E1"),)


async def test_claim_citing_unpicked_material_fails_packet():
    """E2 在账本里但本轮没给它看——许它引等于许它编，走重装。"""

    from pmstudio.domain.shared.ids import EvidenceId

    gateway = ScriptedModelGateway(replies={"claims": MATERIAL_CLAIM_REPLY})
    analyzer = make_analyzer(gateway, RecordingPrompts())
    with pytest.raises(PacketParseError, match="材料块"):
        await analyzer.analyze(
            user_input="按这份材料写",
            document_state="# 文档",
            material_eids=frozenset({EvidenceId("E2")}),
        )


async def test_claim_citing_unknown_eid_fails_packet():
    gateway = ScriptedModelGateway(replies={"claims": MATERIAL_CLAIM_REPLY})
    analyzer = make_analyzer(gateway, RecordingPrompts())
    with pytest.raises(PacketParseError, match="材料块"):
        await analyzer.analyze(
            user_input="按这份材料写", document_state="# 文档", material_eids=frozenset()
        )
