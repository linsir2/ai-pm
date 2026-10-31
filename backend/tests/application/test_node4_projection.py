from dataclasses import replace

import pytest

from pmstudio.domain.round.draft import OriginType
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.application.test_attach_research import _harness, _research_done
from tests.application.test_exact_write_consumers import on_card, proposal
from tests.domain.test_exact_write_guards import apply, instruction, seeded
from tests.support.builders import PROJECT, document_with_feature


@pytest.mark.parametrize("op", [WriteOp.REPLACE, WriteOp.REMOVE])
async def test_real_get_round_reads_before_from_signed_history_not_current(op):
    document = seeded()
    apply(document, instruction("old", "later", op=WriteOp.REPLACE, refs=(EvidenceId("E2"),)))
    harness, round_ = on_card([proposal("p1", op=op)], document)
    before = document.snapshot(), document.versions
    view = await harness.get_round()(round_id=round_.round_id)
    card = view.draft.proposals[0]
    assert card.before.text == "A"
    assert card.before.refs == ("E1",)
    assert card.base_version_no == 2
    assert card.requires_review is True
    assert "proposal_stale" in card.warning
    assert (document.snapshot(), document.versions) == before


@pytest.mark.parametrize("base", [None, 99])
async def test_unknown_baseline_is_readable_but_never_claims_current_as_before(base):
    harness, round_ = on_card([proposal("p1", base=base)])
    card = (await harness.get_round()(round_id=round_.round_id)).draft.proposals[0]
    assert card.before is None
    assert card.base_version_no == base
    assert card.requires_review
    assert "baseline_missing" in card.warning


async def test_done_card_keeps_before_without_applying_current_stale_check():
    harness, round_ = on_card([proposal("p1")])
    await harness.confirm_draft()(round_id=round_.round_id, draft_id="d-seen", accepted=("p1",))
    document = harness.documents.get(PROJECT)
    apply(document, instruction("old", "even later", op=WriteOp.REPLACE, base=3))
    harness.documents.save(document)
    view = await harness.get_round()(round_id=round_.round_id)
    card = view.draft.proposals[0]
    assert view.step == "done" and view.waiting_on == "none"
    assert card.before.text == "A"
    assert not card.requires_review
    assert "proposal_stale" not in (card.warning or "")


async def test_workspace_uses_full_document_and_reads_round_before_document():
    harness, round_ = on_card([proposal("p1")])
    harness.index.set_latest_round("p1", round_.round_id)
    reads = []
    get_round, get_doc = harness.rounds.get, harness.documents.get

    def read_round(rid):
        reads.append("round")
        return get_round(rid)

    def read_doc(pid):
        reads.append("document")
        return get_doc(pid)

    harness.rounds.get, harness.documents.get = read_round, read_doc
    view = await harness.get_workspace()(project_id=PROJECT)
    assert view.round.draft.proposals[0].before.text == "A"
    assert reads.index("round") < reads.index("document")


async def test_abandon_response_has_historical_before():
    harness, round_ = on_card([proposal("p1")])
    view = await harness.abandon_round()(round_id=round_.round_id)
    assert view.draft.proposals[0].before.text == "A"
    assert not view.draft.proposals[0].requires_review


async def test_attach_response_retains_history_before_on_a_running_legacy_round():
    # 可读取的历史draft残留与当前挂载状态同时存在；不把它伪装成pending。
    harness = _harness()
    research = await _research_done(harness)
    _, round_ = on_card([proposal("p1")])
    from pmstudio.domain.round.round import Round
    from pmstudio.domain.round.step import Step
    from tests.support.builders import NOW

    legacy = Round(round_id=round_.round_id, project_id=PROJECT,
                   user_input=round_.user_input, created_at=NOW,
                   step=Step.SEATS_RUNNING, draft=round_.draft)
    harness.documents.seed(seeded())
    harness.rounds.seed(legacy)
    view = await harness.attach_research_sources()(
        round_id=legacy.round_id, exploration_id=research.exploration_id,
        eids=(research.research.sources[0].eid,),
    )
    assert view.draft.proposals[0].before.text == "A"
    assert not view.draft.proposals[0].requires_review


async def test_competing_replaces_keep_origin_and_actual_draft_order_in_all_groups():
    first = replace(proposal("p-z"), origin=replace(proposal("p-z").origin, type=OriginType.USER))
    harness, round_ = on_card([first, proposal("p-a", text="C")])
    draft = (await harness.get_round()(round_id=round_.round_id)).draft
    assert [p.proposal_id for p in draft.proposals] == ["p-z", "p-a"]
    assert [p.origin_type for p in draft.proposals] == ["user", "proposed"]
    for p in draft.proposals:
        assert p.requires_review
        assert "p-z → p-a" in p.warning
        assert "草案顺序" in p.warning
    assert draft.groups[0][1][0] == draft.proposals[0]


async def test_scope_overlap_and_mechanical_warning_all_survive():
    item = replace(proposal("p1", base=None), overlap="1/1")
    harness, round_ = on_card([item])
    card = (await harness.get_round()(round_id=round_.round_id)).draft.proposals[0]
    assert "本轮" in card.warning
    assert "1/1" in card.warning
    assert "baseline_missing" in card.warning


async def test_single_empty_add_competition_uses_shared_batch_guard():
    document = document_with_feature()
    target = Target.body("s5", feature_slug="order-export", field="name")
    items = [proposal("p2", entry="a", op=WriteOp.ADD, target=target),
             proposal("p1", entry="b", op=WriteOp.ADD, target=target)]
    harness, round_ = on_card(items, document)
    draft = (await harness.get_round()(round_id=round_.round_id)).draft
    assert all(p.before is None and p.requires_review for p in draft.proposals)
    assert all("single_add_competition" in p.warning for p in draft.proposals)
    assert all("p2 → p1" in p.warning for p in draft.proposals)


async def test_mixed_baseline_single_add_keeps_own_diagnosis_and_flags_valid_competition():
    document = document_with_feature()
    target = Target.body("s5", feature_slug="order-export", field="name")
    items = [proposal("p-z", entry="a", op=WriteOp.ADD, target=target, base=None),
             proposal("p-a", entry="b", op=WriteOp.ADD, target=target, base=2),
             proposal("p-b", entry="c", op=WriteOp.ADD, target=target, base=2)]
    harness, round_ = on_card(items, document)
    pz, pa, pb = (await harness.get_round()(round_id=round_.round_id)).draft.proposals

    assert pz.requires_review
    assert "baseline_missing" in pz.warning
    assert "single_add_competition" not in pz.warning
    for card in (pa, pb):
        assert card.requires_review
        assert "single_add_competition" in card.warning
        assert "p-a → p-b" in card.warning


async def test_added_duplicate_candidate_requires_review_without_reclassifying_origin():
    item = replace(proposal("p1", entry="new", text="A", op=WriteOp.ADD),
                   origin=replace(proposal("p1").origin, type=OriginType.USER))
    harness, round_ = on_card([item])
    card = (await harness.get_round()(round_id=round_.round_id)).draft.proposals[0]
    assert card.before is None
    assert card.origin_type == "user"
    assert card.requires_review
    assert "added_duplicate" in card.warning
