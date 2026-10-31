"""节点1：真实 PG 交错；门控不替换 SQL/参数/cursor，锁等待由服务器证明。"""

from __future__ import annotations

import asyncio
import threading
import time
import traceback
from dataclasses import replace
from datetime import timedelta

import pytest

from pmstudio.api.errors import status_for
from pmstudio.application.agents.schemas import ResearchPacket
from pmstudio.application.dto import ResearchHit
from pmstudio.application.errors import UnknownProject
from pmstudio.application.project_research import StartWebResearch
from pmstudio.application.use_cases import AnswerInput, EditEntry, SubmitAnswers
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.version import VersionTrigger
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationKind,
    ExplorationScope,
    ScopeKind,
)
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import Question, Questionnaire, QuestionType
from pmstudio.domain.round.round import Round
from pmstudio.domain.services.committer import ProposalCommitter
from pmstudio.domain.services.scope_guard import ScopeGuard
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    ExplorationId,
    FormId,
    ProposalId,
    QuestionId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from pmstudio.harness.errors import ApplicationError
from pmstudio.infrastructure.postgres import owner_scope
from pmstudio.infrastructure.postgres.database import PgConnection
from pmstudio.infrastructure.postgres.materials import SqliteMaterialStore
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.support.builders import (
    NOW,
    PROJECT,
    make_claim,
    make_document,
    make_material_packet,
    make_seat,
)
from tests.support.doubles import FixedClock, RecordingPublisher, SeqIds, StaticSeatCatalog
from tests.support.leases import bind_exploration_lease, bind_round_lease
from tests.support.postgres import direct_url, open_direct

OWNER = UserId("u-owner-scope-test")
OTHER = UserId("u-node1-other")
WAIT = 8
REFS = (EvidenceId("E1"),)


class ReadGate:
    """在首条真实 SELECT 前/后通知；完成后的原 cursor 保持原语句快照。"""

    def __init__(self, connection, table):
        self.connection = connection
        self.table = table
        self.entered = threading.Event()
        self.read = threading.Event()
        self.resume = threading.Event()
        self.selects = 0
        self.gate_sql = True
        self.hydrations = 0
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append(query)
        gated = query.lstrip().upper().startswith("SELECT") and f"FROM {self.table}" in query
        if gated:
            self.selects += 1
        first = gated and self.selects == 1 and self.gate_sql
        if first:
            self.entered.set()
        cursor = self.connection.execute(query, params)
        if first:
            self.read.set()
            assert self.resume.wait(WAIT), "读后门控超时"
        return cursor


class GatedDocuments(SqliteDocumentRepository):
    """丢更新门控在整个聚合 hydrate 之后，保证旧实现确实两读 v2。"""

    def __init__(self, connection):
        super().__init__(connection)
        self.gate = connection
        connection.gate_sql = False

    def _read(self, load):
        first = self.gate.hydrations == 0
        self.gate.hydrations += 1
        if first:
            self.gate.entered.set()
        document = load()
        if first:
            self.gate.read.set()
            assert self.gate.resume.wait(WAIT), "聚合读后门控超时"
        return document

    def get(self, project_id, *, owner=None):
        return self._read(lambda: super(GatedDocuments, self).get(project_id, owner=owner))

    def get_for_update(self, project_id, owner=None):
        return self._read(
            lambda: super(GatedDocuments, self).get_for_update(project_id, owner=owner)
        )


