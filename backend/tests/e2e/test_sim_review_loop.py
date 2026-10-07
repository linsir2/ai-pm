"""模拟评测接进主闭环（M11-T10）：草案卡 → 评测 → 减提案 → 确认 → 文档少一条、版本 +1。

只脚本化模型；其余全真：Harness 的真领域 + 真用例 + 真图（`sim_review` 命令走完整图路由，
用例直调测不到这一层）。验收链：评测提案默认不勾、勾了确认才写、写的是"减"。
"""

from pmstudio.application.commands import RoundCommand
from pmstudio.application.deliverable import iter_document_entries
from tests.application.test_sim_review_use_cases import (  # noqa: F401
    PERSONAS,
    _all_good,
    _harness,
    _round_on_draft_card,
)
from tests.support.builders import PROJECT


async def test_sim_review_proposal_writes_a_document_removal() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    before = harness.documents.get(PROJECT)
    before_version = before.current_version.version_no
    assert any(
        str(item.entry_id) == "en-flow" for item in iter_document_entries(before.view())
    )

    view = await harness.runner().run(
        RoundCommand(
            kind="sim_review",
            round_id=str(round_id),
            persona_ids=PERSONAS,
            feature_slugs=("order-export",),
        )
    )

    assert view.sim_review is not None
    assert view.sim_review.runs == 1
    sim_proposals = [item for item in view.draft.proposals if item.simulation_based]
    assert [item.proposal_id for item in sim_proposals] == ["sim#1"]
    assert sim_proposals[0].origin_type == "proposed", "评测提案落「它提议的」"
    assert sim_proposals[0].op == "remove"

    result = await harness.confirm_draft()(round_id=round_id, accepted=("sim#1",))

    assert result.version.version_no == before_version + 1
    after = harness.documents.get(PROJECT).view()
    remaining = {str(item.entry_id) for item in iter_document_entries(after)}
    assert "en-flow" not in remaining, "被两人标 dead_end 的那条随确认真的被减掉了"


async def test_r2_replace_proposal_writes_the_priority_entry() -> None:
    """R2 写路径：≥2 人 blocked + 功能有优先级条目 → replace 提案 → 确认真的替换（M11 补钉）。

    与 remove 那条链分开：remove 撤条目、replace 改正文——确认端是两条不同的写路径。
    """

    import json

    from tests.application.test_sim_review_use_cases import _packet

    def blocked() -> str:
        payload = json.loads(_packet())
        payload["features"][0]["verdict"] = "blocked"
        return json.dumps(payload, ensure_ascii=False)

    harness = _harness({}, {persona: blocked() for persona in PERSONAS})
    round_id = await _round_on_draft_card(harness)
    before_version = harness.documents.get(PROJECT).current_version.version_no

    view = await harness.runner().run(
        RoundCommand(
            kind="sim_review",
            round_id=str(round_id),
            persona_ids=PERSONAS,
            feature_slugs=("order-export",),
        )
    )

    replace = [item for item in view.draft.proposals if item.op == "replace"]
    assert len(replace) == 1
    proposal_id = replace[0].proposal_id
    assert replace[0].text == "待复核（模拟评测：3/3 个模拟使用者未能走通）"

    result = await harness.confirm_draft()(round_id=round_id, accepted=(proposal_id,))

    assert result.version.version_no == before_version + 1
    after = harness.documents.get(PROJECT).view()
    entries = {str(item.entry_id): item.text for item in iter_document_entries(after)}
    assert entries["en-prio"] == "待复核（模拟评测：3/3 个模拟使用者未能走通）"
    assert "en-flow" in entries, "没勾 remove，那条动的入口不能被误删"
