"""报文容器上限与"超限不判死"。

真跑（`.codex/runs/2026-09-28/报告.md` F2/F4/F11）实测的三次整轮判死：
席位意见 10>8、提案 44>30、未采用意见申报 35>30。这里钉住新口径：
上限装得下一棵完整功能树，超限只截断并报告，不再判死整包。
"""

import json

import pytest
from annotated_types import MaxLen

from pmstudio.application.agents import schemas
from pmstudio.application.errors import PacketParseError


def cap(model, field_name: str) -> int:
    metadata = model.model_fields[field_name].metadata
    return next(item.max_length for item in metadata if isinstance(item, MaxLen))


def test_caps_fit_a_complete_feature_tree():
    # 一棵功能树 12 个字段；一轮至少要装得下 4 棵（48）+ 章节级内容
    assert cap(schemas.IntegrationPacket, "proposals") == 60
    # 7 个席位 × 单席位 12 条：这是"一条都不许默默丢掉"的数学上界
    assert cap(schemas.IntegrationPacket, "dropped_opinions") == 84
    # 提示词允许 5 条判断 + 7 条疑问
    assert cap(schemas.SeatPacket, "opinions") == 12


def proposal(index: int) -> dict:
    return {
        "op": "add",
        "target": {"zone": "body", "section_id": "s1"},
        "text": f"第 {index} 条",
        "origin_type": "derived",
        "evidence_refs": ["E1"],
    }


def packet_json(count: int) -> str:
    return json.dumps(
        {"proposals": [proposal(i) for i in range(count)], "dropped_opinions": []},
        ensure_ascii=False,
    )


def test_forty_four_proposals_no_longer_explode():
    """真跑实测：CRF 那轮 44 条提案整轮被毙，内容一条没落地。"""

    packet, overflow = schemas.parse_packet_trimmed(
        packet_json(44), schemas.IntegrationPacket, purpose="integration"
    )

    assert len(packet.proposals) == 44
    assert overflow == ()


def test_seventy_proposals_are_trimmed_to_the_cap_and_reported():
    packet, overflow = schemas.parse_packet_trimmed(
        packet_json(70), schemas.IntegrationPacket, purpose="integration"
    )

    assert len(packet.proposals) == 60
    assert packet.proposals[0].text == "第 0 条"  # 保留模型给的前 60 条（顺序即优先级）
    assert overflow == (schemas.Overflow(container="proposals", kept=60, dropped=10),)


def test_trimmed_parse_still_rejects_real_shape_errors():
    with pytest.raises(PacketParseError) as exc:
        schemas.parse_packet_trimmed(
            '{"proposals": [{"op": "add"}]}',
            schemas.IntegrationPacket,
            purpose="integration",
        )

    assert "过不了 schema" in exc.value.message


def test_strict_parse_carries_the_overflow_detail_for_the_repair_round():
    """重装指引要知道"给了几条、上限几条"——严格解析那条路也得带上它。"""

    with pytest.raises(PacketParseError) as exc:
        schemas.parse_packet(packet_json(70), schemas.IntegrationPacket, purpose="integration")

    assert exc.value.details["overflow"] == ("proposals", 70, 60)


# —— 顶层数组：模型漏了外壳（真跑留样 15 / 66） ——

#: 真跑里那次数组的第一个元素（原样抄下来，只截短了 why）
REAL_QUESTION = {
    "title": "三类通知的具体模板内容是什么？",
    "why": "通知模板决定接收方能执行的动作",
    "consequence_if_skipped": "通知内容无法实现",
    "target": {
        "zone": "body",
        "section_id": "s5",
        "feature_slug": "notification",
        "field": "io",
    },
    "type": "text",
    "kind": "gap",
    "evidence_refs": ["E11"],
    "from_opinions": ["seat.notification:O1"],
}

MINIMAL_ITEM = {
    "questions": {
        "kind": "gap",
        "title": "t",
        "why": "w",
        "consequence_if_skipped": "c",
        "target": {"zone": "body", "section_id": "s1"},
    },
    "list_items": {
        "text": "t",
        "target": {"zone": "body", "section_id": "s1"},
        "reason": "r",
    },
    "proposals": {
        "op": "add",
        "target": {"zone": "body", "section_id": "s1"},
        "text": "t",
        "origin_type": "derived",
        "evidence_refs": ["E1"],
    },
    "dropped_opinions": {"handle": "seat.x:O1", "reason": "r"},
}


