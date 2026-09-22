import pytest

from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.round.claim import Assumption, Claim
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.shared.errors import (
    DecisionWithoutDecider,
    EmptyBasis,
    GuessWithoutAssumption,
    LabelZoneViolation,
    ShapeError,
)
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.invariants import AgentInvariant, LabelInvariant
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target


def claim(**overrides: object) -> Claim:
    payload = {
        "claim_id": "c1",
        "text": "要做一个订单导出功能",
        "label": ClaimLabel.NEED,
        "target": Target.body("s5"),
        "raw_span": "想做个订单导出的功能",
        "eid": EvidenceId("E1"),
    }
    payload.update(overrides)
    return Claim(**payload)  # type: ignore[arg-type]


def test_LB1_ok_preference_targets_preference_zone():
    item = claim(label=ClaimLabel.PREFERENCE, target=Target.preference(), raw_span="登录页太丑")
    assert item.target.label() == "preference"


def test_LB1_bad_preference_targets_body():
    with pytest.raises(LabelZoneViolation) as exc:
        claim(label=ClaimLabel.PREFERENCE, target=Target.body("s5"))
    assert exc.value.invariant == LabelInvariant.LB1


def test_LB2_ok_guess_with_assumption():
    item = claim(
        label=ClaimLabel.GUESS,
        target=Target.body("s10"),
        raw_span="数据量大概几万",
        assumption=Assumption(
            failure_consequence="若实际是百万级，同步导出会超时",
            verification="上线前用真实数据量压测一次",
        ),
    )
    assert item.assumption is not None


def test_LB2_bad_guess_without_assumption():
    with pytest.raises(GuessWithoutAssumption) as exc:
        claim(label=ClaimLabel.GUESS, target=Target.body("s10"))
    assert exc.value.invariant == LabelInvariant.LB2


def test_LB3_ok_decision_with_decider():
    item = claim(label=ClaimLabel.DECISION, target=Target.body("s9"), decided_by="PM")
    assert item.decided_by == "PM"


def test_LB3_bad_decision_without_decider():
    with pytest.raises(DecisionWithoutDecider) as exc:
        claim(label=ClaimLabel.DECISION, target=Target.body("s9"))
    assert exc.value.invariant == LabelInvariant.LB3


def test_claim_requires_raw_span():
    with pytest.raises(ShapeError):
        claim(raw_span="   ")


def test_only_guess_carries_assumption():
    with pytest.raises(ShapeError):
        claim(assumption=Assumption(failure_consequence="x", verification="y"))


def test_AG1_ok_empty_opinions_with_basis():
    review = SeatReview(seat_id="seat.data_scale", basis="读了 s5 与本轮 E1–E3，本领域无补充")
    assert review.opinions == ()


def test_AG1_bad_empty_basis():
    with pytest.raises(EmptyBasis) as exc:
        SeatReview(seat_id="seat.data_scale", basis="   ")
    assert exc.value.invariant == AgentInvariant.AG1


def test_AG1_bad_opinion_without_basis_note():
    with pytest.raises(EmptyBasis):
        Opinion(
            local_id="O1",
            form=OpinionForm.JUDGMENT,
            about="导出的性能上限",
            claim="单次导出 60 秒内",
            because="同类导出常见上限是 60 秒",
        )


def test_opinion_kind_hint_follows_basis():
    with_basis = Opinion(
        local_id="O1",
        form=OpinionForm.JUDGMENT,
        about="导出的性能上限",
        claim="单次导出 60 秒内",
        because="量级在 1 万级",
        evidence_refs=(EvidenceId("E4"),),
    )
    without_basis = Opinion(
        local_id="O2",
        form=OpinionForm.JUDGMENT,
        about="导出记录保留期",
        claim="保留 30 天",
        because="同类系统常见做法",
        basis_note="纯专业判断，无用户依据",
    )
    assert with_basis.kind_if_judgment is EvidenceKind.AGENT_DERIVED
    assert without_basis.kind_if_judgment is EvidenceKind.AGENT_PROPOSED


def test_seat_review_pass_index_defaults_to_one():
    review = SeatReview(seat_id="seat.data_scale", basis="读了 s5 与本轮 E1–E8")
    assert review.pass_index == 1


def test_seat_review_bad_pass_index_must_be_positive():
    with pytest.raises(ShapeError):
        SeatReview(seat_id="seat.data_scale", basis="读了 s5", pass_index=0)
