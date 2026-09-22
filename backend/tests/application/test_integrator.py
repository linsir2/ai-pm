import json

import pytest

from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.errors import PacketParseError
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway

INTEGRATION_REPLY = json.dumps(
    {
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数是多少？",
                "why": "量级决定同步还是异步，只有用户知道",
                "consequence_if_skipped": "按同步实现，超时风险由 PM 承担",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万–10 万"},
                ],
                "evidence_refs": ["E4"],
                "asked_by": ["seat.data_scale"],
            }
        ],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "acceptance",
                },
                "text": "5 万条以内 60 秒可下载",
                "origin_type": "derived",
                "evidence_refs": ["E4", "E9"],
                "rationale": "量级在 1 万级，同步实现可满足",
                "kind": "acceptance",
            }
        ],
    },
    ensure_ascii=False,
)


async def test_integrator_returns_parsed_packet_and_all_slots():
    gateway = ScriptedModelGateway(replies={"integration": INTEGRATION_REPLY})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    packet = await integrator.integrate(
        document_state="# 文档",
        claims="- [E1] 需求 · s5 · 要做一个订单导出功能",
        opinions="### seat.data_scale（数据规模与性能）…",
        answered="（还没有答过题）",
        skipped="（没有跳过的题）",
        entry_ids="（文档现在没有任何条目）",
    )

    assert packet.questions[0].kind == "gap"
    assert packet.proposals[0].target.field == "acceptance"
    request = gateway.requests[0]
    assert request.purpose == "integration"
    assert request.schema_name == "IntegrationPacket"
    name, slots = prompts.calls[0]
    assert name == "integrator"
    assert tuple(sorted(slots)) == tuple(sorted(Integrator.PROMPT_SLOTS))
    assert slots["validation_error"] == ""  # 第一次不给错误
    assert "最多 5" in slots["limits"]
    assert "功能子树" in slots["limits"]


async def test_integrator_passes_validation_error_on_repair():
    gateway = ScriptedModelGateway(replies={"integration": INTEGRATION_REPLY})
    prompts = RecordingPrompts()
    integrator = Integrator(model=gateway, prompts=prompts)

    await integrator.integrate(
        document_state="# 文档",
        claims="…",
        opinions="…",
        answered="…",
        skipped="…",
        entry_ids="…",
        validation_error="未知字段：exceptionss",
    )

    assert "未知字段：exceptionss" in prompts.calls[0][1]["validation_error"]


async def test_integrator_bad_missing_proposal_refs_is_a_packet_error():
    reply = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s5"},
                    "text": "x",
                    "origin_type": "derived",
                    "evidence_refs": [],
                }
            ],
        },
        ensure_ascii=False,
    )
    gateway = ScriptedModelGateway(replies={"integration": reply})
    integrator = Integrator(model=gateway, prompts=RecordingPrompts())

    with pytest.raises(PacketParseError):
        await integrator.integrate(
            document_state="# 文档",
            claims="…",
            opinions="…",
            answered="…",
            skipped="…",
            entry_ids="…",
        )
