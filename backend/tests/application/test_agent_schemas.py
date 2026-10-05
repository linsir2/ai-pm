import json

import pytest
from pydantic import ValidationError

from pmstudio.application.agents import schemas
from pmstudio.harness.errors import PacketParseError


def test_claims_packet_schema_is_generated_with_required_fields():
    schema = schemas.ClaimsPacket.model_json_schema()
    assert set(schema["required"]) == {"basis", "claims", "seats"}
    claim_props = schema["$defs"]["ClaimDraft"]["properties"]
    assert {"text", "raw_span", "label", "zone"} <= set(claim_props)
    assert "fact" in claim_props["label"]["enum"]
    # 挑席位是硬要求：至少一个，且要说清为什么
    seat_props = schema["$defs"]["SeatPickDraft"]["properties"]
    assert set(seat_props) == {"seat_id", "why"}


def test_raw_span_field_carries_the_grounding_rule_in_its_description():
    """`raw_span` 的规矩要写在字段上——这个 schema 会被内联进提示词，写在字段旁最不容易漂。"""

    description = schemas.ClaimsPacket.model_json_schema()["$defs"]["ClaimDraft"][
        "properties"
    ]["raw_span"]["description"]

    assert "连续" in description
    assert "省略号" in description


def test_seat_packet_requires_form_enum():
    schema = schemas.SeatPacket.model_json_schema()
    form = schema["$defs"]["OpinionDraft"]["properties"]["form"]
    assert form["enum"] == ["question", "judgment"]


def test_integration_packet_caps_questions_at_twelve():
    schema = schemas.IntegrationPacket.model_json_schema()
    assert schema["properties"]["questions"]["maxItems"] == 12


def test_inquiry_packet_shape_caps_and_falsifiability_fields():
    """调研包：上限 6/3 写进 schema；假设必须带证伪方式与所需真实证据（X1 的线形状）。"""

    schema = schemas.InquiryPacket.model_json_schema()
    assert set(schema["required"]) == {"basis"}
    assert schema["properties"]["hypotheses"]["maxItems"] == 6
    assert schema["properties"]["blind_spots"]["maxItems"] == 3
    hypothesis = schema["$defs"]["HypothesisDraft"]
    assert {"statement", "grounding", "falsification", "evidence_needed"} <= set(
        hypothesis["required"]
    )
    assert "invented" in hypothesis["properties"]["grounding"]["enum"]
    assert "next_step" in schema["$defs"]["BlindSpotDraft"]["required"]


def test_parse_packet_returns_model_instance():
    text = json.dumps(
        {
            "basis": "读了全文档",
            "claims": [
                {
                    "text": "要做订单导出",
                    "raw_span": "想做个订单导出",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}],
        },
        ensure_ascii=False,
    )
    packet, _, _ = schemas.parse_packet_lenient(
        text, schemas.ClaimsPacket, purpose="claims"
    )
    assert packet.claims[0].label == "need"


def test_parse_packet_bad_not_json_is_a_packet_error():
    with pytest.raises(PacketParseError) as exc:
        schemas.parse_packet_lenient("这不是 JSON", schemas.ClaimsPacket, purpose="claims")
    assert exc.value.code == "model_packet_unparsable"
    assert exc.value.details["purpose"] == "claims"


def test_parse_packet_bad_missing_field_is_a_packet_error():
    with pytest.raises(PacketParseError):
        schemas.parse_packet_lenient('{"claims": []}', schemas.ClaimsPacket, purpose="claims")


def test_integration_packet_accepts_dropped_opinions():
    """报文要能装"我丢了哪条、为什么"（`04` C5 后半）。"""

    packet, _, _ = schemas.parse_packet_lenient(
        json.dumps(
            {
                "questions": [],
                "list_items": [],
                "proposals": [],
                "dropped_opinions": [
                    {"handle": "seat.data_scale:O2", "reason": "与文档已有内容重复"},
                ],
            },
            ensure_ascii=False,
        ),
        schemas.IntegrationPacket,
        purpose="integration",
    )

    assert packet.dropped_opinions[0].handle == "seat.data_scale:O2"
    assert packet.dropped_opinions[0].reason == "与文档已有内容重复"


def test_dropped_opinion_without_reason_is_rejected():
    """丢弃必须给原因——没有原因的留痕等于没留。"""

    with pytest.raises(PacketParseError):
        schemas.parse_packet_lenient(
            json.dumps(
                {
                    "questions": [],
                    "list_items": [],
                    "proposals": [],
                    "dropped_opinions": [{"handle": "seat.x:O1", "reason": ""}],
                },
                ensure_ascii=False,
            ),
            schemas.IntegrationPacket,
            purpose="integration",
        )


def test_a_question_must_say_which_kind_it_is():
    """`kind` 必填（P11 C3）：原来的默认值把"忘了写"和"判断成 gap"混成一种。

    真跑实测：`edge-contradiction` 的 15 道题里 **8 道没写 kind**，全被默认值吞成 gap，
    于是一道矛盾题都没标出来。
    """

    with pytest.raises(ValidationError):
        schemas.IntegrationPacket.model_validate(
            {
                "questions": [
                    {
                        "title": "单次导出的最大行数是多少？",
                        "why": "量级决定同步还是异步",
                        "consequence_if_skipped": "按同步实现",
                        "target": {"zone": "body", "section_id": "s8"},
                        "type": "single",
                        "options": [
                            {"option_id": "a", "text": "少于 1 万"},
                            {"option_id": "b", "text": "1 万–10 万"},
                        ],
                        "evidence_refs": ["E1"],
                    }
                ],
                "list_items": [],
                "proposals": [],
            }
        )


def test_a_question_with_a_kind_still_validates():
    packet = schemas.IntegrationPacket.model_validate(
        {
            "questions": [
                {
                    "kind": "conflict",
                    "title": "点检记录能不能改？",
                    "why": "E2 说不能改、E3 说要能改错",
                    "consequence_if_skipped": "先按不能改实现",
                    "target": {"zone": "body", "section_id": "s5"},
                    "type": "single",
                    "options": [
                        {"option_id": "a", "text": "不能改"},
                        {"option_id": "b", "text": "能改"},
                    ],
                    "evidence_refs": ["E2", "E3"],
                }
            ],
            "list_items": [],
            "proposals": [],
        }
    )

    assert packet.questions[0].kind == "conflict"


def test_discussion_reply_rejects_empty_text():
    """say 回包空串不算成功（M9-T2）：空回答没有推进讨论。"""

    with pytest.raises(ValidationError):
        schemas.DiscussionReply.model_validate({"text": "", "based_on": []})
    assert (
        schemas.DiscussionReply.model_validate(
            {"text": "先说说你的想法", "based_on": []}
        ).text
        == "先说说你的想法"
    )


def test_cards_packet_rejects_more_than_3():
    """组装回包超 3 张不算成功——走 PacketParseError，不做 lenient 截断（M9-T2）。"""

    drafts = [
        {"statement": f"灵感{i}", "grounding": "invented", "based_on": []}
        for i in range(4)
    ]
    with pytest.raises(ValidationError):
        schemas.CardsPacket.model_validate({"cards": drafts})


def test_cards_packet_rejects_empty_list():
    """组装回包空 list 不算成功（M9-T2）。"""

    with pytest.raises(ValidationError):
        schemas.CardsPacket.model_validate({"cards": []})
