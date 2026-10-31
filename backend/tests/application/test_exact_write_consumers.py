import json
from dataclasses import replace

import pytest

from pmstudio.application.use_cases import dry_run
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.application.test_confirm_and_queries import confirmable_harness, round_awaiting_confirm
from tests.application.test_sim_review_use_cases import (
    PERSONAS,
    _all_good,
    _harness,
    _round_on_draft_card,
)
from tests.domain.test_exact_write_guards import apply, instruction, seeded
from tests.support.builders import NOW, PROJECT, SEAT, document_with_feature, make_claim, make_seat
from tests.support.harness import Harness

S1 = Target.body("s1")


def proposal(pid, *, entry="old", text="B", op=WriteOp.REPLACE, base=2, target=S1):
    return Proposal(ProposalId(pid), EntryId(entry), op, target, text,
                    Origin(OriginType.PROPOSED, (EvidenceId("E1"),)), base_version_no=base)


def on_card(proposals, document=None):
    harness = Harness(replies={})
    harness.seed_project()
    harness.documents.seed(document or seeded())
    round_ = Round.start(round_id=RoundId("r-exact"), project_id=PROJECT,
                         user_input="用户输入", now=NOW)
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(make_seat().seat_id,))
    round_.integrate(draft=Draft(DraftId("d-seen"), round_.round_id, tuple(proposals)))
    harness.rounds.seed(round_)
    return harness, round_


@pytest.mark.parametrize("accepted", [("p2", "p1"), ("p1", "p2", "p1"), ("p2", "p1", "p2")])
async def test_confirm_restores_draft_order_and_applies_once_from_same_batch_start(accepted):
    harness, round_ = on_card([proposal("p1", text="B"), proposal("p2", text="C")])
    await harness.confirm_draft()(round_id=round_.round_id, draft_id="d-seen", accepted=accepted)
    document = harness.documents.get(PROJECT)
    assert document.locate_entry(EntryId("old")).entry.text == "C"
    assert len(document.versions) == 3


@pytest.mark.parametrize("accepted,success", [(("p-new",), True), (("p-old",), False),
                                            (("p-new", "p-old"), False)])
async def test_cross_baseline_removes_are_each_checked_before_default_merge(accepted, success):
    document = seeded()
    apply(document, instruction("old", "B", op=WriteOp.REPLACE))
    harness, round_ = on_card([proposal("p-old", op=WriteOp.REMOVE),
                              proposal("p-new", op=WriteOp.REMOVE, base=3)], document)
    if success:
        await harness.confirm_draft()(
            round_id=round_.round_id, draft_id="d-seen", accepted=accepted,
        )
        assert not any(b.entries for b in harness.documents.get(PROJECT).blocks)
    else:
        before = harness.documents.get(PROJECT).snapshot()
        with pytest.raises(DomainError) as caught:
            await harness.confirm_draft()(
                round_id=round_.round_id, draft_id="d-seen", accepted=accepted,
            )
        assert caught.value.code == "proposal_stale"
        assert harness.documents.get(PROJECT).snapshot() == before
        assert harness.events.names == []


@pytest.mark.parametrize("base", [None, 99])
async def test_legacy_readable_card_is_not_resigned_on_confirm(base):
    harness, round_ = on_card([proposal("p1", base=base)])
    assert harness.rounds.get(round_.round_id).draft.proposals[0].base_version_no == base
    with pytest.raises(DomainError) as caught:
        await harness.confirm_draft()(round_id=round_.round_id, draft_id="d-seen", accepted=("p1",))
    assert caught.value.code == "baseline_missing"
    assert harness.documents.get(PROJECT).current_version.version_no == 2
    assert harness.events.names == []


async def test_new_draft_reusing_p01_rejects_seen_old_draft_before_resolving_ids():
    harness = confirmable_harness()
    rid = await round_awaiting_confirm(harness)
    old = harness.rounds.get(rid).draft.draft_id
    stored = harness.rounds.get(rid)
    stored._draft = replace(stored.draft, draft_id=DraftId("d-new"))
    harness.rounds.save(stored)
    before = harness.documents.get(PROJECT).snapshot()
    with pytest.raises(DomainError) as caught:
        await harness.confirm_draft()(round_id=rid, draft_id=str(old), accepted=("p01",))
    assert caught.value.code == "draft_mismatch"
    assert harness.documents.get(PROJECT).snapshot() == before


