from dataclasses import replace
from datetime import datetime

import pytest

from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Assumption, Claim
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.errors import DuplicateLocalId, UnknownEvidence
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target

NOW = datetime(2026, 9, 22, 10, 0, 0)
ROUND = RoundId("r1")


def new_ledger() -> EvidenceLedger:
    return EvidenceLedger.empty(ProjectId("p1"))


def _assumption() -> Assumption:
    return Assumption(
        failure_consequence="若实际是百万级，同步导出会超时",
        verification="上线前用真实数据量压测一次",
    )


def _user_source():
    from pmstudio.domain.provenance.evidence import EvidenceSource

    return EvidenceSource(round_id=ROUND, raw_span="数据量大概几万")


def test_assign_claims_records_user_input_with_raw_span():
    ledger = new_ledger()
    claims = (
        Claim(
            claim_id="c1",
            text="要做订单导出功能",
            label=ClaimLabel.NEED,
            target=Target.body("s5"),
            raw_span="想做个订单导出的功能",
        ),
        Claim(
            claim_id="c2",
            text="数据量大概几万",
            label=ClaimLabel.GUESS,
            target=Target.body("s10"),
            raw_span="数据量大概几万",
            assumption=_assumption(),
        ),
    )
    assigned = EvidenceAssigner().assign_claims(claims, ledger=ledger, round_id=ROUND, now=NOW)
    assert [item.eid for item in assigned] == [EvidenceId("E1"), EvidenceId("E2")]
    assert ledger.get(EvidenceId("E1")).kind is EvidenceKind.USER_INPUT
    assert ledger.get(EvidenceId("E1")).source.raw_span == "想做个订单导出的功能"
    assert claims[0].eid is None  # 原对象不被改动


def test_assign_opinions_numbers_only_judgments():
    ledger = new_ledger()
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="数据量大概几万",
        source=_user_source(),
        now=NOW,
    )
    review = SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5 与本轮 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.QUESTION,
                about="量级",
                claim="量级决定同步还是异步，必须问用户",
                because="1 万以内可同步，10 万以上必须异步",
                evidence_refs=(user,),
            ),
            Opinion(
                local_id="O2",
                form=OpinionForm.JUDGMENT,
                about="性能上限",
                claim="单次导出 60 秒内",
                because="量级在 1 万级",
                evidence_refs=(user,),
            ),
        ),
    )
    assignments = EvidenceAssigner().assign_opinions(
        (review,), ledger=ledger, round_id=ROUND, now=NOW
    )
    assert [(item.local_id, item.eid, item.kind) for item in assignments] == [
        ("O2", EvidenceId("E2"), EvidenceKind.AGENT_DERIVED)
    ]
    assert ledger.get(EvidenceId("E2")).source.derived_from == (user,)


def test_assign_opinions_marks_proposed_without_basis():
    ledger = new_ledger()
    review = SeatReview(
        seat_id="seat.rules",
        basis="读了 s5",
        opinions=(
            Opinion(
                local_id="O3",
                form=OpinionForm.JUDGMENT,
                about="导出记录保留期",
                claim="导出记录保留 30 天",
                because="同类系统常见做法",
                basis_note="纯专业判断，无用户依据",
            ),
        ),
    )
    assignments = EvidenceAssigner().assign_opinions(
        (review,), ledger=ledger, round_id=ROUND, now=NOW
    )
    assert assignments[0].kind is EvidenceKind.AGENT_PROPOSED
    assert ledger.get(assignments[0].eid).source.derived_from == ()


def test_same_local_id_in_two_seats_is_fine():
    ledger = new_ledger()
    reviews = tuple(
        SeatReview(
            seat_id=seat_id,
            basis="读了 s5",
            opinions=(
                Opinion(
                    local_id="O1",
                    form=OpinionForm.JUDGMENT,
                    about=f"{seat_id} 的判断",
                    claim=f"{seat_id} 认为要加权限",
                    because="含客户信息",
                    basis_note="纯专业判断",
                ),
            ),
        )
        for seat_id in ("seat.role", "seat.rules")
    )
    assignments = EvidenceAssigner().assign_opinions(
        reviews, ledger=ledger, round_id=ROUND, now=NOW
    )
    assert len(assignments) == 2
    assert [item.eid for item in assignments] == [EvidenceId("E1"), EvidenceId("E2")]


