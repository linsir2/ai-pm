"""端到端全覆盖真跑：模拟产品经理做"社区团购平台"的完整旅程。

覆盖：主闭环(3轮) → 讨论区 → 探索 → 版本管理 → 搜索 → 工具层。
用 Harness + ScriptedModelGateway（不联网、不需 API key），走真实用例层 + 编排层 + 领域层。
"""

import asyncio
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.support.harness import Harness, PROJECT, make_seat
from pmstudio.domain.shared.ids import ExplorationId, FeatureSlug, EvidenceId


def claims_v1():
    return json.dumps({"basis": "读了全文", "claims": [
        {"text": "社区团购平台服务于生鲜采购", "raw_span": "社区团购平台", "label": "fact", "zone": "body", "section_id": "s1"},
        {"text": "核心用户是家庭主妇和上班族", "raw_span": "家庭主妇和上班族", "label": "guess", "zone": "body", "section_id": "s2",
         "assumption": {"failure_consequence": "获客成本上升", "verification": "用户访谈"}},
        {"text": "团长负责最后一公里配送", "raw_span": "最后一公里配送", "label": "fact", "zone": "body", "section_id": "s5"},
    ], "seats": [{"seat_id": "seat.business_rules", "why": "准入规则"}, {"seat_id": "seat.data_scale", "why": "SKU预估"}]}, ensure_ascii=False)


def claims_v2():
    return json.dumps({"basis": "读了全文", "claims": [
        {"text": "支持预售模式", "raw_span": "预售模式", "label": "fact", "zone": "body", "section_id": "s5"},
        {"text": "预售可降低库存损耗", "raw_span": "提前一天下单", "label": "guess", "zone": "body", "section_id": "s8",
         "assumption": {"failure_consequence": "损耗高于预期", "verification": "预售数据"}},
    ], "seats": [{"seat_id": "seat.business_rules", "why": "预售规则"}, {"seat_id": "seat.data_scale", "why": "库存模型"}]}, ensure_ascii=False)


def claims_v3():
    return json.dumps({"basis": "基于讨论", "claims": [
        {"text": "预售体验需30秒下单", "raw_span": "预售体验", "label": "constraint", "zone": "body", "section_id": "s8"},
    ], "seats": [{"seat_id": "seat.frontend_interaction", "why": "交互"}]}, ensure_ascii=False)


def seat_reply(seat_id):
    ops = {"seat.business_rules": [{"local_id": "O1", "form": "judgment", "about": "准入", "claim": "团长需缴纳保证金", "because": "降低流失", "evidence_refs": []}, {"local_id": "O2", "form": "question", "about": "退出", "claim": "退还周期？", "because": "影响意愿", "evidence_refs": []}],
           "seat.data_scale": [{"local_id": "O1", "form": "judgment", "about": "SKU", "claim": "200-500个", "because": "降低损耗", "evidence_refs": []}, {"local_id": "O2", "form": "question", "about": "周转", "claim": "周转目标？", "because": "影响损耗", "evidence_refs": []}],
           "seat.frontend_interaction": [{"local_id": "O1", "form": "judgment", "about": "流程", "claim": "下单不超3步", "because": "减少流失", "evidence_refs": []}, {"local_id": "O2", "form": "question", "about": "支付", "claim": "支付方式？", "because": "影响转化", "evidence_refs": []}]}
    return json.dumps({"basis": seat_id, "opinions": ops.get(seat_id, ops["seat.business_rules"])}, ensure_ascii=False)


def integration_reply():
    return json.dumps({"basis": "综合意见", "proposals": [
        {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "group-buying", "field": "one_liner"}, "text": "社区团购平台", "evidence_refs": ["E1"], "rationale": "定位"},
        {"op": "add", "target": {"zone": "body", "section_id": "s5", "feature_slug": "group-buying", "field": "goal"}, "text": "30分钟达", "evidence_refs": ["E1"], "rationale": "需求"},
    ], "questions": [{"kind": "decision", "title": "退还周期", "why": "疑问", "target": {"zone": "body", "section_id": "s5", "feature_slug": "group-buying", "field": "rules"}, "options": [{"id": "7d", "label": "7天"}, {"id": "30d", "label": "30天"}], "consequence_if_skipped": "按30天", "evidence_refs": [], "from_opinions": ["seat.business_rules:O2"], "blocking": False}],
    "list_items": [], "dropped_opinions": []}, ensure_ascii=False)


