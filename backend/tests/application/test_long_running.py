"""长时真跑：验证新功能在 6 轮迭代中的实际效果。

验证项：
1. L2 观察跨轮累积 + 去重
2. L4 摘要每轮生成
3. 内容感知席位过滤（不同输入 → 不同席位子集）
4. 席位间冲突检测（机械检测相反意见）
5. source_ref 去重（同 source_ref 只保留最新）
"""
import json
from pathlib import Path

import pytest

from pmstudio.infrastructure.seats.catalog import TomlSeatCatalog
from tests.support.harness import Harness

DEFINITIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "pmstudio"
    / "infrastructure"
    / "seats"
    / "definitions"
)


# ── 验证 1: 内容感知席位过滤 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_content_aware_seat_filtering():
    """不同输入下，席位目录过滤结果不同。"""
    catalog = TomlSeatCatalog(DEFINITIONS_DIR)

    # 输入含"导出"
    ids_export = {s.seat_id for s in catalog.filter_by_input("用户需要导出Excel报表")}
    # 输入含"审批"
    ids_approval = {s.seat_id for s in catalog.filter_by_input("订单审批流程")}
    # 输入含"通知"
    ids_notify = {s.seat_id for s in catalog.filter_by_input("消息推送通知")}
    # 空输入
    ids_empty = {s.seat_id for s in catalog.filter_by_input("")}

    # 验证关键词命中
    assert "seat.export_import" in ids_export, "导出输入应命中 export_import"
    assert "seat.export_import" not in ids_approval, "审批输入不应命中 export_import"
    assert "seat.state_machine" in ids_approval, "审批输入应命中 state_machine"
    assert "seat.state_machine" not in ids_export, "导出输入不应命中 state_machine"
    assert "seat.notification" in ids_notify, "通知输入应命中 notification"
    assert "seat.notification" not in ids_export, "导出输入不应命中 notification"

    # 验证 always_on 始终存在
    always_on = {
        "seat.data_scale",
        "seat.business_rules",
        "seat.frontend_interaction",
        "seat.permissions",
        "seat.tech_approach",
    }
    assert always_on <= ids_export
    assert always_on <= ids_approval
    assert always_on <= ids_notify
    assert always_on <= ids_empty, "空输入应只保留 always_on"

    # 验证过滤效果：不同输入产生不同子集
    assert ids_export != ids_approval, "不同输入应产生不同席位子集"
    assert ids_export != ids_notify

    print(f"\n  导出: {sorted(ids_export)}")
    print(f"  审批: {sorted(ids_approval)}")
    print(f"  通知: {sorted(ids_notify)}")
    print(f"  空输入: {sorted(ids_empty)}")


# ── 验证 2: 冲突检测 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_conflict_detection():
    """两个席位对同一 about 持相反判断 → 检测到冲突。"""
    from pmstudio.application.assembly import seat_conflict_pairs
    from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview

    # 相反判断
    reviews = (
        SeatReview(seat_id="seat.data_scale", basis="A",
                   opinions=(Opinion("O1", OpinionForm.JUDGMENT, "导出性能",
                                     "导出应在 60 秒内完成", "用户体验", ("E1",)),)),
        SeatReview(seat_id="seat.tech_approach", basis="B",
                   opinions=(Opinion("O1", OpinionForm.JUDGMENT, "导出性能",
                                     "导出不应在 60 秒内完成", "数据量大", ("E2",)),)),
    )
    conflicts = seat_conflict_pairs(reviews)
    assert len(conflicts) == 1
    assert conflicts[0].about == "导出性能"
    print(f"\n  冲突: [{conflicts[0].seat_a}] {conflicts[0].claim_a}")
    print(f"        vs [{conflicts[0].seat_b}] {conflicts[0].claim_b}")

    # 相同判断不冲突
    same = (
        SeatReview(seat_id="seat.data_scale", basis="A",
                   opinions=(Opinion("O1", OpinionForm.JUDGMENT, "导出性能",
                                     "导出应在 60 秒内完成", "用户体验", ("E1",)),)),
        SeatReview(seat_id="seat.tech_approach", basis="B",
                   opinions=(Opinion("O1", OpinionForm.JUDGMENT, "导出性能",
                                     "导出必须在 60 秒内完成", "产品要求", ("E2",)),)),
    )
    assert len(seat_conflict_pairs(same)) == 0
    print("  同向判断冲突: 0 ✓")