def test_duplicate_local_id_in_same_seat_raises():
    ledger = new_ledger()
    opinion = Opinion(
        local_id="O1",
        form=OpinionForm.JUDGMENT,
        about="性能",
        claim="60 秒内",
        because="常见上限",
        basis_note="纯专业判断",
    )
    review = SeatReview(seat_id="seat.data_scale", basis="读了 s5", opinions=(opinion, opinion))
    with pytest.raises(DuplicateLocalId):
        EvidenceAssigner().assign_opinions((review,), ledger=ledger, round_id=ROUND, now=NOW)


def test_opinion_referencing_unknown_evidence_fails_loudly():
    ledger = new_ledger()
    review = SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.JUDGMENT,
                about="性能",
                claim="60 秒内",
                because="依据 E9",
                evidence_refs=(EvidenceId("E9"),),
            ),
        ),
    )
    with pytest.raises(UnknownEvidence):
        EvidenceAssigner().assign_opinions((review,), ledger=ledger, round_id=ROUND, now=NOW)


def test_assign_opinions_is_idempotent_under_retry():
    """同一批意见被赋两次号：第二次复用第一次的编号，账本不增长（计划 §3.1 ⑤）。"""

    from pmstudio.domain.provenance.evidence import EvidenceSource

    ledger = new_ledger()
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做订单导出",
        source=EvidenceSource(round_id=ROUND, raw_span="想做个订单导出"),
        now=NOW,
    )
    review = SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5 与 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.JUDGMENT,
                about="性能上限",
                claim="60 秒内完成",
                because="同类做法",
                evidence_refs=(user,),
            ),
        ),
    )
    assigner = EvidenceAssigner()

    first = assigner.assign_opinions((review,), ledger=ledger, round_id=ROUND, now=NOW)
    size = len(ledger)
    second = assigner.assign_opinions((review,), ledger=ledger, round_id=ROUND, now=NOW)

    assert [str(item.eid) for item in first] == [str(item.eid) for item in second]
    assert len(ledger) == size
    assert ledger.find_by_local(seat_id="seat.data_scale", local_id="p1:O1") is not None


def test_the_same_opinion_slot_in_a_later_round_is_a_different_evidence():
    """真跑抓到（P22 回归）：两轮的 `(席位, 趟, 条目 id)` 会撞号。

    现场：第二轮 `seat.permissions:O3` 拿到了第一轮的编号 E15，而 E15 的账本文本是
    第一轮那条完全不同的判断——用户点编号看到的不是支撑这条内容的那句话。

    身份必须是 **(轮次, 趟, 席位, 条目 id)**：趟数每轮都从 1 重来，只带趟数不够。
    """

    from pmstudio.domain.provenance.evidence import EvidenceSource

    ledger = new_ledger()
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做订单导出",
        source=EvidenceSource(round_id=ROUND, raw_span="想做个订单导出"),
        now=NOW,
    )
    first = SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5 与 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.JUDGMENT,
                about="第一轮的判断",
                claim="第一轮：导出要分页",
                because="同类做法",
                evidence_refs=(user,),
            ),
        ),
    )
    second = replace(
        first,
        opinions=(
            replace(first.opinions[0], about="第二轮的判断", claim="第二轮：导出要脱敏"),
        ),
    )

    assigner = EvidenceAssigner()
    one = assigner.assign_opinions((first,), ledger=ledger, round_id=RoundId("r-one"), now=NOW)
    two = assigner.assign_opinions((second,), ledger=ledger, round_id=RoundId("r-two"), now=NOW)

    assert str(one[0].eid) != str(two[0].eid)
    assert ledger.get(two[0].eid).text == "第二轮：导出要脱敏"
    assert ledger.get(one[0].eid).text == "第一轮：导出要分页"


def test_retrying_the_same_round_still_reuses_the_number():
    """反方向不放松：**同一轮**重放（重试那一趟）照旧复用编号，不许每跑一次就多占一个号。"""

    from pmstudio.domain.provenance.evidence import EvidenceSource

    ledger = new_ledger()
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做订单导出",
        source=EvidenceSource(round_id=ROUND, raw_span="想做个订单导出"),
        now=NOW,
    )
    review = SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5 与 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.JUDGMENT,
                about="性能上限",
                claim="60 秒内完成",
                because="同类做法",
                evidence_refs=(user,),
            ),
        ),
    )

    assigner = EvidenceAssigner()
    first = assigner.assign_opinions((review,), ledger=ledger, round_id=ROUND, now=NOW)
    size = len(ledger)
    again = assigner.assign_opinions((review,), ledger=ledger, round_id=ROUND, now=NOW)

    assert str(first[0].eid) == str(again[0].eid)
    assert len(ledger) == size