def test_signatures_are_mutually_exclusive():
    """四个容器的判别键必须两两不撞——撞了就是猜，不是重建。"""

    mapping = {
        name: schemas._container_of_item(item, model=schemas.IntegrationPacket)
        for name, item in MINIMAL_ITEM.items()
    }

    assert mapping == {name: name for name in MINIMAL_ITEM}


def test_top_level_array_of_questions_is_rebuilt():
    """真跑实测：模型把 5 道题直接当成整包回回来 → 整轮 502（留样 15）。"""

    payload = json.dumps([REAL_QUESTION, REAL_QUESTION], ensure_ascii=False)

    packet, overflow = schemas.parse_packet_trimmed(
        payload, schemas.IntegrationPacket, purpose="integration"
    )

    assert len(packet.questions) == 2
    assert packet.proposals == []
    assert packet.list_items == []
    assert packet.dropped_opinions == []
    assert overflow == ()


def test_top_level_array_of_mixed_items_is_rebuilt_element_wise():
    payload = json.dumps(
        [
            MINIMAL_ITEM["questions"],
            MINIMAL_ITEM["proposals"],
            MINIMAL_ITEM["dropped_opinions"],
        ],
        ensure_ascii=False,
    )

    packet, _ = schemas.parse_packet_trimmed(
        payload, schemas.IntegrationPacket, purpose="integration"
    )

    assert len(packet.questions) == 1
    assert len(packet.proposals) == 1
    assert len(packet.dropped_opinions) == 1


def test_array_with_an_unclassifiable_element_still_fails_with_a_shape_hint():
    payload = json.dumps([MINIMAL_ITEM["questions"], {"foo": "bar"}], ensure_ascii=False)

    with pytest.raises(PacketParseError) as exc:
        schemas.parse_packet_trimmed(payload, schemas.IntegrationPacket, purpose="integration")

    assert exc.value.details["packet_shape"] == {
        "got": "array",
        "expected_keys": ("questions", "list_items", "proposals", "dropped_opinions"),
    }


def test_claims_packet_array_is_not_guessed():
    """claims 必填 basis + 至少一个席位：重建就得凭空造字段，所以只判死（有重装兜）。"""

    with pytest.raises(PacketParseError) as exc:
        schemas.parse_packet(
            json.dumps([{"text": "t", "raw_span": "t", "label": "need"}]),
            schemas.ClaimsPacket,
            purpose="claims",
        )

    # 2026-09-29：claims 报文加了 `contradictions`（互斥对子，T7）——形状清单跟着长一条
    # 2026-09-30：又加了 `web_queries`（联网检索词提名，C-2b）——再长一条
    assert exc.value.details["packet_shape"]["expected_keys"] == (
        "basis",
        "claims",
        "seats",
        "contradictions",
        "web_queries",
    )


def test_rebuilt_array_still_honours_the_cap():
    """重建之后照旧走截断：70 条提案的数组不该因为"换了个壳"就绕过上限。"""

    payload = json.dumps([MINIMAL_ITEM["proposals"]] * 70, ensure_ascii=False)

    packet, overflow = schemas.parse_packet_trimmed(
        payload, schemas.IntegrationPacket, purpose="integration"
    )

    assert len(packet.proposals) == 60
    assert overflow == (schemas.Overflow(container="proposals", kept=60, dropped=10),)


def test_empty_top_level_array_is_not_rebuilt():
    """空数组不重建（L2：空返回不等于没意见）——照旧判死，由重装兜。"""

    with pytest.raises(PacketParseError):
        schemas.parse_packet_trimmed("[]", schemas.IntegrationPacket, purpose="integration")


# —— 字段级归一化（P8）：形状能机械判的就地修，内容与写入语义一律不动 ——


def seat_packet_json(*, evidence_refs: object) -> str:
    return json.dumps(
        {
            "basis": "读了 s1–s13 与 E1",
            "opinions": [
                {
                    "local_id": "O1",
                    "form": "judgment",
                    "about": "量级",
                    "claim": "60 秒内完成",
                    "because": "同类做法",
                    "evidence_refs": evidence_refs,
                }
            ],
        },
        ensure_ascii=False,
    )