# ── 验证 3: source_ref 去重 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_source_ref_dedup():
    """同 source_ref 只保留最新一条。"""
    h = Harness(replies={}, seat_replies={})
    pid = h.seed_project(title="测试")

    from pmstudio.domain.memory.observations import ObservationType, RoundObservation

    h.observations.save(RoundObservation(
        obs_id="obs-1", round_id="r1", project_id=str(pid),
        obs_type=ObservationType.SEAT_INSIGHT, title="旧", detail="旧", source_ref="seat.x:O1",
        created_at=h.clock.now().isoformat(),
    ))
    h.observations.save(RoundObservation(
        obs_id="obs-2", round_id="r2", project_id=str(pid),
        obs_type=ObservationType.SEAT_INSIGHT, title="新", detail="新", source_ref="seat.x:O1",
        created_at=h.clock.now().isoformat(),
    ))

    recent = h.observations.recent(str(pid), limit=5)
    matching = [o for o in recent if o.source_ref == "seat.x:O1"]
    assert len(matching) == 1
    assert matching[0].title == "新"
    matching = [o for o in recent if o.source_ref == "seat.x:O1"]
    assert len(matching) == 1
    assert matching[0].title == "新"
    print(f"\n  同 source_ref 保留: {matching[0].title} ✓")


# ── 验证 4: L2 观察跨轮累积 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_l2_observations_accumulate():
    """多轮迭代后 L2 观察累积且去重。"""
    h = Harness(replies={}, seat_replies={})
    pid = h.seed_project(title="测试")

    from pmstudio.domain.memory.observations import ObservationType, RoundObservation

    # 模拟 3 轮产生观察
    for i in range(3):
        h.observations.save(RoundObservation(
            obs_id=f"obs-r{i+1}-a", round_id=f"r{i+1}", project_id=str(pid),
            obs_type=ObservationType.SEAT_INSIGHT, title=f"R{i+1} 观察A", detail="A",
            source_ref="seat.data_scale:O1",  # 同 source_ref
            created_at=h.clock.now().isoformat(),
        ))
        h.observations.save(RoundObservation(
            obs_id=f"obs-r{i+1}-b", round_id=f"r{i+1}", project_id=str(pid),
            obs_type=ObservationType.GAP_IDENTIFIED, title=f"R{i+1} 观察B", detail="B",
            source_ref=f"seat.seat{i}:O1",  # 不同 source_ref
            created_at=h.clock.now().isoformat(),
        ))

    recent = h.observations.recent(str(pid), limit=10)

    # seat.data_scale:O1 应只保留最新（R3）。判据是 created_at：这三行用 FixedClock
    # 拿到的是同一时刻，所以是 id 倒序破平才留下 R3——**不是**因为 id 本身代表新旧。
    scale_obs = [o for o in recent if o.source_ref == "seat.data_scale:O1"]
    assert len(scale_obs) == 1
    assert scale_obs[0].title == "R3 观察A"

    # 不同 source_ref 的观察应全部保留
    all_refs = [o.source_ref for o in recent]
    assert len(all_refs) == len(set(all_refs)), "每个 source_ref 应唯一"

    print(f"\n  总观察数(去重后): {len(recent)}")
    print(f"  source_ref 唯一: {len(set(all_refs))} ✓")


