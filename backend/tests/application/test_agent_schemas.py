import json

import pytest
from pydantic import ValidationError

from pmstudio.application.agents import schemas
from pmstudio.domain.shared.research import MAX_RESEARCH_ANSWER_CHARS
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


def test_research_packet_requires_an_answer_and_answer_refs():
    """S4：研究综合必须交回答案与来源序号；缺一个就整包重装。"""

    schema = schemas.ResearchPacket.model_json_schema()
    assert set(schema["required"]) == {"answer", "answer_refs"}
    assert schema["properties"]["answer"]["minLength"] == 1
    assert schema["properties"]["answer_refs"]["minItems"] == 1

    for payload in (
        {"findings": [], "open_questions": []},
        {"answer": "", "answer_refs": ["S1"], "findings": [], "open_questions": []},
        {"answer": "有答案", "answer_refs": [], "findings": [], "open_questions": []},
    ):
        with pytest.raises(PacketParseError):
            schemas.parse_packet_lenient(
                json.dumps(payload, ensure_ascii=False),
                schemas.ResearchPacket,
                purpose="research",
            )


def test_research_packet_answer_is_capped_to_the_shared_limit() -> None:
    """S4/B2：1200 上限要写进 schema，模型才看得见；报错也不许回显答案原句。"""

    schema = schemas.ResearchPacket.model_json_schema()
    assert schema["properties"]["answer"]["maxLength"] == MAX_RESEARCH_ANSWER_CHARS

    too_long = "字" * (MAX_RESEARCH_ANSWER_CHARS + 1)
    with pytest.raises(PacketParseError) as caught:
        schemas.parse_packet_lenient(
            json.dumps(
                {
                    "answer": too_long,
                    "answer_refs": ["S1"],
                    "findings": [],
                    "open_questions": [],
                },
                ensure_ascii=False,
            ),
            schemas.ResearchPacket,
            purpose="research",
        )

    assert caught.value.details["errors"][0]["loc"] == ("answer",)
    assert too_long not in json.dumps(
        caught.value.details, ensure_ascii=False, default=str
    )


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


def _sim_slice(i: int) -> dict:
    return {
        "local_id": f"S{i}",
        "situation": "运营月底要对账",
        "job": "按时间筛出订单",
        "trigger": "月底结账",
        "workaround": "导出全部再手工筛",
        "anchors": ["E1"],
    }


def _sim_hypothesis(i: int) -> dict:
    return {
        "local_id": f"H{i}",
        "slice_id": "S1",
        "statement": "运营宁愿等一分钟也不愿分两次导",
        "grounding": "document",
        "based_on": ["E1"],
        "falsification": "观察到运营主动分多次小批量导出",
        "evidence_needed": "跟两场月底结账看实际操作",
    }


def test_sim_inquiry_packet_caps_slices_hypotheses_unknowns():
    """模拟调研包：上限 3 切片 / 6 假设 / 3 盲区写进 schema。"""

    with pytest.raises(ValidationError):
        schemas.SimInquiryPacket.model_validate(
            {"basis": "x", "slices": [_sim_slice(i) for i in range(4)]}
        )
    with pytest.raises(ValidationError):
        schemas.SimInquiryPacket.model_validate(
            {
                "basis": "x",
                "slices": [_sim_slice(1)],
                "hypotheses": [_sim_hypothesis(i) for i in range(7)],
            }
        )
    with pytest.raises(ValidationError):
        schemas.SimInquiryPacket.model_validate(
            {
                "basis": "x",
                "slices": [_sim_slice(1)],
                "unknowns": [
                    {
                        "local_id": f"U{i}",
                        "statement": "s",
                        "why_it_matters": "w",
                        "how_to_find_out": "h",
                    }
                    for i in range(4)
                ],
            }
        )


def test_sim_slice_requires_at_least_one_anchor():
    """切片没有锚点就是凭空分组：anchors 为空直接拒收（parse 层，不靠提示词自觉）。"""

    bad = _sim_slice(1)
    bad["anchors"] = []
    with pytest.raises(ValidationError):
        schemas.SimInquiryPacket.model_validate({"basis": "x", "slices": [bad]})