class Worker:
    def __init__(self, action, *, table):
        self.errors = []
        self.result = None
        self.ready = threading.Event()
        self.done = threading.Event()
        self.connection = None
        self.gate = None
        self.pid = None

        def run():
            token = owner_scope.set_owner_scope(OWNER)
            try:
                with open_direct() as connection:
                    self.connection = connection
                    connection.execute("SET statement_timeout = '12s'")
                    connection.execute("SET lock_timeout = '10s'")
                    connection.execute("SET idle_in_transaction_session_timeout = '20s'")
                    self.pid = connection.info.backend_pid
                    self.gate = ReadGate(connection, table)
                    self.ready.set()
                    self.result = action(self.gate)
            except BaseException:
                self.errors.append(traceback.format_exc())
            finally:
                owner_scope.clear_owner_scope(token)
                self.ready.set()
                self.done.set()

        self.thread = threading.Thread(target=run, name="node1-pg-writer")
        self.thread.start()
        assert self.ready.wait(WAIT), "连接准备超时"

    def finish(self):
        assert self.done.wait(WAIT), "worker 未结束"
        self.thread.join(WAIT)
        assert not self.thread.is_alive()
        assert not self.errors, "\n".join(self.errors)
        return self.result


@pytest.fixture
def workers():
    started = []

    def start(action, table="documents"):
        worker = Worker(action, table=table)
        started.append(worker)
        return worker

    yield start
    for worker in started:
        if worker.gate is not None:
            worker.gate.resume.set()
    for worker in started:
        worker.thread.join(1)
        if worker.thread.is_alive() and worker.connection is not None:
            worker.connection.cancel()
        worker.thread.join(15)
        assert not worker.thread.is_alive(), "测试线程泄漏"


@pytest.fixture
def db(pg_test_database):
    with open_direct() as connection:
        row = connection.execute("SELECT current_schema() AS schema").fetchone()
        assert row["schema"] == pg_test_database.schema
        documents = SqliteDocumentRepository(connection)
        ledger = EvidenceLedger.empty(PROJECT)
        ledger.record(
            kind=EvidenceKind.USER_INPUT,
            text="原始用户依据",
            source=EvidenceSource(round_id=RoundId("r-seed"), raw_span="原始用户依据"),
            now=NOW,
        )
        document = make_document()
        document.apply((instruction("x", "原始 X"), instruction("y", "原始 Y", section="s2")),
                       now=NOW, summary="两条基线")
        with PgUnitOfWork(connection):
            documents.save(document)
            SqliteEvidenceRepository(connection).save(ledger)
        yield connection


def instruction(entry, text, *, section="s1", op=WriteOp.ADD, refs=REFS):
    return WriteInstruction(
        instruction_id=f"i-{entry}", entry_id=EntryId(entry), op=op,
        target=Target.body(section), text=text, refs=refs,
    )


def committer(connection, uow=None):
    documents = GatedDocuments(connection)
    rounds = SqliteRoundRepository(connection)
    return ProposalCommitter(
        documents=documents, writer=documents, rounds=rounds, round_writer=rounds,
        evidence=SqliteEvidenceRepository(connection), scope=ScopeGuard(),
        traceability=TraceabilityChecker(), uow=uow or PgUnitOfWork(connection),
    )


def seed_round(connection, round_id, *, questions=False):
    target = Round.start(round_id=RoundId(round_id), project_id=PROJECT,
                         user_input="补充需求", now=NOW)
    target.attach_claims((make_claim(eid=EvidenceId("E1")),))
    target.submit_opening(seats=(make_seat().seat_id,))
    if questions:
        target.integrate(questionnaire=Questionnaire(
            form_id=FormId(f"f-{round_id}"), round_id=target.round_id,
            questions=(Question(question_id=QuestionId("q1"), target=Target.body("s1"),
                                type=QuestionType.TEXT, title="补充哪条事实？", why="需拍板",
                                consequence_if_skipped="仍未决"),),
        ))
    else:
        target.integrate(draft=Draft(
            draft_id=DraftId(f"d-{round_id}"), round_id=target.round_id,
            proposals=(Proposal(
                proposal_id=ProposalId("p1"), entry_id=EntryId("b"), op=WriteOp.ADD,
                target=Target.body("s2"), text="B 新事实",
                origin=Origin(OriginType.PROPOSED, (EvidenceId("E1"),)),
                base_version_no=2,
            ),),
        ))
    SqliteRoundRepository(connection).save(target)


