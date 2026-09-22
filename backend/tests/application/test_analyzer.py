import json

import pytest
from pydantic import ValidationError

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.schemas import ClaimsPacket
from pmstudio.application.errors import PacketParseError
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway

INPUT = "想做个订单导出的功能，运营每天都要用，数据量大概几万。登录页太丑了。下个月必须上线。"

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
    },
    ensure_ascii=False,
)


async def test_analyzer_returns_claims_with_labels_targets_and_no_eids():
    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    prompts = RecordingPrompts()
    analyzer = ClaimAnalyzer(model=gateway, prompts=prompts)

    claims = await analyzer.analyze(user_input=INPUT, document_state="# 订单中台（v1）")

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


async def test_analyzer_sends_purpose_schema_and_all_prompt_slots():
    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    prompts = RecordingPrompts()
    analyzer = ClaimAnalyzer(model=gateway, prompts=prompts)

    await analyzer.analyze(user_input=INPUT, document_state="# 文档")

    assert gateway.requests[0].purpose == "claims"
    assert gateway.requests[0].schema_name == "ClaimsPacket"
    assert gateway.requests[0].response_schema["title"] == "ClaimsPacket"
    name, slots = prompts.calls[0]
    assert name == "analyzer"
    assert tuple(sorted(slots)) == tuple(sorted(ClaimAnalyzer.PROMPT_SLOTS))
    assert "猜测" in slots["labels"]
    assert "s5 功能需求" in slots["sections"]


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
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = ClaimAnalyzer(model=gateway, prompts=RecordingPrompts())

    with pytest.raises(PacketParseError) as exc:
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")
    assert "原话片段" in exc.value.message
    assert exc.value.details["purpose"] == "claims"


async def test_analyzer_bad_preference_target_may_not_carry_section():
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
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"claims": reply})
    analyzer = ClaimAnalyzer(model=gateway, prompts=RecordingPrompts())

    with pytest.raises(PacketParseError):
        await analyzer.analyze(user_input=INPUT, document_state="# 文档")


def test_claims_from_packet_requires_at_least_one_claim():
    with pytest.raises(ValidationError):
        ClaimsPacket.model_validate({"basis": "读了文档", "claims": []})