def test_failed_all_candidate_preview_keeps_current_gaps_and_does_not_mutate():
    proposals = [proposal("p1", op=WriteOp.REMOVE), proposal("p2", text="B")]
    harness, round_ = on_card(proposals)
    document = harness.documents.get(PROJECT)
    before = document.snapshot(), document.versions
    notes = dry_run(document, proposals, round_=round_, now=NOW)
    assert any("全部候选不能同时写入" in n and "未生成全选结果预览" in n for n in notes)
    assert any("当前已确认正文" in n for n in notes)
    assert (document.snapshot(), document.versions) == before


async def test_result_guard_failure_never_mutates_original_read_document():
    selected = [proposal("p1", text="B"),
                proposal("p2", entry="new", text="B", op=WriteOp.ADD)]
    harness, round_ = on_card(selected)
    original = harness.documents.get(PROJECT)
    before = original.snapshot(), original.versions
    harness.confirm_draft()._committer._documents.get_for_update = lambda _: original
    with pytest.raises(DomainError) as caught:
        await harness.confirm_draft()(
            round_id=round_.round_id, draft_id="d-seen", accepted=("p1", "p2"),
        )
    assert caught.value.code == "batch_conflict"
    assert (original.snapshot(), original.versions) == before
    assert harness.events.names == []


async def test_sim_new_proposal_uses_actual_input_k_and_keeps_original_n():
    harness = _harness({}, _all_good())
    rid = await _round_on_draft_card(harness)
    before = harness.rounds.get(rid).draft.proposals
    document = harness.documents.get(PROJECT)
    apply(document, instruction("unrelated", "新背景", target=Target.body("s2"), base=3))
    harness.documents.save(document)
    await harness.run_sim_review()(
        round_id=rid, persona_ids=PERSONAS, feature_slugs=("order-export",),
    )
    after = harness.rounds.get(rid).draft.proposals
    assert after[:len(before)] == before
    assert after[-1].base_version_no == 4
    assert all(p.base_version_no == 3 for p in before)


async def test_sim_target_change_during_call_rejects_before_e_or_run_persisted():
    harness = _harness({}, _all_good())
    rid = await _round_on_draft_card(harness)
    use_case = harness.run_sim_review()
    run = use_case._simulator.run

    async def changing_model(**kwargs):
        result = await run(**kwargs)
        document = harness.documents.get(PROJECT)
        if document.current_version.version_no == 3:
            apply(document, instruction("en-flow", "目标已变", op=WriteOp.REPLACE, base=3,
                  target=Target.body("s5", feature_slug="order-export", field="main_flow")))
            harness.documents.save(document)
        return result

    use_case._simulator.run = changing_model
    ledger_before = harness.evidences.get(PROJECT).evidences()
    round_before = harness.rounds.get(rid).draft
    event_count = len(harness.events.names)
    with pytest.raises(DomainError) as caught:
        await use_case(round_id=rid, persona_ids=PERSONAS, feature_slugs=("order-export",))
    assert caught.value.code == "proposal_stale"
    assert harness.evidences.get(PROJECT).evidences() == ledger_before
    assert harness.rounds.get(rid).draft == round_before
    assert harness.rounds.get(rid).sim_review_runs == 0
    assert len(harness.events.names) == event_count


async def mechanical_round(*, target, text, duplicate=False):
    claim_reply = {
        "basis": "读了实际文档", "claims": [{"text": text, "raw_span": text,
        "label": "need", "zone": "body", "section_id": target.section_id,
        "feature_slug": target.feature_slug, "field": target.field}],
        "seats": [{"seat_id": SEAT, "why": "核验文档"}]}
    packet = {"questions": [], "list_items": [], "proposals": [{"op": "add", "entry_id": None,
              "target": {"zone": "body", "section_id": "s2"}, "text": "模型候选",
              "origin_type": "proposed", "evidence_refs": ["E2"]}]}
    harness = Harness(replies={"claims": json.dumps(claim_reply, ensure_ascii=False),
                                "integration": json.dumps(packet, ensure_ascii=False)},
                      seat_replies={SEAT: json.dumps({"basis": "已核验", "opinions": []})})
    harness.seed_project()
    document = document_with_feature()
    if duplicate:
        apply(document, instruction("previous", text, target=target))
    harness.documents.seed(document)
    rid = await harness.start_round().create(project_id=PROJECT, user_input=text)
    await harness.start_round().analyze(round_id=rid)
    await harness.submit_opening()(round_id=rid, seats=(SEAT,))
    await harness.run_seat_round()(round_id=rid)
    # E2 是真实账本条目；模型刻意只引用它，E1 输入必须走机械路。
    ledger = harness.evidences.get(PROJECT)
    from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
    ledger.record(kind=EvidenceKind.AGENT_PROPOSED, text="模型依据",
                  source=EvidenceSource(seat_id="seat.data_scale", rationale="建议"), now=NOW)
    harness.evidences.save(ledger)
    return harness, rid