def disc_say():
    return json.dumps({"text": "反方：团长模式有流失风险，建议增加直营渠道。", "based_on": []}, ensure_ascii=False)


def disc_cards():
    return json.dumps({"cards": [{"statement": "团长模式有流失风险，建议增加直营渠道", "grounding": "invented", "based_on": []}, {"statement": "品控需标准化", "grounding": "invented", "based_on": []}]}, ensure_ascii=False)


def build_harness():
    return Harness(
        replies={
            "claims": [claims_v1(), claims_v2(), claims_v3()],
            "integration": integration_reply(),
            "discussion": disc_say(),
            "discussion_cards": disc_cards(),
            "inquiry": json.dumps({"basis": "用户视角", "hypotheses": [{"statement": "用户最在意配送时效", "falsification": "价格更多则不成立", "anchors": []}], "blind_spots": [{"statement": "未考虑老年用户", "anchors": []}]}),
        },
        seat_replies={
            "seat.business_rules": seat_reply("seat.business_rules"),
            "seat.data_scale": seat_reply("seat.data_scale"),
            "seat.frontend_interaction": seat_reply("seat.frontend_interaction"),
        },
        seats=[
            make_seat(seat_id="seat.business_rules", title="业务规则"),
            make_seat(seat_id="seat.data_scale", title="数据规模"),
            make_seat(seat_id="seat.frontend_interaction", title="前端交互"),
        ],
    )


async def step(name, coro):
    print(f"\n{'='*60}\n▶ {name}\n{'='*60}")
    try:
        result = await coro
        print("  ✅")
        return result
    except Exception as e:
        print(f"  ❌ {e}")
        traceback.print_exc()
        raise


