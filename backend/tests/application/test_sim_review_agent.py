"""模拟评测智能体（M11-T3）：一个 persona 一次调用；转换层补 id、锁 Patch 形状。

转换只做**机械可判**的事：补 local_id（F/A 按功能内顺序）、查重、原样落字段。
"引用文档里什么东西存在不存在"是筛的活（`sim_review.review_issues`，需要文档锚点）——
所以这里不抛引用问题，只在用了重复的 local_id 时炸（那是结构问题，重装也救不回语义）。
"""

import json

import pytest

from pmstudio.application.agents.persona_simulator import (
    PersonaSimulatorAgent,
    render_persona,
    render_targets,
    review_from_packet,
)
from pmstudio.application.agents.schemas import SimReviewPacket
from pmstudio.harness.errors import PacketParseError
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway


def _feature(slug: str = "order-export", *, frictions: list | None = None) -> dict:
    return {
        "feature_slug": slug,
        "started": True,
        "walkthrough": [
            {
                "step_index": 1,
                "step_text": "进入订单列表",
                "from_doc": "s5/order-export.main_flow#1",
                "action": "打开订单列表页",
                "outcome": "ok",
                "need": "",
            }
        ],
        "frictions": frictions
        if frictions is not None
        else [
            {
                "local_id": "",
                "kind": "ambiguous_rule",
                "entry_id": "r1#01",
                "field": "",
                "quote": "筛选为空导出全部",
                "not_documented": False,
                "blocks_flow": False,
                "note": "不知道筛选口径按创建时间还是支付时间",
            }
        ],
        "acceptance_checks": [
            {"local_id": "", "entry_id": "r1#02", "status": "unverified", "note": ""}
        ],
        "verdict": "friction",
        "verdict_because": ["F1"],
    }


def _packet(features: list | None = None) -> dict:
    return {"basis": "读了 s5 的 main_flow 与 acceptance", "features": features or [_feature()]}


def test_review_from_packet_fills_ids_and_keeps_shape() -> None:
    packet = SimReviewPacket.model_validate(_packet())

    review = review_from_packet(packet)

    assert set(review) == {"features"}, "persona 身份由智能体补，转换层只管走查内容"
    feature = review["features"][0]
    assert feature["feature_slug"] == "order-export"
    assert feature["frictions"][0]["local_id"] == "F1"
    assert feature["acceptance_checks"][0]["local_id"] == "A1"
    assert feature["verdict"] == "friction"
    assert feature["verdict_because"] == ["F1"]


def test_review_from_packet_ids_restart_per_feature() -> None:
    two = [_feature("order-export"), _feature("checkout")]
    packet = SimReviewPacket.model_validate(_packet(two))

    review = review_from_packet(packet)

    ids = [item["frictions"][0]["local_id"] for item in review["features"]]
    assert ids == ["F1", "F1"], "编号作用域是功能：外部引用地址带功能段，不会撞"


def test_duplicate_friction_id_within_feature_raises() -> None:
    feature = _feature(
        frictions=[
            {"local_id": "F1", "kind": "dead_end", "entry_id": "r1#01", "field": "",
             "quote": "", "not_documented": True, "blocks_flow": True, "note": "走不通"},
            {"local_id": "F1", "kind": "dead_end", "entry_id": "r1#02", "field": "",
             "quote": "", "not_documented": True, "blocks_flow": True, "note": "还是走不通"},
        ]
    )
    packet = SimReviewPacket.model_validate(_packet([feature]))

    with pytest.raises(PacketParseError):
        review_from_packet(packet)


def test_render_persona_and_targets_are_prompt_ready() -> None:
    class _Persona:
        persona_id = "persona.first_touch"
        title = "首次上手者"
        situation = "第一次使用，没有背景"
        goal = "独立走完一次完整任务"
        behavior = "按主流程从头走，卡一次就停"
        probes = "入口、前置条件、每步反馈"

    persona_text = render_persona(_Persona())

    assert "首次上手者" in persona_text
    assert "独立走完一次完整任务" in persona_text
    assert "卡一次就停" in persona_text
    assert render_targets(("order-export", "checkout")) == "feature#order-export、feature#checkout"


async def test_agent_run_calls_model_with_persona_slots() -> None:
    payload = json.dumps(_packet(), ensure_ascii=False)
    gateway = ScriptedModelGateway(replies={"sim_review": payload})
    prompts = RecordingPrompts()
    agent = PersonaSimulatorAgent(model=gateway, prompts=prompts)

    class _Persona:
        persona_id = "persona.edge_maker"
        title = "异常状态制造者"
        situation = "故意走边界"
        goal = "把流程搞崩"
        behavior = "每步先试边界输入"
        probes = "异常字段、错误提示、回滚"

    outcome = await agent.run(
        persona=_Persona(),
        feature_slugs=("order-export",),
        document_state="## s5（v1）\n- feature#order-export\n",
        claims="- [E1] 导出订单",
        round_id="r-1",
        packet_slot="p1-a1",
    )

    assert set(outcome.review) == {"persona_id", "title", "features"}
    assert outcome.review["persona_id"] == "persona.edge_maker"
    assert outcome.review["title"] == "异常状态制造者"
    assert outcome.review["features"][0]["frictions"][0]["local_id"] == "F1"
    assert gateway.purposes == ["sim_review"]
    metadata = gateway.requests[0].metadata
    assert metadata["persona_id"] == "persona.edge_maker"
    slots = prompts.last("sim_review")
    assert "异常状态制造者" in slots["persona"]
    assert "feature#order-export" in slots["targets"]
    assert "导出订单" in slots["claims"]
