"""L4 + L3 端到端集成测试：确认后生成 L4，收下后存 L3，整合者注入 L4+L3。"""

import json

import pytest

from tests.support.harness import Harness

PROJECT = "p-integration"
SEAT = "seat.data_scale"
SEAT_REPLY = json.dumps({
    "basis": "数据规模视角",
    "opinions": [
        {
            "local_id": "O1",
            "form": "judgment",
            "about": "SKU",
            "claim": "200-500个SKU",
            "because": "过多增加损耗",
        },
        {
            "local_id": "O2",
            "form": "question",
            "about": "周转",
            "claim": "库存周转目标？",
            "because": "影响损耗率",
        },
    ],
})
CLAIMS_REPLY = json.dumps({
    "basis": "读了全文",
    "claims": [
        {
            "text": "社区团购平台",
            "raw_span": "社区团购",
            "label": "fact",
            "zone": "body",
            "section_id": "s1",
        },
        {
            "text": "团长负责配送",
            "raw_span": "社区团购",
            "label": "fact",
            "zone": "body",
            "section_id": "s5",
        },
    ],
    "seats": [{"seat_id": SEAT, "why": "量级评估"}],
})
INTEGRATION_REPLY = json.dumps({
    "basis": "综合意见",
    "proposals": [
        {
            "op": "add",
            "target": {
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "group-buying",
                "field": "one_liner",
            },
            "text": "社区团购平台",
            "evidence_refs": ["E1"],
            "rationale": "定位",
        },
    ],
    "questions": [],
    "list_items": [],
    "dropped_opinions": [],
})


@pytest.fixture
def harness():
    return Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": INTEGRATION_REPLY,
            "discussion": json.dumps(
                {"text": "反方观点：团长模式有流失风险，建议增加直营渠道。", "based_on": []}
            ),
            "discussion_cards": json.dumps({
                "cards": [
                    {
                        "statement": "团长月流失率15%，建议增加直营渠道作为备选",
                        "grounding": "invented",
                        "based_on": [],
                    },
                    {
                        "statement": "品控需标准化，否则复购率会下降",
                        "grounding": "invented",
                        "based_on": [],
                    },
                ]
            }),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )


async def _run_full_round(harness, project_id, user_input):
    start = harness.start_round()
    round_id = await start.create(project_id=project_id, user_input=user_input)
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    # 授权功能
    round_obj = harness.rounds.get(round_id)
    from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
    if "group-buying" not in [str(f) for f in round_obj.authorized_new_features]:
        round_obj.authorize_new_feature(FeatureSlug("group-buying"), basis=EvidenceId("E1"))
        harness.rounds.save(round_obj)
    await harness.integrate_round()(round_id=round_id)
    round_view = await harness.get_round()(round_id=round_id)
    if round_view.draft:
        await harness.confirm_draft()(
            round_id=round_id,
            accepted=tuple(p.proposal_id for p in round_view.draft.proposals),
        )
    return round_id


@pytest.mark.asyncio
async def test_l4_generated_after_confirm(harness):
    """确认后生成 L4 轮次摘要。"""
    pid = harness.seed_project(title="社区团购平台")
    await _run_full_round(harness, pid, "社区团购平台，团长配送")
    # 检查 digest 存储
    recent = harness.digests.recent(str(pid))
    assert len(recent) > 0
    # 摘要包含触及的章节
    assert "s5" in recent[0].summary or "s5" in str(recent[0].sections_touched)
    # 关键成果包含确认的内容
    assert any("社区团购" in o for o in recent[0].key_outcomes)


@pytest.mark.asyncio
async def test_l4_injected_into_integrator(harness):
    """L4 注入到整合者提示词。"""
    pid = harness.seed_project(title="社区团购平台")
    # R1: 确认写入
    await _run_full_round(harness, pid, "社区团购平台，团长配送")
    # R2: 整合者应该看到 L4
    start2 = harness.start_round()
    r2 = await start2.create(project_id=pid, user_input="社区团购预售模式")
    await start2.analyze(round_id=r2)
    await harness.submit_opening()(round_id=r2, seats=(SEAT,))
    await harness.run_seat_round()(round_id=r2)
    await harness.integrate_round()(round_id=r2)
    # 检查 integrator 提示词包含 digests
    integrator_slots = harness.prompts.last("integrator")
    assert "digests" in integrator_slots


@pytest.mark.asyncio
async def test_l3_stored_after_keep_card(harness):
    """收下讨论卡后存 L3。"""
    pid = harness.seed_project(title="社区团购平台")
    # 先创建讨论
    from pmstudio.domain.discussion.thread import QuestionSnapshot
    await harness.start_discussion()(
        project_id=pid, topic="用户增长策略", role_id="dlg.challenger",
        question=QuestionSnapshot(title="如何不依赖团长实现增长？")
    )
    did = (await harness.list_discussions()(project_id=pid))[-1].thread_id
    await harness.say_to_thread()(discussion_id=did, text="团长模式有流失风险")
    await harness.assemble_cards()(discussion_id=did)
    disc = harness.discussions.get(did)
    if disc.cards:
        await harness.keep_discussion_card()(
            discussion_id=did, card_id=disc.cards[0].card_id, as_="material"
        )
    # 检查 inspiration 存储
    recent = harness.inspirations.recent(str(pid))
    assert len(recent) > 0


@pytest.mark.asyncio
async def test_three_round_flow(harness):
    """完整三轮流程：R1 确认 → R2 看到 L4+L3。"""
    pid = harness.seed_project(title="社区团购平台")
    # R1: 确认写入
    await _run_full_round(harness, pid, "社区团购平台，团长配送")
    # R2: 应该看到 L4
    start2 = harness.start_round()
    r2 = await start2.create(project_id=pid, user_input="社区团购预售模式")
    await start2.analyze(round_id=r2)
    await harness.submit_opening()(round_id=r2, seats=(SEAT,))
    await harness.run_seat_round()(round_id=r2)
    await harness.integrate_round()(round_id=r2)
    # 检查 integrator 提示词包含 digests 和 inspirations_index
    integrator_slots = harness.prompts.last("integrator")
    assert "digests" in integrator_slots
    assert "inspirations_index" in integrator_slots