async def main():
    print("=" * 60)
    print("端到端全覆盖真跑：社区团购平台")
    print("=" * 60)

    H = build_harness()
    pid = H.seed_project(title="社区团购平台")

    # ═══ 1. 主闭环 R1 ═══
    start1 = H.start_round()
    r1 = await step("1a. R1: create", start1.create(project_id=pid, user_input="社区团购平台，团长负责社群运营和最后一公里配送，核心用户是家庭主妇和上班族"))
    await step("1b. R1: analyze", start1.analyze(round_id=r1))
    await step("1c. R1: submit_opening", H.submit_opening()(round_id=r1, seats=("seat.business_rules", "seat.data_scale")))
    await step("1d. R1: run_seat_round", H.run_seat_round()(round_id=r1))
    r1_obj = H.rounds.get(r1)
    r1_obj.authorize_new_feature(FeatureSlug("group-buying"), basis=EvidenceId("E1"))
    H.rounds.save(r1_obj)
    await step("1e. R1: integrate", H.integrate_round()(round_id=r1))
    v1 = await H.get_round()(round_id=r1)
    await step("1f. R1: confirm", H.confirm_draft()(round_id=r1, accepted=tuple(p.proposal_id for p in v1.draft.proposals)))
    print(f"  → v{v1.confirmed_version_no}")

    # ═══ 2. 主闭环 R2 ═══
    start2 = H.start_round()
    r2 = await step("2a. R2: create", start2.create(project_id=pid, user_input="预售模式，提前一天下单次日送达"))
    await step("2b. R2: analyze", start2.analyze(round_id=r2))
    await step("2c. R2: submit_opening", H.submit_opening()(round_id=r2, seats=("seat.business_rules", "seat.data_scale")))
    await step("2d. R2: run_seat_round", H.run_seat_round()(round_id=r2))
    await step("2e. R2: integrate", H.integrate_round()(round_id=r2))
    v2 = await H.get_round()(round_id=r2)
    await step("2f. R2: confirm", H.confirm_draft()(round_id=r2, accepted=tuple(p.proposal_id for p in v2.draft.proposals)))
    print(f"  → v{v2.confirmed_version_no}")

    # ═══ 3. 讨论区 ═══
    from pmstudio.domain.discussion.thread import QuestionSnapshot
    await step("3a. 讨论: start", H.start_discussion()(project_id=pid, topic="用户增长策略", role_id="dlg.challenger", question=QuestionSnapshot(title="如何不依赖团长实现增长？")))
    did = (await H.list_discussions()(project_id=pid))[-1].thread_id
    await step("3b. 讨论: say", H.say_to_thread()(discussion_id=did, text="团长模式有流失风险，建议增加直营渠道"))
    await step("3c. 讨论: assemble", H.assemble_cards()(discussion_id=did))
    disc = H.discussions.get(did)
    print(f"  卡: {len(disc.cards)}")
    if disc.cards:
        await step("3d. 讨论: keep", H.keep_discussion_card()(discussion_id=did, card_id=disc.cards[0].card_id, as_="material"))

    # ═══ 4. 探索 ═══
    from pmstudio.domain.exploration.exploration import Exploration, ExplorationScope, ScopeKind
    from pmstudio.domain.exploration.item import Hypothesis, BlindSpot, Grounding
    eid = ExplorationId("e2e-exp-001")
    exp = Exploration.start(exploration_id=eid, project_id=pid, scope=ExplorationScope(text="竞品分析", kind=ScopeKind.DOCUMENT), now=H.clock.now())
    exp.complete(
        hypotheses=(Hypothesis(local_id="H1", statement="用户最在意配送时效", grounding=Grounding.INVENTED, based_on=(), falsification="价格更多则不成立", evidence_needed="访谈"),),
        blind_spots=(BlindSpot(local_id="B1", statement="未考虑老年用户", next_step="可用性测试"),),
    )
    H.explorations.save(exp)
    await step("4b. 探索: keep", H.keep_candidate()(exploration_id=eid, candidate_id="H1", as_="material"))

    # ═══ 5. 主闭环 R3 ═══
    start3 = H.start_round()
    r3 = await step("5a. R3: create", start3.create(project_id=pid, user_input="基于讨论结论，补充预售体验设计"))
    await step("5b. R3: analyze", start3.analyze(round_id=r3))
    await step("5c. R3: submit_opening", H.submit_opening()(round_id=r3, seats=("seat.frontend_interaction",)))
    await step("5d. R3: run_seat_round", H.run_seat_round()(round_id=r3))
    await step("5e. R3: integrate", H.integrate_round()(round_id=r3))
    v3 = await H.get_round()(round_id=r3)
    await step("5f. R3: confirm", H.confirm_draft()(round_id=r3, accepted=tuple(p.proposal_id for p in v3.draft.proposals)))
    print(f"  → v{v3.confirmed_version_no}")

    # ═══ 6. 版本管理 ═══
    versions = await H.get_versions()(project_id=pid)
    print(f"  版本: {len(versions.versions)}")
    diff = await H.get_version_diff()(project_id=pid, from_version_no=1, to_version_no=3)
    total = sum(len(g.added) + len(g.removed) + len(g.changed) for g in diff.groups)
    print(f"  v1→v3: {len(diff.groups)} 组, {total} 条目变更")
    await step("6c. 回退→v2", H.rollback_version()(project_id=pid, version_no=2))
    print(f"  当前 v{(await H.get_document()(project_id=pid)).version_no}")

    # ═══ 7. 搜索 ═══
    results = await H.search()(project_id=pid, query="团购")
    print(f"  '团购' 命中 {len(results.hits)}")

    # ═══ 8. 工具层 ═══
    await step("8a. 上传", H.upload_material()(project_id=pid, filename="竞品.md", media_type="text/markdown", data=b"# \u7ade\u54c1\n- \u7f8e\u56e2\n"))
    print(f"  材料 {len(await H.list_materials()(project_id=pid))}")

    # ═══ 总结 ═══
    print("\n" + "=" * 60)
    print("✅ 全覆盖真跑完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
