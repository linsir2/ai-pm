"""模拟调研装配（M10）：包 → 切片/假设/盲区。账本之外的判断（去重/人口学窄词表）
是 T4 用例层的事，这里只判包内形状：空结果、切片挂载、local_id、grounding 一致性。"""

import pytest

from pmstudio.application.agents import schemas
from pmstudio.application.agents.sim_inquirer import sim_from_packet
from pmstudio.harness.errors import PacketParseError


def _packet(**overrides) -> schemas.SimInquiryPacket:
    base = {
        "basis": "读了 E1 的现状",
        "slices": [
            {
                "local_id": "S1",
                "situation": "运营月底要对账",
                "job": "按时间筛出订单",
                "trigger": "月底结账",
                "workaround": "导出全部再手工筛",
                "anchors": ["E1"],
            }
        ],
        "hypotheses": [
            {
                "local_id": "H1",
                "slice_id": "S1",
                "statement": "运营宁愿等一分钟也不愿分两次导",
                "grounding": "document",
                "based_on": ["E1"],
                "falsification": "观察到运营主动分多次小批量导出",
                "evidence_needed": "跟两场月底结账看实际操作",
            }
        ],
        "unknowns": [
            {
                "local_id": "U1",
                "statement": "节假日前的导出量会不会爆",
                "why_it_matters": "决定要不要做异步导出",
                "how_to_find_out": "拉去年节假日的导出日志",
            }
        ],
    }
    base.update(overrides)
    return schemas.SimInquiryPacket.model_validate(base)


def test_sim_from_packet_empty_is_failure() -> None:
    """无假设无盲区不算成功（筛#6 的包内一半；账本/去重是 T4 的事）。"""

    packet = _packet(hypotheses=[], unknowns=[])
    with pytest.raises(PacketParseError):
        sim_from_packet(packet, known_eids=frozenset({"E1"}))


def test_sim_from_packet_unknown_slice_is_reported_not_raised() -> None:
    """假设挂到不存在的切片上：不判死，进 ref_drops（重装一次仍犯才丢条，用例层管）。

    2026-10-07 真跑修正：设计口径是"丢条+留痕"（`02` §11.0.47），
    此前的实现把整包抛出——测试跟着实现写歪了，这里改回设计契约。
    """

    packet = _packet()
    packet.hypotheses[0].slice_id = "S9"
    slices, hypotheses, unknowns, ref_drops = sim_from_packet(
        packet, known_eids=frozenset({"E1"})
    )
    assert hypotheses == (), "挂不上的假设不产出领域对象"
    assert slices != (), "切片本身没问题，照常产出"
    assert any("S9" in reason for _handle, reason in ref_drops)


def test_sim_from_packet_unknown_anchor_is_reported_and_cascades() -> None:
    """切片锚点落不回账本：切片被丢，挂在它下面的假设连带丢（各带一条原因）。"""

    packet = _packet()
    packet.slices[0].anchors = ["E99"]
    slices, hypotheses, unknowns, ref_drops = sim_from_packet(
        packet, known_eids=frozenset({"E1"})
    )
    assert slices == []
    assert hypotheses == ()
    assert unknowns != (), "盲区不挂切片，不受连累"
    reasons = [reason for _handle, reason in ref_drops]
    assert any("E99" in reason for reason in reasons), reasons
    assert any("切片 S1" in handle for handle, _reason in ref_drops)


def test_sim_from_packet_keeps_good_items_beside_bad_ones() -> None:
    """坏的和好的一起出现：坏的丢、好的留（丢条不是连坐，是逐条判）。"""

    packet = _packet(
        slices=[
            {
                "local_id": "S1",
                "situation": "锚点坏的切片",
                "job": "x",
                "trigger": "x",
                "workaround": "x",
                "anchors": ["s8"],
            },
            {
                "local_id": "S2",
                "situation": "锚点好的切片",
                "job": "x",
                "trigger": "x",
                "workaround": "x",
                "anchors": ["E1"],
            },
        ],
        hypotheses=[
            {
                "local_id": "H1",
                "slice_id": "S1",
                "statement": "挂坏切片",
                "grounding": "document",
                "based_on": ["E1"],
                "falsification": "x",
                "evidence_needed": "x",
            },
            {
                "local_id": "H2",
                "slice_id": "S2",
                "statement": "挂好切片",
                "grounding": "document",
                "based_on": ["E1"],
                "falsification": "x",
                "evidence_needed": "x",
            },
        ],
        unknowns=[],
    )
    slices, hypotheses, unknowns, ref_drops = sim_from_packet(
        packet, known_eids=frozenset({"E1"})
    )
    assert [item["local_id"] for item in slices] == ["S2"]
    assert [item.local_id for item in hypotheses] == ["H2"]
    assert [handle for handle, _reason in ref_drops] == ["模拟切片 S1", "模拟假设 H1"]


def test_sim_from_packet_invented_with_refs_is_failure() -> None:
    """凭空推演带依据编号：领域对象直接拒（复用 Hypothesis，不在装配层重写一遍）。"""

    packet = _packet()
    packet.hypotheses[0].grounding = "invented"
    packet.hypotheses[0].based_on = ["E1"]
    with pytest.raises(PacketParseError):
        sim_from_packet(packet, known_eids=frozenset({"E1"}))


def test_sim_from_packet_fills_blank_local_ids() -> None:
    """空 local_id 按顺序补 S1/H1/U1（三类各自编号，与 M8 的 H/B 分开）。"""

    packet = _packet()
    packet.slices[0].local_id = ""
    packet.hypotheses[0].local_id = ""
    packet.unknowns[0].local_id = ""
    slices, hypotheses, unknowns, _drops = sim_from_packet(
        packet, known_eids=frozenset({"E1"})
    )
    assert slices[0]["local_id"] == "S1"
    assert hypotheses[0].local_id == "H1"
    assert unknowns[0]["local_id"] == "U1"


def test_sim_from_packet_duplicate_id_is_failure() -> None:
    """三类共用一个 id 空间：S1 和 H1 撞名也算重复（与 M8 的 seen 同理）。"""

    packet = _packet()
    packet.hypotheses[0].local_id = "S1"
    with pytest.raises(PacketParseError, match="S1"):
        sim_from_packet(packet, known_eids=frozenset({"E1"}))


def test_sim_inquiry_agent_call_binding() -> None:
    """装配声明：purpose/kind/prompt/packet 四件套，purpose 与 M8 的 inquiry 必须不同；
    接地三源（文档 + 主张 + 材料）都在槽位里（真跑抓过只渲染文档的坑）。"""

    from pmstudio.application.agents.inquirer import Inquirer
    from pmstudio.application.agents.sim_inquirer import SimInquiryAgent

    assert SimInquiryAgent.definition.packet is schemas.SimInquiryPacket
    assert SimInquiryAgent.definition.purpose == "sim_inquiry"
    assert SimInquiryAgent.definition.purpose != Inquirer.definition.purpose
    assert SimInquiryAgent.definition.prompt == "sim_inquiry"
    assert {"document_state", "claims", "external_material"} <= set(
        SimInquiryAgent.definition.slots
    )
