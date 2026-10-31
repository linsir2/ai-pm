from dataclasses import replace

import pytest
from evals.assertions import check

from pmstudio.application.assembly import normalize_mechanical_proposals
from pmstudio.application.dto import RoundView, round_view
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim, make_document

ASSERTION = {"type": "outside_scope_items_are_labeled"}


def mechanical_view(kind="baseline_missing", *, outside=False, extra_inside=False):
    """真实领域候选→生产DTO；不直接编造warning或给legacy重签。"""
    target = Target.body("s1")
    document = make_document()
    document.apply((WriteInstruction("seed", EntryId("old"), WriteOp.ADD, target,
                                    "旧文", (EvidenceId("E1"),)),),
                   now=NOW, summary="测试基线")
    proposal = Proposal(ProposalId("p01"), EntryId("old"), WriteOp.REPLACE, target,
                        "修订正文", Origin(OriginType.USER, (EvidenceId("E1"),)),
                        base_version_no=None if kind == "baseline_missing" else 2)
    proposals = [proposal]
    if kind == "proposal_stale":
        document.apply((WriteInstruction("change", EntryId("old"), WriteOp.REPLACE,
                                        target, "当前已经改过", (EvidenceId("E1"),)),),
                       now=NOW, summary="模拟生成后改文")
    elif kind == "overlap":
        fresh = replace(proposal, entry_id=EntryId("new"), op=WriteOp.ADD,
                        text="旧文", base_version_no=None)
        proposals = list(normalize_mechanical_proposals((fresh,), document).proposals)
    elif kind == "competition":
        proposals.append(replace(proposal, proposal_id=ProposalId("p02"), text="另一个替换"))
    if extra_inside:
        proposals.append(Proposal(
            ProposalId("p-inside"), EntryId("new-inside"), WriteOp.ADD, Target.body("s5"),
            "本轮范围内候选", Origin(OriginType.USER, (EvidenceId("E1"),)),
        ))
    round_ = Round.start(round_id=ROUND_ID, project_id=PROJECT,
                         user_input="用户修订", now=NOW)
    round_.attach_claims((make_claim(target=Target.body("s5") if outside else target,
                                    eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.integrate(draft=Draft(DraftId("d-eval"), ROUND_ID, tuple(proposals)))
    return round_view(round_, document=document)


def without_card_scope_warning(view: RoundView) -> RoundView:
    """模拟DTO传播只漏掉scope那段；proposals/groups同步，真实机械诊断照留。"""
    draft = view.draft
    assert draft is not None
    first = draft.proposals[0]
    assert first.warning is not None and "不在本轮去处里" in first.warning
    mechanical = first.warning.partition("；")[2]
    assert mechanical and "不在本轮去处里" not in mechanical
    changed = replace(first, warning=mechanical)
    return replace(view, draft=replace(
        draft, proposals=(changed, *draft.proposals[1:]),
        groups=tuple((title, tuple(changed if p.proposal_id == first.proposal_id else p
                                  for p in items)) for title, items in draft.groups),
    ))


@pytest.mark.parametrize("kind", ["baseline_missing", "proposal_stale", "overlap", "competition"])
def test_scope_eval_ignores_real_mechanical_only_dto_warnings(kind):
    view = mechanical_view(kind)
    assert view.advisories == ()
    assert all(p.requires_review and p.warning for p in view.draft.proposals)
    assert all("不在本轮去处里" not in p.warning for p in view.draft.proposals)
    if kind == "baseline_missing":
        assert view.draft.proposals[0].base_version_no is None
        assert "baseline_missing" in view.draft.proposals[0].warning
    elif kind == "proposal_stale":
        assert view.draft.proposals[0].before.text == "旧文"
        assert "proposal_stale" in view.draft.proposals[0].warning
    elif kind == "overlap":
        assert view.draft.proposals[0].overlap == "1/1"
    else:
        assert "p01 → p02" in view.draft.proposals[0].warning
    assert check(ASSERTION, view) is None


@pytest.mark.parametrize("kind", ["baseline_missing", "proposal_stale", "overlap"])
def test_scope_eval_counts_composed_scope_once_with_an_inside_mechanical_card(kind):
    view = mechanical_view(kind, outside=True, extra_inside=True)
    outside, inside = view.draft.proposals
    assert "不在本轮去处里" in outside.warning
    assert outside.requires_review and inside.requires_review
    assert "不在本轮去处里" not in inside.warning
    assert len(view.advisories) == 1
    assert check(ASSERTION, view) is None


@pytest.mark.parametrize("kind", ["baseline_missing", "proposal_stale", "overlap"])
def test_real_scope_missing_card_label_fails_even_if_mechanical_warning_remains(kind):
    original = mechanical_view(kind, outside=True)
    view = without_card_scope_warning(original)
    assert view.advisories == original.advisories
    assert view.draft.proposals[0].requires_review
    assert view.draft.proposals[0].warning
    assert "不在本轮去处里" not in view.draft.proposals[0].warning
    assert check(ASSERTION, view) is not None


@pytest.mark.parametrize("kind", ["baseline_missing", "proposal_stale", "overlap"])
def test_real_scope_missing_top_label_still_fails(kind):
    original = mechanical_view(kind, outside=True)
    view = replace(original, advisories=())
    assert "不在本轮去处里" in view.draft.proposals[0].warning
    assert view.draft.proposals[0].requires_review
    assert check(ASSERTION, view) is not None