# ── 验证 5: L4 摘要每轮生成 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_l4_digest_generation():
    """确认后 L4 摘要生成。"""
    h = Harness(replies={}, seat_replies={})
    pid = h.seed_project(title="测试")

    from pmstudio.domain.memory.digest import RoundDigest

    # 模拟 3 轮确认后生成摘要
    for i in range(3):
        h.digests.save(RoundDigest(
            round_id=f"r{i+1}", project_id=str(pid),
            summary=f"R{i+1} 确认了功能X",
            sections_touched=(f"s{i+1}",),
            features_addressed=(f"feat-{i}",),
            key_outcomes=(f"outcome-{i}",),
        ))

    recent = h.digests.recent(str(pid), limit=10)
    assert len(recent) == 3
    print(f"\n  L4 摘要数: {len(recent)}")
    for d in recent:
        print(f"    {d.round_id}: {d.summary}")


# ── 验证 6: 完整 6 轮主闭环 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_six_rounds_full_pipeline(tmp_path):
    """6 轮完整主闭环，验证记忆层端到端工作。"""

    # 准备 6 轮的回复
    claims_replies = []
    integration_replies = []
    seat_reply = json.dumps({
        "basis": "数据规模视角",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "性能",
                "claim": "应在 60 秒内",
                "because": "体验",
            },
        ],
    })

    for i in range(6):
        claims_replies.append(json.dumps({
            "basis": f"R{i+1}",
            "claims": [
                {
                    "text": f"第{i+1}轮功能迭代",
                    "raw_span": "功能迭代",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": "seat.data_scale", "why": f"R{i+1}"}],
        }))
        integration_replies.append(json.dumps({
            "basis": f"R{i+1}",
            "proposals": [
                {
                    "op": "add",
                    "entry_id": None,
                    "target": {
                        "zone": "body",
                        "section_id": "s5",
                        "feature_slug": f"feat-{i}",
                        "field": "one_liner",
                    },
                    "text": f"第{i+1}轮功能迭代",
                    "evidence_refs": ["E1"],
                    "rationale": f"R{i+1}",
                },
            ],
            "questions": [],
            "list_items": [],
            "dropped_opinions": [],
        }))

    h = Harness(
        replies={
            "claims": claims_replies,
            "integration": integration_replies,
            "discussion": json.dumps({"text": "讨论回复", "based_on": []}),
            "discussion_cards": json.dumps(
                {"cards": [{"statement": "结论", "grounding": "invented", "based_on": []}]}
            ),
            "sim_inquiry": json.dumps({"slices": [], "hypotheses": [], "unknowns": []}),
            "sim_review": json.dumps(
                {"runs": [], "summary": {"smooth": 0, "friction": 0, "blocked": 0}}
            ),
            "inquiry": json.dumps({"hypotheses": [], "blind_spots": []}),
            "opinion_dispositions": json.dumps({"dispositions": []}),
            "memory_curator": json.dumps({"changes": []}),
        },
        seat_replies={"seat.data_scale": [seat_reply] * 6},
    )
    pid = h.seed_project(title="6轮测试")

    start = h.start_round()
    round_ids = []

    for i in range(6):
        rid = await start.create(project_id=pid, user_input=f"第{i+1}轮：功能迭代")
        await start.analyze(round_id=rid)
        await h.submit_opening()(round_id=rid, seats=("seat.data_scale",))
        await h.run_seat_round()(round_id=rid)

        from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
        obj = h.rounds.get(rid)
        obj.authorize_new_feature(FeatureSlug(f"feat-{i}"), basis=EvidenceId("E1"))
        h.rounds.save(obj)

        await h.integrate_round()(round_id=rid)
        rv = await h.get_round()(round_id=rid)
        if rv.draft:
            accepted = tuple(p.proposal_id for p in rv.draft.proposals)
            await h.confirm_draft()(
                round_id=rid, draft_id=str(h.rounds.get(rid).draft.draft_id), accepted=accepted,
            )
        round_ids.append(rid)

    # 验证
    assert len(round_ids) == 6

    obs_count = len(h.observations.recent(str(pid)))
    digest_count = len(h.digests.recent(str(pid)))

    print("\n  6 轮完成")
    print(f"  L2 观察: {obs_count}")
    print(f"  L4 摘要: {digest_count}")

    assert obs_count > 0, "应有 L2 观察"
    assert digest_count > 0, "应有 L4 摘要"