def block_or_old_read(observer, waiting, holder, read):
    """旧实现读完就放行复现覆盖；新实现必须留下真实 blocking_pids。"""

    holder_pid = holder.pid if isinstance(holder, Worker) else holder.info.backend_pid
    assert waiting.pid != holder_pid
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        row = observer.execute(
            "SELECT pg_blocking_pids(%s) AS blockers", (waiting.pid,)
        ).fetchone()
        if holder_pid in row["blockers"]:
            print(f"PG lock wait: waiter={waiting.pid} holder={holder_pid} "
                  f"pg_blocking_pids={row['blockers']}")
            return True
        if read.is_set() or waiting.done.is_set():
            return False
        time.sleep(0.01)
    raise AssertionError("既未观察到等待也未读完")


def entry_texts(document):
    return {str(entry.entry_id): entry.text for block in document.blocks for entry in block.entries}


@pytest.mark.parametrize("entrypoint", ["confirm", "manual", "rollback"])
def test_three_document_writers_wait_before_read_and_keep_consistent_history(
    db, workers, entrypoint
):
    seed_round(db, "r-confirm")

    def first(connection):
        return committer(connection).apply_manual(
            project_id=PROJECT, instruction=instruction("a", "A 新事实"), now=NOW, summary="A")

    def second(connection):
        writer = committer(connection)
        if entrypoint == "confirm":
            bind_round_lease(connection.connection, "r-confirm", owner=OWNER)
            return writer.commit(round_id=RoundId("r-confirm"),
                                 draft_id=DraftId("d-r-confirm"),
                                 accepted=(ProposalId("p1"),), now=NOW)
        if entrypoint == "rollback":
            return writer.rollback_to(project_id=PROJECT, version_no=2, now=NOW, summary="回退")
        return writer.apply_manual(project_id=PROJECT, instruction=instruction("b", "B 新事实"),
                                   now=NOW, summary="B")

    a = workers(first)
    assert a.gate.read.wait(WAIT)
    b = workers(second)
    assert b.gate.entered.wait(WAIT)
    blocked = block_or_old_read(db, b, a, b.gate.read)
    a.gate.resume.set()
    assert a.finish().version.version_no == 3
    b.gate.resume.set()
    result = b.finish()
    document = SqliteDocumentRepository(db).get(PROJECT)
    assert result.version.version_no == 4, "两个旧 v2 不能互相覆盖同一个 v3"
    assert [v.version_no for v in document.versions] == [1, 2, 3, 4]
    assert entry_texts(document) == (
        {"x": "原始 X", "y": "原始 Y"} if entrypoint == "rollback"
        else {"x": "原始 X", "y": "原始 Y", "a": "A 新事实", "b": "B 新事实"}
    )
    assert document.blocks == document.current_version.snapshot.blocks
    assert "a" in entry_texts(Document(
        document_id=document.document_id, project_id=PROJECT, title=document.title,
        blocks=document.version(3).snapshot.blocks, versions=document.versions[:3], created_at=NOW,
    ))
    assert blocked, "等待必须发生在锁读处，不能只在 stale 候选的 UPDATE 处"


def test_normal_get_hydrates_one_statement_snapshot_when_writer_commits_between_reads(db, workers):
    reader = workers(lambda connection: SqliteDocumentRepository(connection).get(PROJECT))
    assert reader.gate.read.wait(WAIT)
    document = SqliteDocumentRepository(db).get(PROJECT)
    document.apply((instruction("a", "后来提交"),), now=NOW, summary="新 v3")
    SqliteDocumentRepository(db).save(document)
    print(f"fractured read gate: reader={reader.pid} writer={db.info.backend_pid}")
    assert reader.pid != db.info.backend_pid
    reader.gate.resume.set()
    loaded = reader.finish()
    assert loaded.current_version.version_no == 2, "旧 blocks 不得搭配新 head"
    assert loaded.blocks == loaded.current_version.snapshot.blocks
    assert entry_texts(loaded) == {"x": "原始 X", "y": "原始 Y"}
    assert len(reader.gate.queries) == 1, "普通 hydrate 必须只有一个 SQL 语句快照"


