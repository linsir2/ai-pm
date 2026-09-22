from datetime import UTC, datetime

import pytest

from pmstudio.application.errors import UnknownProject, UnknownRound
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.ids import EntryId, EvidenceId, ProjectId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.support.builders import NOW, PROJECT, make_document, make_round


@pytest.fixture()
def repos(tmp_path):
    connection = database.connect(tmp_path / "pmstudio.db")
    yield (
        SqliteDocumentRepository(connection),
        SqliteRoundRepository(connection),
        SqliteEvidenceRepository(connection),
        SqliteWorkspaceIndex(connection),
        SqliteUnitOfWork(connection),
    )
    connection.close()


def test_document_round_trips_blocks_versions_and_tombstones(repos):
    documents, _, _, _, uow = repos
    document = make_document()
    with uow:
        documents.save(document)

    loaded = documents.get(PROJECT)

    assert loaded.title == document.title
    assert [str(block.block_id) for block in loaded.blocks] == [
        str(block.block_id) for block in document.blocks
    ]
    assert loaded.current_version.version_no == document.current_version.version_no
    assert loaded.current_version.trigger is document.current_version.trigger
    assert loaded.view().sections == document.view().sections
    assert loaded.preference_entries == document.preference_entries


def test_document_versions_are_append_only(repos):
    documents, _, _, _, uow = repos
    with uow:
        documents.save(make_document())
    with uow:
        loaded = documents.get(PROJECT)
        loaded.apply(
            [
                WriteInstruction(
                    instruction_id="i9",
                    entry_id=EntryId("r1#99"),
                    op=WriteOp.ADD,
                    target=Target.body("s1"),
                    text="背景：运营每天手工导表",
                    refs=(EvidenceId("E1"),),
                    kind=BlockKind.TEXT,
                )
            ],
            now=NOW,
            summary="第二版",
        )
        documents.save(loaded)

    rows = documents.version_rows(PROJECT)
    assert [row["version_no"] for row in rows] == [1, 2]
    assert rows[0]["summary"] == "初始化 13 章骨架"  # 旧版本行没有被改写


def test_round_round_trips_every_field(repos):
    _, rounds, _, _, uow = repos
    round_ = make_round()
    with uow:
        rounds.save(round_)

    loaded = rounds.get(RoundId("r1"))

    assert loaded.step is round_.step
    assert loaded.waiting_on is round_.waiting_on
    assert loaded.user_input == round_.user_input
    assert loaded.seats == round_.seats
    assert [str(claim.eid) for claim in loaded.claims] == [
        str(claim.eid) for claim in round_.claims
    ]
    assert loaded.claims[0].target == round_.claims[0].target
    assert loaded.claims[0].label is round_.claims[0].label
    assert loaded.created_at == round_.created_at
    assert loaded.seat_round_count == round_.seat_round_count


def test_ledger_round_trips_and_keeps_next_number(repos):
    documents, _, evidences, _, uow = repos
    with uow:
        documents.save(make_document())  # 账本表有 FK：先有文档才有账本
    ledger = EvidenceLedger.empty(PROJECT)
    first = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做订单导出",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="想做个订单导出"),
        now=NOW,
    )
    with uow:
        evidences.save(ledger)

    loaded = evidences.get(PROJECT)
    second = loaded.record(
        kind=EvidenceKind.USER_INPUT,
        text="数据量几万",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="数据量大概几万"),
        now=NOW,
    )

    assert str(first) == "E1"
    assert str(second) == "E2"  # 重水合以后编号接着长，不复用
    assert loaded.get(first).text == "要做订单导出"


def test_workspace_index_reports_summary_and_latest_round(repos):
    documents, rounds, _, index, uow = repos
    with uow:
        documents.save(make_document())
        rounds.save(make_round())

    summary = index.summary(PROJECT)
    assert summary.title == "订单中台"
    assert summary.version_no == 1
    assert index.latest_round_id(PROJECT) == RoundId("r1")
    assert [item.project_id for item in index.list_summaries()] == ["p1"]


def test_repositories_bad_unknown_ids(repos):
    documents, rounds, evidences, _, _ = repos
    with pytest.raises(UnknownProject):
        documents.get(ProjectId("p9"))
    with pytest.raises(UnknownProject):
        evidences.get(ProjectId("p9"))
    with pytest.raises(UnknownRound):
        rounds.get(RoundId("r9"))


def test_unit_of_work_rolls_back_on_exception(repos):
    documents, _, _, _, uow = repos
    with pytest.raises(RuntimeError):
        with uow:
            documents.save(make_document())
            raise RuntimeError("模拟写一半炸了")

    with pytest.raises(UnknownProject):
        documents.get(PROJECT)  # 回滚干净：库里没有这个项目


def test_summary_updated_at_comes_from_the_database(repos):
    documents, _, _, index, uow = repos
    with uow:
        documents.save(make_document())
    summary = index.summary(PROJECT)
    assert isinstance(summary.updated_at, datetime)
    assert summary.updated_at.tzinfo == UTC
