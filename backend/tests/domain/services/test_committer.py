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
                    base_version_no=1,
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
                base_version_no=1,
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
                    base_version_no=1,
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


def _commit_one_entry(fixture: "Fixture") -> None:
    """先真的往文档里加一条（后面才好删它）。"""

    fixture.add_draft()
    fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                               accepted=(ProposalId("p1"),), now=NOW)


def _round_with_proposals(fixture: "Fixture", round_id: str, proposals) -> None:
    """第二个轮次：claims → 开场 → 直接出草案（没有问卷那一条路）。"""

    round_ = Round.start(
        round_id=RoundId(round_id),
        project_id=PROJECT,
        user_input="把这一轮答过的未决项撤下",
        now=NOW,
    )
    round_.attach_claims(
        (
            Claim(
                claim_id="c1",
                text="撤下答过的未决项",
                label=ClaimLabel.NEED,
                target=Target.body("s11"),
                raw_span="撤下未决项",
                eid=EvidenceId("E1"),
            ),
        )
    )
    round_.submit_opening(seats=(SEAT,))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId(f"d-{round_id}"),
            round_id=RoundId(round_id),
            proposals=tuple(proposals),
        )
    )
    fixture.rounds.seed(round_)


def _remove(proposal_id: str, *, text: str = "撤下这条未决项", origin=OriginType.SYSTEM_DEFAULT):
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId("en1"),
        op=WriteOp.REMOVE,
        target=Target.body("s5"),
        text=text,
        origin=Origin(origin, (EvidenceId("E2"),), None),
        base_version_no=2,
    )


def test_two_removes_on_the_same_entry_commit_once():
    """P30 · F28 的领域形态：整批确认里两条 `remove` 指向同一条目。

    今天它抛 `UnknownEntry`（真跑里就是那个 422）——修完之后只算一条、正常写入。
    """

    fixture = Fixture()
    _commit_one_entry(fixture)
    _round_with_proposals(
        fixture,
        "r2",
        (
            _remove("p1", text="〔未决〕受试者编号规则"),
            _remove("p2", text="本轮已回答，撤下这条未决项"),
        ),
    )

    result = fixture.committer().commit(
        round_id=RoundId("r2"),
        draft_id=DraftId("d-r2"),
        accepted=(ProposalId("p1"), ProposalId("p2")),
        now=NOW,
    )

    assert result.version.version_no == 3
    assert result.merged_removes == (ProposalId("p2"),)
    document = fixture.documents.get(PROJECT)
    assert not [
        entry
        for block in document.blocks
        for entry in block.entries
        if entry.entry_id == EntryId("en1")
    ], "那条条目应该被撤下"


def test_the_merged_remove_can_also_be_accepted_alone():
    """只勾被合并的那一条也照样能写（它自己就是一条合法的 `remove`）。"""

    fixture = Fixture()
    _commit_one_entry(fixture)
    _round_with_proposals(
        fixture, "r2", (_remove("p1"), _remove("p2", text="本轮已回答，撤下这条未决项"))
    )

    result = fixture.committer().commit(
        round_id=RoundId("r2"), draft_id=DraftId("d-r2"), accepted=(ProposalId("p2"),), now=NOW
    )

    assert result.version.version_no == 3
    assert result.merged_removes == ()


def test_proposed_accepted_does_not_undercount_when_a_remove_is_merged():
    """被合并掉的那条若是 `proposed` 来源，C4 的"收了几条"不能少算。

    `taken` 必须从**用户接受的 id** 算，不能从合并后的提案列表算——
    否则 `proposal_accepted` 会凭空少一条，C4 那条"连续三轮全不勾"的提示会被带偏。
    """

    fixture = Fixture()
    _commit_one_entry(fixture)
    _round_with_proposals(
        fixture,
        "r2",
        (
            _remove("p1", text="〔未决〕编号规则", origin=OriginType.PROPOSED),
            _remove("p2", text="本轮已回答，撤下这条未决项", origin=OriginType.PROPOSED),
        ),
    )

    fixture.committer().commit(
        round_id=RoundId("r2"),
        draft_id=DraftId("d-r2"),
        accepted=(ProposalId("p1"), ProposalId("p2")),
        now=NOW,
    )

    round_ = fixture.rounds.get(RoundId("r2"))
    assert round_.proposed_offered == 2
    assert round_.proposed_accepted == 2, "合并掉的那条也是用户勾的，不能少算"


def test_commit_writes_entries_and_version_in_one_transaction():
    fixture = Fixture()
    fixture.add_draft()
    result = fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                                        accepted=(ProposalId("p1"),), now=NOW)
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
        round_id=ROUND_ID, draft_id=DraftId("d1"),
        accepted=(ProposalId("p1"), ProposalId("p2")), now=NOW
    )
    assert [item.code for item in result.advisories] == [OUTSIDE_ROUND_SCOPE]
    assert result.advisories[0].target == Target.body("s8")
    assert len(fixture.documents.get(PROJECT).blocks) > 13


