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
