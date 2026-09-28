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

    assert exc.value.details["packet_shape"]["expected_keys"] == ("basis", "claims", "seats")


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