def test_null_list_field_becomes_empty_list():
    """真跑实测（P7 验证留样 7）：席位把 `evidence_refs` 写成 `null` → 整席位判死。"""

    packet, _ = schemas.parse_packet_trimmed(
        seat_packet_json(evidence_refs=None), schemas.SeatPacket, purpose="seat_review"
    )

    assert packet.opinions[0].evidence_refs == []


def test_single_string_becomes_a_one_item_list():
    packet, _ = schemas.parse_packet_trimmed(
        seat_packet_json(evidence_refs="E1"), schemas.SeatPacket, purpose="seat_review"
    )

    assert packet.opinions[0].evidence_refs == ["E1"]


def test_null_on_a_defaulted_field_falls_back_to_the_default():
    """L3：**非 Optional、有默认值**的字段收到 `null` → 用默认值（P8 口径）。

    这个例子原来用的是 `kind`（`null → gap`）；2026-09-29 P11 C3 把 `kind` 改成**必填**，
    于是它退出了 L3 的适用范围——例子换成同样"有默认值的分类提示"的 `type`。
    """

    raw = json.dumps(
        {
            "questions": [
                {
                    "kind": "gap",
                    "type": None,
                    "title": "t",
                    "why": "w",
                    "consequence_if_skipped": "c",
                    "target": {"zone": "body", "section_id": "s1"},
                }
            ],
            "list_items": None,
            "proposals": [],
            "dropped_opinions": None,
        },
        ensure_ascii=False,
    )

    packet, _ = schemas.parse_packet_trimmed(
        raw, schemas.IntegrationPacket, purpose="integration"
    )

    assert packet.questions[0].type == "single"
    assert packet.questions[0].kind == "gap"
    assert packet.list_items == []
    assert packet.dropped_opinions == []


def test_write_semantics_fields_are_never_defaulted():
    """`op` 写歪了可能把 remove 变成 add——这类宁可失败（有重装兜）。"""

    raw = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": None,
                    "entry_id": "r1#01",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": "撤下这条",
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                }
            ],
        },
        ensure_ascii=False,
    )

    with pytest.raises(PacketParseError):
        schemas.parse_packet_trimmed(raw, schemas.IntegrationPacket, purpose="integration")


def test_seat_opinion_form_is_never_defaulted():
    """`form` 决定这条意见进不进账本（有没有编号）——不许用默认值悄悄改。"""

    raw = json.dumps(
        {
            "basis": "读了 s1–s13",
            "opinions": [
                {
                    "local_id": "O1",
                    "form": None,
                    "about": "a",
                    "claim": "c",
                    "because": "b",
                    "evidence_refs": [],
                }
            ],
        },
        ensure_ascii=False,
    )

    with pytest.raises(PacketParseError):
        schemas.parse_packet_trimmed(raw, schemas.SeatPacket, purpose="seat_review")


def test_content_fields_are_never_invented():
    raw = json.dumps(
        {
            "questions": [],
            "list_items": [],
            "proposals": [
                {
                    "op": "add",
                    "target": {"zone": "body", "section_id": "s1"},
                    "text": None,
                    "origin_type": "derived",
                    "evidence_refs": ["E1"],
                }
            ],
        },
        ensure_ascii=False,
    )

    with pytest.raises(PacketParseError):
        schemas.parse_packet_trimmed(raw, schemas.IntegrationPacket, purpose="integration")


def test_array_rebuild_and_field_normalisation_compose():
    """顶层是数组 + 里面字段写歪：两道归一化要能叠加（P7 × P8）。"""

    payload = json.dumps(
        [
            {"handle": "seat.x:O1", "reason": "与文档重复"},
            {"text": "t", "target": {"zone": "body", "section_id": "s1"}, "reason": "r"},
        ],
        ensure_ascii=False,
    )

    packet, _ = schemas.parse_packet_trimmed(
        payload, schemas.IntegrationPacket, purpose="integration"
    )

    assert len(packet.dropped_opinions) == 1
    assert len(packet.list_items) == 1


# —— 主张（claims）：真跑里唯一一条"没有 /retry 可点"的判死（P21 F16-a） ——


def claims_packet_json(count: int) -> str:
    return json.dumps(
        {
            "basis": "读了文档与本轮输入",
            "claims": [
                {
                    "text": f"第 {index} 件事",
                    "raw_span": "用户原话",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s1",
                }
                for index in range(1, count + 1)
            ],
            "seats": [{"seat_id": "seat.rules", "why": "要有人表态"}],
        },
        ensure_ascii=False,
    )


