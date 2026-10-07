"""模拟接进主闭环（M10-T9）：草案卡 → 跑一趟 → 收下为材料 → 带进下一轮 → 账本 `simulated`。

只脚本化模型；其余全真：Harness 的真领域 + 真用例 + 真图（`RoundRunner`，
`sim_inquiry` 命令走完整的图路由——用例直调测不到这一层）。
"""

import json

from pmstudio.application.commands import RoundCommand
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from tests.support.builders import PROJECT, ROUND_ID, SEAT
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "同类导出的常见上限",
                "evidence_refs": ["E1"],
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

DRAFT_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "exceptions",
                },
                "text": "无导出权限的用户不显示导出入口",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "订单含客户信息",
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

SIM_REPLY = json.dumps(
    {
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
        "unknowns": [],
    },
    ensure_ascii=False,
)


async def test_sim_loop_run_keep_carry_simulated() -> None:
    round2_claims = json.dumps(
        {
            "basis": "读了空文档",
            "claims": [
                {
                    "text": "按模拟的假设继续",
                    "raw_span": "按模拟的假设继续",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s10",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "沿用上一轮的席位"}],
        },
        ensure_ascii=False,
    )
    harness = Harness(
        replies={
            "claims": [CLAIMS_REPLY, round2_claims],
            "integration": DRAFT_REPLY,
            "sim_inquiry": SIM_REPLY,
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    harness.seed_project()
    ledger = harness.evidences.get(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="想做个订单导出的功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=harness.clock.now(),
    )
    harness.evidences.seed(ledger)

    # 第一轮走到底：草案卡（AWAITING_CONFIRM/DRAFT）
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
    harness.rounds.save(round_)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.waiting_on is WaitingOn.DRAFT

    # 草案卡上跑一趟模拟（走图：命令路由 + 动作锁 + 读回投影 Whole path）
    view = await harness.runner().run(
        RoundCommand(kind="sim_inquiry", round_id=str(round_id))
    )
    assert view.sim_inquiry is not None
    assert [item.local_id for item in view.sim_inquiry.hypotheses] == ["H1"]
    assert view.sim_inquiry.hypotheses[0].slice_id == "S1"

    # 收下 H1 为材料
    kept = await harness.keep_sim_inquiry()(round_id=round_id, chosen=("H1",))
    assert kept.kept == ("H1",)

    # 下一轮带上：账本多一条 SIMULATED（external_ref 指回模拟收下）
    view2 = await harness.runner().run(
        RoundCommand(
            kind="start",
            project_id=str(PROJECT),
            user_input="按模拟的假设继续",
            material_ids=(kept.material_id,),
        )
    )
    assert [item.material_id for item in view2.materials] == [kept.material_id]
    simulated = [
        item for item in harness.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.SIMULATED
    ]
    assert len(simulated) == 1
    assert simulated[0].source.external_ref == (
        f"simulation:inquiry:{round_id}#H1#c00"
    )
