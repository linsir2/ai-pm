from datetime import datetime

import pytest

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.services.committer import ProposalCommitter
from pmstudio.domain.services.scope_guard import OUTSIDE_ROUND_SCOPE, ScopeGuard
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.errors import (
    IllegalStepTransition,
    NothingToCommit,
    UnknownProposal,
    UntraceableContent,
)
from pmstudio.domain.shared.ids import (
    DocumentId,
    DraftId,
    EntryId,
    EvidenceId,
    ProjectId,
    ProposalId,
    RoundId,
)
from pmstudio.domain.shared.invariants import EInvariant
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.fakes import (
    FailingRoundWriter,
    FakeTransaction,
    InMemoryDocumentRepository,
    InMemoryEvidenceRepository,
    InMemoryRoundRepository,
)

NOW = datetime(2026, 9, 22, 10, 0, 0)
PROJECT = ProjectId("p1")
ROUND_ID = RoundId("r1")
SEAT = "seat.data_scale"


class Fixture:
    def __init__(self) -> None:
        self.documents = InMemoryDocumentRepository()
        self.rounds = InMemoryRoundRepository()
        self.evidences = InMemoryEvidenceRepository()
        self.transaction = FakeTransaction(self.documents, self.rounds, self.evidences)
        self.documents.seed(
            Document.new(
                document_id=DocumentId("doc1"), project_id=PROJECT, title="测试文档", now=NOW
            )
        )
        self.ledger = EvidenceLedger.empty(PROJECT)
        user = self.ledger.record(
            kind=EvidenceKind.USER_INPUT,
            text="数据量大概几万",
            source=EvidenceSource(round_id=ROUND_ID, raw_span="数据量大概几万"),
            now=NOW,
        )
        derived = self.ledger.record(
            kind=EvidenceKind.AGENT_DERIVED,
            text="单次导出 60 秒内",
            source=EvidenceSource(seat_id=SEAT, derived_from=(user,), rationale="量级在 1 万级"),
            now=NOW,
        )
        proposed = self.ledger.record(
            kind=EvidenceKind.AGENT_PROPOSED,
            text="导出记录保留 30 天",
            source=EvidenceSource(seat_id="seat.rules", rationale="纯专业判断"),
            now=NOW,
        )
        self.evidences.seed(self.ledger)
        self.round = self._round_with_claims(derived=derived, proposed=proposed)

    def _round_with_claims(self, *, derived: EvidenceId, proposed: EvidenceId) -> Round:
        round_ = Round.start(
            round_id=ROUND_ID, project_id=PROJECT, user_input="数据量大概几万", now=NOW
        )
        round_.attach_claims(
            (
                Claim(
                    claim_id="c1",
                    text="要做订单导出功能",
                    label=ClaimLabel.NEED,
                    target=Target.body("s5"),
                    raw_span="想做个订单导出的功能",
                    eid=derived,
                ),
            )
        )
        round_.submit_opening(seats=(SEAT,))
        return round_

    def proposals(
        self, *, with_out_of_scope: bool = False, untraceable: bool = False
    ) -> tuple[Proposal, ...]:
        if untraceable:
            return (
                Proposal(
                    proposal_id=ProposalId("p9"),
                    entry_id=EntryId("en9"),
                    op=WriteOp.ADD,
                    target=Target.body("s5"),
                    text="导出要有权限控制",
                    origin=Origin(OriginType.DERIVED, (EvidenceId("E3"),), "没有用户依据"),
                    kind=BlockKind.BULLET_LIST,
                ),
            )
        items = [
            Proposal(
                proposal_id=ProposalId("p1"),
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body("s5"),
                text="验收标准：5 万条内 60 秒可下载",
                origin=Origin(OriginType.DERIVED, (EvidenceId("E2"),), "量级在 1 万级"),
                kind=BlockKind.ACCEPTANCE,
            )
        ]
        if with_out_of_scope:
            items.append(
                Proposal(
                    proposal_id=ProposalId("p2"),
                    entry_id=EntryId("en2"),
                    op=WriteOp.ADD,
                    target=Target.body("s8"),
                    text="单次导出 60 秒内完成",
                    origin=Origin(OriginType.PROPOSED, (EvidenceId("E3"),)),
                    kind=BlockKind.NFR,
                )
            )
        return tuple(items)

    def add_draft(
        self, *, with_out_of_scope: bool = False, untraceable: bool = False
    ) -> None:
        self.round.integrate(
            draft=Draft(
                draft_id=DraftId("d1"),
                round_id=ROUND_ID,
                proposals=self.proposals(
                    with_out_of_scope=with_out_of_scope, untraceable=untraceable
                ),
            )
        )
        self.rounds.seed(self.round)

    def committer(self, *, round_writer=None) -> ProposalCommitter:
        return ProposalCommitter(
            documents=self.documents,
            writer=self.documents,
            rounds=self.rounds,
            round_writer=round_writer or self.rounds,
            evidence=self.evidences,
            scope=ScopeGuard(),
            traceability=TraceabilityChecker(),
            uow=self.transaction,
        )