def test_two_answer_consumers_lock_ledger_before_issuing_different_eids(db, workers):
    for rid in ("r-a", "r-b"):
        seed_round(db, rid, questions=True)

    def submit(rid, text):
        def action(connection):
            bind_round_lease(connection.connection, rid, owner=OWNER)
            rounds = SqliteRoundRepository(connection)
            evidence = SqliteEvidenceRepository(connection)
            use_case = SubmitAnswers(
                rounds=rounds, round_writer=rounds, evidence=evidence, evidence_writer=evidence,
                catalog=StaticSeatCatalog((make_seat(),)),
                uow=PgUnitOfWork(connection), clock=FixedClock(NOW),
                events=RecordingPublisher(),
            )
            asyncio.run(use_case(round_id=RoundId(rid), form_id=f"f-{rid}",
                                  answers=(AnswerInput(question_id="q1", text=text),)))
        return action

    a = workers(submit("r-a", "事实 A"), table="evidence_ledgers")
    assert a.gate.read.wait(WAIT)
    b = workers(submit("r-b", "事实 B"), table="evidence_ledgers")
    assert b.gate.entered.wait(WAIT)
    blocked = block_or_old_read(db, b, a, b.gate.read)
    a.gate.resume.set()
    a.finish()
    b.gate.resume.set()
    b.finish()
    facts = SqliteEvidenceRepository(db).get(PROJECT).evidences()
    assert [(str(e.eid), e.text) for e in facts] == [
        ("E1", "原始用户依据"), ("E2", "补充哪条事实？ → 事实 A"),
        ("E3", "补充哪条事实？ → 事实 B"),
    ], "两个发 E 事务不能覆盖前者的事实"
    assert [str(e.source.round_id) for e in facts[1:]] == ["r-a", "r-b"]
    for rid, text in (("r-a", "事实 A"), ("r-b", "事实 B")):
        assert SqliteRoundRepository(db).get(RoundId(rid)).answer_sets[-1].answers[0].text == text
    assert blocked


def test_edit_outer_first_read_waits_and_retains_the_latest_old_refs(db, workers):
    ledger = SqliteEvidenceRepository(db).get(PROJECT)
    ledger.record(kind=EvidenceKind.MANUAL, text="先行修订依据", source=EvidenceSource(), now=NOW)
    SqliteEvidenceRepository(db).save(ledger)

    def first(connection):
        return committer(connection).apply_manual(
            project_id=PROJECT,
            instruction=instruction("x", "先行修订", op=WriteOp.REPLACE,
                                    refs=(EvidenceId("E1"), EvidenceId("E2"))),
            now=NOW, summary="先行修订",
        )

    def edit(connection):
        uow = PgUnitOfWork(connection)
        events = RecordingPublisher()
        use_case = EditEntry(
            documents=GatedDocuments(connection), evidence=SqliteEvidenceRepository(connection),
            evidence_writer=SqliteEvidenceRepository(connection),
            committer=committer(connection, uow),
            uow=uow, clock=FixedClock(NOW), ids=SeqIds(), events=events,
        )
        result = asyncio.run(use_case(project_id=PROJECT, op=WriteOp.REPLACE,
                                       entry_id="x", text="最终手改"))
        return result, events

    a = workers(first)
    assert a.gate.read.wait(WAIT)
    b = workers(edit)
    assert b.gate.entered.wait(WAIT)
    blocked = block_or_old_read(db, b, a, b.gate.read)
    a.gate.resume.set()
    a.finish()
    b.gate.resume.set()
    result, events = b.finish()
    document = SqliteDocumentRepository(db).get(PROJECT)
    entry = next(e for block in document.blocks for e in block.entries if str(e.entry_id) == "x")
    assert tuple(map(str, entry.refs)) == ("E1", "E2", "E3"), "首次旧 refs 读取不能抢在锁外"
    assert result.version_no == 4
    assert entry.text == "最终手改"
    assert len(events.published) == 1
    assert blocked