def test_claims_cap_fits_a_long_input():
    """真跑 `.codex/runs/2026-10-01/报告.md` F16：一份"把前面几章补齐"的长输入
    被切出 17 条主张，12 条上限把整轮判死——用户连轮次都建不出来（起轮没有 `/retry`）。
    """

    assert cap(schemas.ClaimsPacket, "claims") == 20


def test_seventeen_claims_are_not_trimmed_at_all():
    packet, overflow = schemas.parse_packet_trimmed(
        claims_packet_json(17), schemas.ClaimsPacket, purpose="claims"
    )

    assert len(packet.claims) == 17
    assert overflow == ()


def test_twenty_five_claims_are_trimmed_to_the_cap_and_reported():
    packet, overflow = schemas.parse_packet_trimmed(
        claims_packet_json(25), schemas.ClaimsPacket, purpose="claims"
    )

    assert len(packet.claims) == 20
    assert packet.claims[0].text == "第 1 件事"  # 保留模型给的前 20 条（顺序即优先级）
    assert overflow == (schemas.Overflow(container="claims", kept=20, dropped=5),)


def test_trimmed_claims_parse_still_rejects_real_shape_errors():
    """'超限不判死'只对 too_long 生效；别的毛病照旧抛（不许放松）。"""

    broken = json.dumps(
        {"basis": "读了文档", "claims": [{"text": "少了必填字段"}], "seats": []},
        ensure_ascii=False,
    )

    with pytest.raises(PacketParseError):
        schemas.parse_packet_trimmed(broken, schemas.ClaimsPacket, purpose="claims")


# —— 容器里的"非对象元素"：真跑里一个占位符把整轮打掉（2026-10-01 审计 F17） ——


def seat_packet_with(elements: list) -> str:
    return json.dumps(
        {"basis": "读了 s1–s13 与 E1", "opinions": elements}, ensure_ascii=False
    )


def opinion(local_id: str, *, about: str = "量级") -> dict:
    return {
        "local_id": local_id,
        "form": "judgment",
        "about": about,
        "claim": "单次导出应在 60 秒内完成",
        "because": "量级在 1 万级",
        "evidence_refs": [],
    }


def test_a_non_object_opinion_is_dropped_with_its_position():
    """现场：`opinions[1]` 是模型写的占位符字符串 `"O2_placeholder"`（报告 F17）。

    它不是"内容错"（那条根本没有内容可判），是**形状错**——与"超限截断"同一套语义：
    丢掉那一条 + 把位置报出来，整轮照跑。
    """

    packet, overflow, discarded = schemas.parse_packet_lenient(
        seat_packet_with([opinion("O1"), "O2_placeholder"]),
        schemas.SeatPacket,
        purpose="seat_review",
    )

    assert [item.local_id for item in packet.opinions] == ["O1"]
    assert overflow == ()
    assert [(item.container, item.index) for item in discarded] == [("opinions", 2)]
    assert "不是对象" in discarded[0].reason


def test_trimmed_parse_still_refuses_to_drop_silently():
    """边界钉子：**摘掉一条**这件事只开给有留痕出口的调用方（`parse_packet_lenient`）。

    `parse_packet_trimmed` 剩下的调用点是整合者，它这一趟还没有"把丢掉的提案写进留痕"的出口
    ——让它静默摘，就等于破"一条都不许默默丢掉"这条规矩。所以它照旧抛。

    （`parse_packet_lenient` 的摘除行为由上面那条用例钉着；两者是同一份实现的两种开关。）
    """

    with pytest.raises(PacketParseError):
        schemas.parse_packet_trimmed(
            seat_packet_with([opinion("O1"), "placeholder"]),
            schemas.SeatPacket,
            purpose="seat_review",
        )

    packet, overflow = schemas.parse_packet_trimmed(
        seat_packet_with([opinion("O1")]), schemas.SeatPacket, purpose="seat_review"
    )

    assert overflow == ()
    assert len(packet.opinions) == 1  # 签名与返回值形状都没变（2 元组）


def test_dropping_a_placeholder_does_not_mask_a_real_field_error():
    """反例必须留着：元素**是对象但字段写错** → 照旧抛，不许被"丢那一条"掩盖。"""

    broken = opinion("O2")
    broken["claim"] = None

    with pytest.raises(PacketParseError):
        schemas.parse_packet_lenient(
            seat_packet_with([opinion("O1"), "placeholder", broken]),
            schemas.SeatPacket,
            purpose="seat_review",
        )