def test_commit_writes_entries_and_version_in_one_transaction():
    fixture = Fixture()
    fixture.add_draft()
    result = fixture.committer().commit(round_id=ROUND_ID, accepted=(ProposalId("p1"),), now=NOW)
    assert result.version.version_no == 2
    assert fixture.transaction.commits == 1
    assert fixture.transaction.rollbacks == 0
    assert fixture.documents.saves == 1
    assert fixture.rounds.saves == 1
    stored = fixture.documents.get(PROJECT)
    assert [entry.text for block in stored.blocks for entry in block.entries] == [
        "验收标准：5 万条内 60 秒可下载"
    ]
    assert fixture.rounds.get(ROUND_ID).confirmed_version_no == 2


def test_commit_reports_out_of_scope_as_advisory():
    fixture = Fixture()
    fixture.add_draft(with_out_of_scope=True)
    result = fixture.committer().commit(
        round_id=ROUND_ID, accepted=(ProposalId("p1"), ProposalId("p2")), now=NOW
    )
    assert [item.code for item in result.advisories] == [OUTSIDE_ROUND_SCOPE]
    assert result.advisories[0].target == Target.body("s8")
    assert len(fixture.documents.get(PROJECT).blocks) > 13


def test_commit_bad_unknown_proposal():
    fixture = Fixture()
    fixture.add_draft()
    with pytest.raises(UnknownProposal):
        fixture.committer().commit(
            round_id=ROUND_ID, accepted=(ProposalId("p-forged"),), now=NOW
        )
    assert fixture.documents.saves == 0
    assert fixture.transaction.rollbacks == 1


def test_commit_bad_nothing_accepted():
    fixture = Fixture()
    fixture.add_draft()
    with pytest.raises(NothingToCommit):
        fixture.committer().commit(round_id=ROUND_ID, accepted=(), now=NOW)
    assert fixture.documents.get(PROJECT).current_version.version_no == 1


def test_commit_bad_untraceable_derived_proposal_writes_nothing():
    fixture = Fixture()
    fixture.add_draft(untraceable=True)
    with pytest.raises(UntraceableContent) as exc:
        fixture.committer().commit(round_id=ROUND_ID, accepted=(ProposalId("p9"),), now=NOW)
    assert exc.value.invariant == EInvariant.E4
    assert fixture.documents.saves == 0
    assert fixture.documents.get(PROJECT).current_version.version_no == 1


def test_commit_ok_proposed_without_traceability_is_allowed():
    fixture = Fixture()
    fixture.add_draft(with_out_of_scope=True)
    result = fixture.committer().commit(round_id=ROUND_ID, accepted=(ProposalId("p2"),), now=NOW)
    assert result.version.version_no == 2


def test_commit_bad_round_write_failure_rolls_back_document():
    fixture = Fixture()
    fixture.add_draft()
    committer = fixture.committer(round_writer=FailingRoundWriter())
    with pytest.raises(RuntimeError):
        committer.commit(round_id=ROUND_ID, accepted=(ProposalId("p1"),), now=NOW)
    assert fixture.transaction.rollbacks == 1
    assert fixture.transaction.commits == 0
    assert fixture.documents.get(PROJECT).current_version.version_no == 1
    assert fixture.rounds.get(ROUND_ID).step.value != "done"


def test_commit_bad_round_is_not_awaiting_confirm():
    fixture = Fixture()
    fixture.add_draft()
    awaiting = fixture.rounds.get(ROUND_ID)
    # 直接构造一个"有草案但不在等确认"的轮，验证写入端会先拦下来
    stuck = Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input=awaiting.user_input,
        created_at=NOW,
        step=Step.SEATS_RUNNING,
        waiting_on=WaitingOn.NONE,
        claims=awaiting.claims,
        seats=awaiting.seats,
        seat_round_count=1,
        draft=awaiting.draft,
    )
    fixture.rounds.seed(stuck)
    with pytest.raises(IllegalStepTransition):
        fixture.committer().commit(round_id=ROUND_ID, accepted=(ProposalId("p1"),), now=NOW)
    assert fixture.documents.get(PROJECT).current_version.version_no == 1
    assert fixture.transaction.rollbacks == 1