def document_with_versions(document, versions):
    return Document(
        document_id=document.document_id, project_id=document.project_id, title=document.title,
        template=document.template, blocks=document.blocks, versions=versions,
        preference_entries=document.preference_entries, tombstones=document.tombstones,
        created_at=NOW,
    )


def stored_rows(connection):
    return (
        connection.execute(
            "SELECT * FROM documents WHERE project_id = %s", (str(PROJECT),)
        ).fetchall(),
        connection.execute(
            "SELECT * FROM document_versions WHERE project_id = %s ORDER BY version_no",
            (str(PROJECT),),
        ).fetchall(),
    )


@pytest.mark.parametrize(
    "difference", ["snapshot", "summary", "trigger", "changed", "time", "stale"]
)
@pytest.mark.parametrize("boundary", ["standalone", "outer_catches", "nested_catches"])
def test_new_version_collision_and_stale_head_fail_without_half_writes(db, difference, boundary):
    documents = SqliteDocumentRepository(db)
    candidate = documents.get(PROJECT)
    winner = documents.get(PROJECT)
    winner.apply((instruction("a", "赢者正文"),), now=NOW, summary="赢者 v3")
    documents.save(winner)
    if difference != "stale":
        candidate.apply((instruction("a", "输者正文" if difference == "snapshot" else "赢者正文"),),
                        now=NOW, summary="输者元信息" if difference == "summary" else "赢者 v3")
        last = candidate.current_version
        if difference == "trigger":
            last = replace(last, trigger=VersionTrigger.ROLLBACK)
        elif difference == "changed":
            last = replace(last, changed_block_ids=())
        elif difference == "time":
            last = replace(last, created_at=NOW + timedelta(seconds=1))
        candidate = document_with_versions(candidate, (*candidate.versions[:-1], last))
    before = stored_rows(db)

    def attempt():
        with pytest.raises(ApplicationError) as error:
            documents.save(candidate)
        assert error.value.code == "document_version_conflict"
        assert status_for(error.value) == 409

    if boundary == "standalone":
        attempt()
    else:
        uow = PgUnitOfWork(db)
        with uow:
            if boundary == "nested_catches":
                with uow:
                    attempt()
            else:
                attempt()
            assert stored_rows(db) == before
    assert stored_rows(db) == before, "异常被外层接住/仓储独立 save 也不得留下半笔"


def test_immutable_history_replay_is_idempotent_and_owner_scoped(db):
    documents = SqliteDocumentRepository(db)
    document = documents.get(PROJECT)
    versions = documents.version_rows(PROJECT)
    documents.save(document)
    documents.save(document)
    assert documents.version_rows(PROJECT) == versions
    document.apply((instruction("a", "越权正文"),), now=NOW, summary="越权")
    before = stored_rows(db)
    documents.save(document, owner=OTHER)
    assert stored_rows(db) == before
    for repository in (documents, SqliteEvidenceRepository(db)):
        for method in (repository.get, repository.get_for_update):
            with pytest.raises(UnknownProject):
                method(PROJECT, owner=OTHER)
    assert documents.version_rows(PROJECT, owner=OTHER) == []


def test_confirm_waits_for_round_control_before_touching_document(db, workers):
    seed_round(db, "r-confirm")
    leased = threading.Event()
    proceed = threading.Event()

    def confirm(connection):
        bind_round_lease(connection.connection, "r-confirm", owner=OWNER)
        leased.set()
        assert proceed.wait(WAIT)
        return committer(connection).commit(
            round_id=RoundId("r-confirm"), draft_id=DraftId("d-r-confirm"),
            accepted=(ProposalId("p1"),), now=NOW)

    b = workers(confirm)
    assert leased.wait(WAIT)
    try:
        with PgUnitOfWork(db):
            db.execute("SELECT 1 FROM rounds WHERE round_id = %s FOR UPDATE", ("r-confirm",))
            proceed.set()
            blocked = block_or_old_read(db, b, db, b.gate.entered)
            document_touched = b.gate.entered.is_set()
    finally:
        proceed.set()
        b.gate.resume.set()
    assert b.finish().version.version_no == 3
    assert blocked and not document_touched, "confirm 必须 Round → Document"