def test_sim_inquiry_packet_empty_parses_ok():
    """有切片、无假设无盲区时 parse 放行："没东西"算不算成功是装配层的事（筛#6），
    parse 只管形状。注意切片至少 1 个（无切片就没有锚点单位，parse 直接拒）。"""

    packet = schemas.SimInquiryPacket.model_validate(
        {"basis": "读了 E1", "slices": [_sim_slice(1)]}
    )
    assert packet.hypotheses == [] and packet.unknowns == []


def test_sim_hypothesis_has_no_kind_subject_touches():
    """砍掉的字段不许悄悄回来：kind/subject/touches 没有任何机械消费者，
    假设的字段集锁死这 7 个（M10 审核 C1 旁的裁决）。"""

    assert set(schemas.SimHypothesisDraft.model_fields) == {
        "local_id",
        "slice_id",
        "statement",
        "grounding",
        "based_on",
        "falsification",
        "evidence_needed",
    }
    assert set(schemas.SimSliceDraft.model_fields) == {
        "local_id",
        "situation",
        "job",
        "trigger",
        "workaround",
        "anchors",
    }


def test_sim_inquiry_schema_has_no_demographic_or_numeric_fields():
    """schema 里根本没有可填人口学/数字的位置（三篇证据的落地：不是提示词口号，是形状）。"""

    banned = {
        "age", "gender", "sex", "region", "income", "education", "ethnicity",
        "score", "confidence", "percentage", "ratio", "satisfaction",
    }
    for model in (
        schemas.SimSliceDraft,
        schemas.SimHypothesisDraft,
        schemas.SimUnknownDraft,
        schemas.SimInquiryPacket,
    ):
        assert not (set(model.model_fields) & banned), model.__name__


def test_sim_inquiry_packet_rejects_zero_slices():
    """切片是锚点单位：0 个切片 parse 直接拒（连分组都没有的推演不必进装配）。"""

    with pytest.raises(ValidationError):
        schemas.SimInquiryPacket.model_validate({"basis": "读了 E1", "slices": []})


# ── 模拟评测包（M11-T3）──────────────────────────────────────────────────


def _sim_review_feature(*, frictions: list[dict] | None = None) -> dict:
    return {
        "feature_slug": "order-export",
        "started": True,
        "walkthrough": [
            {
                "step_index": 1,
                "step_text": "进入订单列表",
                "from_doc": "r1#01",
                "action": "打开订单列表页",
                "outcome": "ok",
                "need": "",
            }
        ],
        "frictions": frictions or [],
        "acceptance_checks": [
            {"local_id": "A1", "entry_id": "r1#09", "status": "unverified", "note": ""}
        ],
        "verdict": "friction",
        "verdict_because": ["F1"],
    }


def _sim_review_friction(i: int) -> dict:
    return {
        "local_id": f"F{i}",
        "kind": "ambiguous_rule",
        "entry_id": "r1#01",
        "field": "",
        "quote": "筛选为空导出全部",
        "not_documented": False,
        "blocks_flow": False,
        "note": "不知道筛选口径按创建时间还是支付时间",
    }


def test_sim_review_packet_caps_and_shape():
    """模拟评测包：功能 ≥1；每功能走查 ≤12 步 / 摩擦 ≤6 / 验收核对 ≤12。"""

    packet = schemas.SimReviewPacket.model_validate(
        {"basis": "读了 main_flow", "features": [_sim_review_feature()]}
    )
    assert packet.features[0].verdict == "friction"

    with pytest.raises(ValidationError):
        schemas.SimReviewPacket.model_validate({"basis": "x", "features": []})

    steps = _sim_review_feature()
    steps["walkthrough"] = [
        {
            "step_index": i,
            "step_text": "走一步",
            "from_doc": "",
            "action": "做",
            "outcome": "ok",
            "need": "",
        }
        for i in range(schemas.MAX_SIM_REVIEW_WALKTHROUGH_STEPS + 1)
    ]
    with pytest.raises(ValidationError):
        schemas.SimReviewPacket.model_validate({"basis": "x", "features": [steps]})

    too_many = _sim_review_feature(
        frictions=[_sim_review_friction(i) for i in range(schemas.MAX_SIM_REVIEW_FRICTIONS + 1)]
    )
    with pytest.raises(ValidationError):
        schemas.SimReviewPacket.model_validate({"basis": "x", "features": [too_many]})