@pytest.mark.parametrize("duplicate", [False, True])
async def test_mechanical_single_normalizes_without_changing_origin(duplicate):
    target = Target.body("s5", feature_slug="order-export", field="one_liner")
    harness, rid = await mechanical_round(target=target, text="新的整句")
    if duplicate:
        document = harness.documents.get(PROJECT)
        apply(document, instruction("en-bootstrap", "新的整句", target=target, op=WriteOp.REPLACE))
        harness.documents.save(document)
    await harness.integrate_round()(round_id=rid)
    proposals = harness.rounds.get(rid).draft.proposals
    mechanical = next(p for p in proposals if p.origin.type is OriginType.USER)
    assert mechanical.op is WriteOp.REPLACE
    assert mechanical.entry_id == "en-bootstrap"
    assert mechanical.base_version_no == (3 if duplicate else 2)
    assert bool(mechanical.overlap) is duplicate


@pytest.mark.parametrize("text", ["整段重复", "第一行\n第二行"])
async def test_mechanical_multi_restatement_remains_visible_and_default_reviewed(text):
    target = Target.body("s5", feature_slug="order-export", field="exceptions")
    harness, rid = await mechanical_round(target=target, text=text, duplicate=True)
    await harness.integrate_round()(round_id=rid)
    draft = harness.rounds.get(rid).draft
    mechanical = next(p for p in draft.proposals if p.origin.type is OriginType.USER)
    assert mechanical.text == text
    assert mechanical.op is WriteOp.ADD
    assert mechanical.base_version_no == 3
    assert mechanical.overlap is not None
    assert any("全部候选不能同时写入" in n for n in draft.gaps)
    # 合法模型单选仍可确认；不能因全选重复而吞整包或机械输入。
    await harness.confirm_draft()(round_id=rid, draft_id=str(draft.draft_id), accepted=("p01",))


async def test_final_mechanical_preview_replaces_model_only_gap_result():
    target = Target.body("s5", feature_slug="order-export", field="name")
    harness, rid = await mechanical_round(target=target, text="订单导出")
    await harness.integrate_round()(round_id=rid)
    draft = harness.rounds.get(rid).draft
    assert any("s5 已填 2/6" in n for n in draft.gaps)
    assert not any("s5 已填 1/6" in n for n in draft.gaps)


async def test_all_candidate_remove_replace_failure_still_produces_both_cards():
    harness, rid = await mechanical_round(target=Target.body("s1"), text="本轮新输入")
    document = harness.documents.get(PROJECT)
    apply(document, instruction("old", "A", base=2))
    harness.documents.save(document)
    use_case = harness.integrate_round()
    # 使用真实 Integrator 的 schema/装配，仅替换外部模型回复。
    packet = {"questions": [], "list_items": [], "proposals": [
        {"op": "remove", "entry_id": "old", "target": {"zone": "body", "section_id": "s1"},
         "text": "", "origin_type": "proposed", "evidence_refs": ["E1"]},
        {"op": "replace", "entry_id": "old", "target": {"zone": "body", "section_id": "s1"},
         "text": "B", "origin_type": "proposed", "evidence_refs": ["E1"]}]}
    from tests.support.doubles import ScriptedModelGateway
    harness.gateway = ScriptedModelGateway(replies={"integration": json.dumps(packet)})
    use_case._integrator._model = harness.gateway
    await use_case(round_id=rid)
    draft = harness.rounds.get(rid).draft
    assert [p.op for p in draft.proposals] == [WriteOp.REMOVE, WriteOp.REPLACE]
    assert any("全部候选不能同时写入" in n for n in draft.gaps)
    assert len([r for r in harness.gateway.requests if r.purpose == "integration"]) == 1
    await harness.confirm_draft()(round_id=rid, draft_id=str(draft.draft_id), accepted=("p02",))
    assert harness.documents.get(PROJECT).locate_entry(EntryId("old")).entry.text == "B"