def test_document_lock_survives_nested_committer_until_outer_uow_exit(db, workers):
    applied = threading.Event()
    release_outer = threading.Event()

    def outer(connection):
        uow = PgUnitOfWork(connection)
        with uow:
            result = committer(connection, uow).apply_manual(
                project_id=PROJECT, instruction=instruction("a", "嵌套写"), now=NOW, summary="嵌套")
            applied.set()
            assert release_outer.wait(WAIT)
        return result

    a = workers(outer)
    assert a.gate.read.wait(WAIT)
    a.gate.resume.set()
    try:
        assert applied.wait(WAIT)
        b = workers(lambda connection: SqliteDocumentRepository(connection).get_for_update(PROJECT))
        assert b.gate.entered.wait(WAIT)
        blocked = block_or_old_read(db, b, a, b.gate.read)
    finally:
        release_outer.set()
    a.finish()
    b.gate.resume.set()
    assert b.finish().current_version.version_no == 3
    assert blocked, "内层 savepoint 结束不能提前释放外层 UoW 的文档锁"


def test_project_research_locks_control_before_ledger_without_document_lock(db, workers):
    eid = ExplorationId("x-research")
    explorations = SqliteExplorationRepository(db)
    explorations.save(Exploration.start(
        exploration_id=eid, project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.TOPIC, text="来源研究"), now=NOW,
        kind=ExplorationKind.WEB_RESEARCH,
    ))
    packet = make_material_packet("真实测试来源正文", origin="web_research")
    hit = ResearchHit(url="https://example.com/node1", title="测试来源", excerpt="来源摘要",
                      parsed=packet.parsed, content=b"source")
    leased = threading.Event()
    proceed = threading.Event()

    def record(connection):
        bind_exploration_lease(connection.connection, eid, owner=OWNER)
        leased.set()
        assert proceed.wait(WAIT)
        repository = SqliteExplorationRepository(connection)
        evidence = SqliteEvidenceRepository(connection)
        use_case = StartWebResearch(
            documents=SqliteDocumentRepository(connection), explorations=repository,
            exploration_writer=repository, evidence=evidence, evidence_writer=evidence,
            materials=SqliteMaterialStore(connection, ids=SeqIds()), uow=PgUnitOfWork(connection),
            clock=FixedClock(NOW), ids=SeqIds(), events=RecordingPublisher(),
            gateway=None, prompts=None, synthesizer=None, toolbox=None,
        )
        return use_case._record(eid, hits=(hit,), outcome=ResearchPacket(), notes=[])

    b = workers(record, table="evidence_ledgers")
    assert leased.wait(WAIT)
    try:
        with PgUnitOfWork(db):
            db.execute(
                "SELECT 1 FROM explorations WHERE exploration_id = %s FOR UPDATE", (str(eid),)
            )
            proceed.set()
            blocked = block_or_old_read(db, b, db, b.gate.read)
            ledger_touched = b.gate.entered.is_set()
    finally:
        proceed.set()
        b.gate.resume.set()
    sources = b.finish()
    assert len(sources) == 1 and str(sources[0].eid) == "E2"
    assert blocked and not ledger_touched, "研究须先自己的控制行 → Ledger，不反向等控制行"
    assert not any("FROM documents" in query for query in b.gate.queries)