def test_a_fully_placeholder_claims_packet_is_still_an_error():
    """主张是地基：一条都切不出来时不许"空着往下跑"（`min_length` 照旧生效）。"""

    raw = json.dumps(
        {
            "basis": "读了文档",
            "claims": ["placeholder"],
            "seats": [{"seat_id": "seat.rules", "why": "要有人表态"}],
        },
        ensure_ascii=False,
    )

    with pytest.raises(PacketParseError):
        schemas.parse_packet_lenient(raw, schemas.ClaimsPacket, purpose="claims")


def test_discard_and_overflow_are_reported_together():
    """两种形状错叠加：丢掉第 1 条 + 剩下的超限截断，两份账都要报，互不掩盖。"""

    raw = seat_packet_with(["placeholder", *[opinion(f"O{i}") for i in range(1, 14)]])

    packet, overflow, discarded = schemas.parse_packet_lenient(
        raw, schemas.SeatPacket, purpose="seat_review"
    )

    assert len(packet.opinions) == 12
    assert overflow == (schemas.Overflow(container="opinions", kept=12, dropped=1),)
    assert [item.index for item in discarded] == [1]


# —— 整合那一包也可以摘非对象元素了（P23 B 案：先给它补上留痕出口） ——


def integration_packet_with(**overrides: object) -> str:
    payload: dict[str, object] = {
        "questions": [],
        "list_items": [],
        "proposals": [],
        "dropped_opinions": [],
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def integration_proposal(index: int = 1) -> dict:
    return {
        "op": "add",
        "target": {"zone": "body", "section_id": "s1"},
        "text": f"第 {index} 条",
        "origin_type": "derived",
        "evidence_refs": ["E1"],
    }


def test_a_non_object_proposal_is_dropped_with_container_and_position():
    packet, overflow, discarded = schemas.parse_packet_lenient(
        integration_packet_with(proposals=[integration_proposal(), "p2_placeholder"]),
        schemas.IntegrationPacket,
        purpose="integration",
    )

    assert [item.text for item in packet.proposals] == ["第 1 条"]
    assert overflow == ()
    assert [(item.container, item.index) for item in discarded] == [("proposals", 2)]


def test_a_non_object_question_is_dropped_too():
    packet, _, discarded = schemas.parse_packet_lenient(
        integration_packet_with(
            questions=[
                {
                    "kind": "gap",
                    "title": "第 1 题",
                    "why": "要定",
                    "consequence_if_skipped": "先按默认",
                    "target": {"zone": "body", "section_id": "s1"},
                },
                "q2_placeholder",
            ]
        ),
        schemas.IntegrationPacket,
        purpose="integration",
    )

    assert [item.title for item in packet.questions] == ["第 1 题"]
    assert [(item.container, item.index) for item in discarded] == [("questions", 2)]


def test_an_object_with_a_bad_field_still_raises_for_integration():
    """反例：元素是对象、字段写错 → 照旧抛（摘非对象不许掩盖别的错）。"""

    broken = integration_proposal()
    broken["text"] = None

    with pytest.raises(PacketParseError):
        schemas.parse_packet_lenient(
            integration_packet_with(proposals=[integration_proposal(), broken]),
            schemas.IntegrationPacket,
            purpose="integration",
        )


def test_the_dispositions_packet_carries_only_dropped_opinions():
    """归因补交代那条小调用的回答形状：**只有** dropped_opinions（`02` §11.0.38）。

    为什么不让它回"我用了"：那等于让模型自己把"没人交代"这条指标清零——
    `04-未决.md` C5 早就否掉了"赌模型老实交代"。
    """

    raw = json.dumps(
        {"dropped_opinions": [{"handle": "seat.x:O1", "reason": "与文档重复"}]},
        ensure_ascii=False,
    )

    packet, overflow, discarded = schemas.parse_packet_lenient(
        raw, schemas.DispositionsPacket, purpose="opinion_dispositions"
    )

    assert [item.handle for item in packet.dropped_opinions] == ["seat.x:O1"]
    assert set(schemas.DispositionsPacket.model_fields) == {"dropped_opinions"}
    assert overflow == ()
    assert discarded == ()
