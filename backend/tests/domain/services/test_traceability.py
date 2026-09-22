from datetime import datetime

import pytest

from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.errors import UnknownEvidence
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId

NOW = datetime(2026, 9, 22, 10, 0, 0)


def ledger_with_chain() -> tuple[EvidenceLedger, dict[str, EvidenceId]]:
    ledger = EvidenceLedger.empty(ProjectId("p1"))
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="数据量大概几万",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="数据量大概几万"),
        now=NOW,
    )
    derived = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出 60 秒内",
        source=EvidenceSource(
            seat_id="seat.data_scale", derived_from=(user,), rationale="量级在 1 万级"
        ),
        now=NOW,
    )
    proposed = ledger.record(
        kind=EvidenceKind.AGENT_PROPOSED,
        text="导出记录保留 30 天",
        source=EvidenceSource(seat_id="seat.rules", rationale="纯专业判断"),
        now=NOW,
    )
    return ledger, {"user": user, "derived": derived, "proposed": proposed}


def test_E4_ok_chain_reaches_user_input():
    ledger, ids = ledger_with_chain()
    result = TraceabilityChecker().check((ids["derived"],), ledger=ledger)
    assert result.traceable is True
    assert result.roots == (ids["user"],)
    assert result.reason == ""


def test_E4_bad_chain_ends_in_proposed():
    ledger, ids = ledger_with_chain()
    result = TraceabilityChecker().check((ids["proposed"],), ledger=ledger)
    assert result.traceable is False
    assert result.roots == ()
    assert "用户输入" in result.reason


def test_E4_bad_empty_refs():
    ledger, _ = ledger_with_chain()
    result = TraceabilityChecker().check((), ledger=ledger)
    assert result.traceable is False
    assert result.reason == "没有编号"


def test_E4_bad_unknown_ref_is_loud():
    ledger, _ = ledger_with_chain()
    with pytest.raises(UnknownEvidence):
        TraceabilityChecker().check((EvidenceId("E99"),), ledger=ledger)


def test_roots_are_deduped_and_keep_order():
    ledger, ids = ledger_with_chain()
    user = ids["user"]
    second_user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="运营每天都要用",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="运营每天都要用"),
        now=NOW,
    )
    first = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="导出要有权限控制",
        source=EvidenceSource(seat_id="seat.role", derived_from=(user,), rationale="含客户信息"),
        now=NOW,
    )
    combined = TraceabilityChecker().check((first, user, second_user), ledger=ledger)
    assert combined.traceable is True
    assert combined.roots == (user, second_user)