@pytest.mark.parametrize("wrapper", [False, True])
def test_failure_after_a_new_version_insert_rolls_back_standalone_save(db, wrapper):
    before = stored_rows(db)
    document = SqliteDocumentRepository(db).get(PROJECT)
    document.apply((instruction("a", "新 v3"),), now=NOW, summary="v3")
    document.apply((instruction("b", "新 v4"),), now=NOW, summary="v4")

    class FailFourth:
        def __init__(self, connection):
            self.connection = getattr(connection, "connection", connection)
            self.executor = connection

        def execute(self, query, params=None):
            if "INSERT INTO document_versions" in query and params[2] == 4:
                raise RuntimeError("v4 插入故障")
            return self.executor.execute(query, params)

    if wrapper:
        with PgConnection(direct_url()) as handle:
            failing = FailFourth(handle)
            with pytest.raises(RuntimeError, match="v4 插入故障"):
                SqliteDocumentRepository(failing).save(document)
    else:
        with pytest.raises(RuntimeError, match="v4 插入故障"):
            SqliteDocumentRepository(FailFourth(db)).save(document)
    assert stored_rows(db) == before


def test_two_old_repository_candidates_wait_at_save_then_reject_the_loser(db, workers):
    read_a = threading.Event()
    read_b = threading.Event()
    start_a = threading.Event()
    start_b = threading.Event()
    saved_a = threading.Event()
    commit_a = threading.Event()

    def save_a(connection):
        connection.gate_sql = False
        repository = SqliteDocumentRepository(connection)
        with PgUnitOfWork(connection):
            document = repository.get(PROJECT)
            assert document.current_version.version_no == 2
            read_a.set()
            assert start_a.wait(WAIT)
            document.apply((instruction("a", "A 保存"),), now=NOW, summary="A v3")
            repository.save(document)
            saved_a.set()
            assert commit_a.wait(WAIT)

    def save_b(connection):
        connection.gate_sql = False
        repository = SqliteDocumentRepository(connection)
        with PgUnitOfWork(connection):
            document = repository.get(PROJECT)
            assert document.current_version.version_no == 2
            read_b.set()
            assert start_b.wait(WAIT)
            document.apply((instruction("b", "B 保存"),), now=NOW, summary="B v3")
            try:
                repository.save(document)
            except ApplicationError as error:
                return error.code
        return None

    a = workers(save_a)
    b = workers(save_b)
    try:
        assert read_a.wait(WAIT) and read_b.wait(WAIT)
        start_a.set()
        assert saved_a.wait(WAIT)
        start_b.set()
        blocked = block_or_old_read(db, b, a, b.done)
    finally:
        start_a.set()
        start_b.set()
        commit_a.set()
    a.finish()
    assert b.finish() == "document_version_conflict", "不能把冲突的 v3 INSERT 吞成成功"
    document = SqliteDocumentRepository(db).get(PROJECT)
    assert entry_texts(document) == {"x": "原始 X", "y": "原始 Y", "a": "A 保存"}
    assert [version.version_no for version in document.versions] == [1, 2, 3]
    assert document.blocks == document.current_version.snapshot.blocks
    assert blocked


def test_edit_ledger_failure_after_nested_document_save_rolls_back_everything(db):
    gate = ReadGate(db, "documents")
    gate.resume.set()
    uow = PgUnitOfWork(gate)
    events = RecordingPublisher()
    before_document = stored_rows(db)
    before_ledger = SqliteEvidenceRepository(db).get(PROJECT).evidences()

    class FailingEvidenceWriter(SqliteEvidenceRepository):
        def save(self, ledger):
            super().save(ledger)
            raise RuntimeError("账本保存后故障")

    use_case = EditEntry(
        documents=GatedDocuments(gate), evidence=SqliteEvidenceRepository(gate),
        evidence_writer=FailingEvidenceWriter(gate), committer=committer(gate, uow),
        uow=uow, clock=FixedClock(NOW), ids=SeqIds(), events=events,
    )
    with pytest.raises(RuntimeError, match="账本保存后故障"):
        asyncio.run(use_case(project_id=PROJECT, op=WriteOp.REPLACE,
                               entry_id="x", text="不能半写的手改"))
    assert stored_rows(db) == before_document
    assert SqliteEvidenceRepository(db).get(PROJECT).evidences() == before_ledger
    assert events.published == [], "事务失败不得发成功事件"
