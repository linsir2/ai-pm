from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from pmstudio.domain.provenance.evidence import Evidence, EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.errors import (
    DerivedFromRequired,
    DuplicateEvidenceId,
    EvidenceSourceShape,
    SelfDerivation,
    UnknownEvidence,
)
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, QuestionId, RoundId
from pmstudio.domain.shared.invariants import EInvariant

NOW = datetime(2026, 9, 22, 10, 0, 0)


def empty_ledger() -> EvidenceLedger:
    return EvidenceLedger.empty(ProjectId("p1"))


def record_user_input(
    ledger: EvidenceLedger, text: str = "运营每天都要用", *, eid: EvidenceId | None = None
) -> EvidenceId:
    return ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text=text,
        source=EvidenceSource(round_id=RoundId("r1"), raw_span=text),
        now=NOW,
        eid=eid,
    )


def record_derived(
    ledger: EvidenceLedger, *, basis: tuple[EvidenceId, ...], text: str = "单次导出 60 秒内"
) -> EvidenceId:
    return ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text=text,
        source=EvidenceSource(
            seat_id="seat.data_scale",
            derived_from=basis,
            rationale="量级在 1 万级，同步实现可满足",
        ),
        now=NOW,
    )


def test_E1_ok_numbers_grow():
    ledger = empty_ledger()
    ids = [record_user_input(ledger, f"第{n}句") for n in (1, 2, 3)]
    assert ids == [EvidenceId("E1"), EvidenceId("E2"), EvidenceId("E3")]
    assert len(ledger) == 3


def test_E1_bad_duplicate_eid():
    ledger = empty_ledger()
    record_user_input(ledger, "第一句", eid=EvidenceId("E1"))
    with pytest.raises(DuplicateEvidenceId) as exc:
        record_user_input(ledger, "又一句", eid=EvidenceId("E1"))
    assert exc.value.invariant == EInvariant.E1


def test_E1_bad_gap_is_not_reused_after_rehydrate():
    evidences = tuple(
        Evidence.build(
            eid=EvidenceId(f"E{n}"),
            kind=EvidenceKind.USER_INPUT,
            text=f"第{n}句",
            source=EvidenceSource(round_id=RoundId("r1"), raw_span=f"第{n}句"),
            now=NOW,
        )
        for n in (1, 2, 5)
    )
    ledger = EvidenceLedger.rehydrate(project_id=ProjectId("p1"), evidences=evidences)
    assert record_user_input(ledger, "新的") == EvidenceId("E6")


def test_E2_ok_revise_appends_and_supersedes():
    ledger = empty_ledger()
    original = record_user_input(ledger, "量级几万")
    revised = ledger.revise(original, text="量级 3 万（用户订正）", now=NOW)
    assert revised == EvidenceId("E2")
    assert ledger.get(original).text == "量级几万"
    assert ledger.get(revised).supersedes == original
    assert ledger.superseded_by(original) == revised
    assert ledger.is_superseded(original) is True


def test_E2_bad_evidence_is_frozen():
    ledger = empty_ledger()
    original = record_user_input(ledger)
    with pytest.raises(FrozenInstanceError):
        ledger.get(original).text = "改历史"  # type: ignore[misc]
    assert not hasattr(ledger, "rewrite")


def test_E3_ok_derived_with_existing_basis():
    ledger = empty_ledger()
    basis = record_user_input(ledger)
    derived = record_derived(ledger, basis=(basis,))
    assert ledger.get(derived).source.derived_from == (basis,)


def test_E3_bad_derived_without_basis():
    ledger = empty_ledger()
    with pytest.raises(DerivedFromRequired) as exc:
        record_derived(ledger, basis=())
    assert exc.value.invariant == EInvariant.E3


def test_E3_bad_unknown_basis():
    ledger = empty_ledger()
    with pytest.raises(UnknownEvidence) as exc:
        record_derived(ledger, basis=(EvidenceId("E99"),))
    assert exc.value.invariant == EInvariant.E3


def test_E3_bad_self_derivation():
    ledger = empty_ledger()
    with pytest.raises(SelfDerivation) as exc:
        record_derived(ledger, basis=(EvidenceId("E1"),))
    assert exc.value.invariant == EInvariant.E3


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        (EvidenceKind.CARD_ANSWER, EvidenceSource(round_id=RoundId("r1"))),
        (EvidenceKind.AGENT_PROPOSED, EvidenceSource(rationale="纯专业判断")),
        (EvidenceKind.SYSTEM_DEFAULT, EvidenceSource(round_id=RoundId("r1"))),
        (EvidenceKind.EXTERNAL, EvidenceSource()),
    ],
)
def test_source_shape_per_kind(kind: EvidenceKind, source: EvidenceSource):
    ledger = empty_ledger()
    with pytest.raises(EvidenceSourceShape):
        ledger.record(kind=kind, text="内容", source=source, now=NOW)


def test_card_answer_needs_question_id():
    ledger = empty_ledger()
    eid = ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text="运营专员 + 主管",
        source=EvidenceSource(round_id=RoundId("r1"), question_id=QuestionId("q3")),
        now=NOW,
    )
    assert ledger.get(eid).source.question_id == QuestionId("q3")


def test_manual_edit_is_a_terminal_source():
    """手改的内容也算"用户直接给的"：不要求来源字段，并且是可追溯链的终点。

    依据：`04-未决.md` D1（手改内容如何获得出处）+ `01-规格.md` §5
    （卡片改的与文档改的谁优先——手改过的块 AI 不能再动）。
    """

    from pmstudio.domain.shared.evidence_kind import TERMINAL_KINDS

    ledger = empty_ledger()
    eid = ledger.record(
        kind=EvidenceKind.MANUAL,
        text="手工改写：把订单按时间筛出来导出成表格",
        source=EvidenceSource(),
        now=NOW,
    )

    assert ledger.get(eid).kind is EvidenceKind.MANUAL
    assert EvidenceKind.MANUAL in TERMINAL_KINDS


def test_chain_walks_derived_from_and_dedups():
    ledger = empty_ledger()
    user = record_user_input(ledger)
    first = record_derived(ledger, basis=(user,), text="判断一")
    second = record_derived(ledger, basis=(user, first), text="判断二")
    chain = ledger.chain(second)
    assert [item.eid for item in chain] == [second, first, user]
