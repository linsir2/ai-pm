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