def test_sim_review_schema_field_sets_are_locked():
    """字段集锁死：多一个就说明又有人加"配置"。"""

    assert set(schemas.SimWalkStepDraft.model_fields) == {
        "step_index",
        "step_text",
        "from_doc",
        "action",
        "outcome",
        "need",
    }
    assert set(schemas.SimFrictionDraft.model_fields) == {
        "local_id",
        "kind",
        "entry_id",
        "field",
        "quote",
        "not_documented",
        "blocks_flow",
        "note",
    }
    assert set(schemas.SimAcceptanceDraft.model_fields) == {
        "local_id",
        "entry_id",
        "status",
        "note",
    }
    assert set(schemas.SimFeatureReviewDraft.model_fields) == {
        "feature_slug",
        "started",
        "walkthrough",
        "frictions",
        "acceptance_checks",
        "verdict",
        "verdict_because",
    }
    assert set(schemas.SimReviewPacket.model_fields) == {"basis", "features"}


def test_sim_review_schema_has_no_demographic_or_numeric_fields():
    """与 M10 同口径：schema 里没有可填人口学/分数/百分比的位置。"""

    banned = {
        "age", "gender", "sex", "region", "income", "education", "ethnicity",
        "score", "confidence", "percentage", "ratio", "satisfaction",
        "would_use_again",  # B1 砍掉：态度预测，与"不预测个体态度"的学术前提冲突
        "abandon_trigger",  # B1 砍掉：与 blocks_flow / verdict_because 重复
    }
    for model in (
        schemas.SimWalkStepDraft,
        schemas.SimFrictionDraft,
        schemas.SimAcceptanceDraft,
        schemas.SimFeatureReviewDraft,
        schemas.SimReviewPacket,
    ):
        assert not (set(model.model_fields) & banned), model.__name__


def test_seat_packet_accepts_four_web_requests_without_capping_them():
    """schema 只解析：上限由用例层在 attach 前按方截断（不在这里丢）。"""

    packet, overflow, discarded = schemas.parse_packet_lenient(
        json.dumps(
            {
                "basis": "读了文档",
                "opinions": [],
                "web_requests": [
                    {"query": f"第{index}条", "why": "要查外部事实"}
                    for index in range(1, 5)
                ],
            },
            ensure_ascii=False,
        ),
        schemas.SeatPacket,
        purpose="seat_review",
    )

    assert [item.query for item in packet.web_requests] == [
        "第1条",
        "第2条",
        "第3条",
        "第4条",
    ]
    assert overflow == ()
    assert discarded == ()
    schema = schemas.SeatPacket.model_json_schema()
    assert "maxItems" not in schema["properties"]["web_requests"]


def test_integration_packet_accepts_four_web_requests_without_capping_them():
    packet, overflow, discarded = schemas.parse_packet_lenient(
        json.dumps(
            {
                "questions": [],
                "list_items": [],
                "proposals": [],
                "web_requests": [
                    {"query": f"第{index}条", "why": "要查外部事实"}
                    for index in range(1, 5)
                ],
            },
            ensure_ascii=False,
        ),
        schemas.IntegrationPacket,
        purpose="integration",
    )

    assert [item.query for item in packet.web_requests] == [
        "第1条",
        "第2条",
        "第3条",
        "第4条",
    ]
    assert overflow == ()
    assert discarded == ()
    schema = schemas.IntegrationPacket.model_json_schema()
    assert "maxItems" not in schema["properties"]["web_requests"]


def test_web_request_draft_tolerates_missing_fields_so_the_use_case_can_drop_one():
    """缺 query / why 不该整包重装：默认空串交给用例层丢那一条 + 留痕。"""

    seat = schemas.SeatPacket.model_validate(
        {
            "basis": "读了文档",
            "opinions": [],
            "web_requests": [{"query": "缺 why"}],
        }
    )
    integration = schemas.IntegrationPacket.model_validate(
        {"web_requests": [{"why": "缺 query"}, {}]}
    )

    assert (seat.web_requests[0].query, seat.web_requests[0].why) == ("缺 why", "")
    assert [(item.query, item.why) for item in integration.web_requests] == [
        ("", "缺 query"),
        ("", ""),
    ]