def test_commit_bad_unknown_proposal():
    fixture = Fixture()
    fixture.add_draft()
    with pytest.raises(UnknownProposal):
        fixture.committer().commit(
            round_id=ROUND_ID, draft_id=DraftId("d1"), accepted=(ProposalId("p-forged"),), now=NOW
        )
    assert fixture.documents.saves == 0
    assert fixture.transaction.rollbacks == 1


def test_commit_bad_nothing_accepted():
    fixture = Fixture()
    fixture.add_draft()
    with pytest.raises(NothingToCommit):
        fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"), accepted=(), now=NOW)
    assert fixture.documents.get(PROJECT).current_version.version_no == 1


def test_commit_bad_untraceable_derived_proposal_writes_nothing():
    fixture = Fixture()
    fixture.add_draft(untraceable=True)
    with pytest.raises(UntraceableContent) as exc:
        fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                                   accepted=(ProposalId("p9"),), now=NOW)
    assert exc.value.invariant == EInvariant.E4
    assert fixture.documents.saves == 0
    assert fixture.documents.get(PROJECT).current_version.version_no == 1


def test_commit_ok_proposed_without_traceability_is_allowed():
    fixture = Fixture()
    fixture.add_draft(with_out_of_scope=True)
    result = fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                                        accepted=(ProposalId("p2"),), now=NOW)
    assert result.version.version_no == 2


def draft_with_one_derived_and_one_proposed() -> Draft:
    """一条"它替你判断的" + 一条"它提议的"——C4 的计数据的是**后者**。"""

    return Draft(
        draft_id=DraftId("d1"),
        round_id=ROUND_ID,
        proposals=(
            Proposal(
                proposal_id=ProposalId("p1"),
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body("s5"),
                text="验收标准：5 万条内 60 秒可下载",
                origin=Origin(OriginType.DERIVED, (EvidenceId("E2"),), "量级在 1 万级"),
                kind=BlockKind.ACCEPTANCE,
                base_version_no=1,
            ),
            Proposal(
                proposal_id=ProposalId("p3"),
                entry_id=EntryId("en3"),
                op=WriteOp.ADD,
                target=Target.body("s8"),
                text="导出记录保留 30 天",
                origin=Origin(OriginType.PROPOSED, (EvidenceId("E3"),), "纯专业判断"),
                kind=BlockKind.BULLET_LIST,
                base_version_no=1,
            ),
        ),
    )


def test_commit_records_zero_when_the_user_takes_no_proposed_content():
    """只勾了"它替你判断的" → 给 1 收 0（C4 的判据就落在这两个数上）。"""

    fixture = Fixture()
    fixture.round.integrate(draft=draft_with_one_derived_and_one_proposed())
    fixture.rounds.seed(fixture.round)

    fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                               accepted=(ProposalId("p1"),), now=NOW)

    round_ = fixture.rounds.get(ROUND_ID)
    assert round_.proposed_offered == 1
    assert round_.proposed_accepted == 0


def test_commit_records_one_when_the_user_takes_the_proposed_content():
    fixture = Fixture()
    fixture.round.integrate(draft=draft_with_one_derived_and_one_proposed())
    fixture.rounds.seed(fixture.round)

    fixture.committer().commit(
        round_id=ROUND_ID, draft_id=DraftId("d1"),
        accepted=(ProposalId("p1"), ProposalId("p3")), now=NOW
    )

    round_ = fixture.rounds.get(ROUND_ID)
    assert round_.proposed_offered == 1
    assert round_.proposed_accepted == 1


def test_commit_bad_round_write_failure_rolls_back_document():
    fixture = Fixture()
    fixture.add_draft()
    committer = fixture.committer(round_writer=FailingRoundWriter())
    with pytest.raises(RuntimeError):
        committer.commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                         accepted=(ProposalId("p1"),), now=NOW)
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
        fixture.committer().commit(round_id=ROUND_ID, draft_id=DraftId("d1"),
                                   accepted=(ProposalId("p1"),), now=NOW)
    assert fixture.documents.get(PROJECT).current_version.version_no == 1
    assert fixture.transaction.rollbacks == 1


def test_to_write_instruction_maps_allow_manual_override():
    """Proposal → WriteInstruction 1:1 带上 D5 例外位（M11）。"""

    from dataclasses import replace as _replace

    from pmstudio.domain.services.committer import to_write_instruction

    proposal = Proposal(
        proposal_id=ProposalId("sim#1"),
        entry_id=EntryId("en1"),
        op=WriteOp.REPLACE,
        target=Target.body("s5", feature_slug="order-export", field="priority"),
        text="待复核（模拟评测：2/3 个模拟使用者未能走通）",
        origin=Origin(OriginType.PROPOSED, (EvidenceId("E8"),), rationale="模拟评测"),
    )

    assert to_write_instruction(proposal).allow_manual_override is False
    flagged = _replace(proposal, allow_manual_override=True)
    assert to_write_instruction(flagged).allow_manual_override is True
