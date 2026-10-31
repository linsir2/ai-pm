"""真实 HTTP→graph→committer→PG；模型仅在独立创建测试中被替换。"""

from dataclasses import replace

import pytest

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.postgres import owner_scope
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.e2e.conftest import E2E_USERNAME
from tests.e2e.test_closed_loop import create_project
from tests.support.api_client import async_client
from tests.support.builders import NOW, SEAT, make_claim
from tests.support.postgres import open_direct

S1 = Target.body("s1")
MULTI = Target.body("s5", feature_slug="order-export", field="exceptions")
SINGLE = Target.body("s5", feature_slug="order-export", field="priority")
REFS = (EvidenceId("E1"),)
RID = RoundId("r-node3")
DID = DraftId("d-seen")


def write(entry="old", text="A", *, op=WriteOp.ADD, target=S1, refs=REFS):
    return WriteInstruction(entry, EntryId(entry), op, target, text, refs)


def proposal(pid="p01", entry="old", text="B", *, op=WriteOp.REPLACE, target=S1,
             base=2, override=False):
    return Proposal(ProposalId(pid), EntryId(entry), op, target, text,
                    Origin(OriginType.PROPOSED, REFS),
                    allow_manual_override=override, base_version_no=base)


def change_structure(document, transform):
    return Document(
        document_id=document.document_id, project_id=document.project_id, title=document.title,
        blocks=tuple(transform(b) for b in document.blocks), versions=document.versions,
        preference_entries=document.preference_entries, tombstones=document.tombstones,
        created_at=NOW, template=document.template,
    )


class Scenario:
    def __init__(self, app, project, connection):
        self.app, self.project, self.connection = app, project, connection
        self.documents = SqliteDocumentRepository(connection)
        self.rounds = SqliteRoundRepository(connection)
        self.document = self.documents.get(project)

    def apply(self, *items):
        self.document.apply(items, now=NOW, summary="测试基线",
                            authorized_new_features=frozenset({"order-export"}))

    def card(self, *items, draft_id=DID, authorize=False):
        round_ = Round.start(round_id=RID, project_id=self.project, user_input="本轮事实", now=NOW)
        round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
        round_.submit_opening(seats=(SEAT,))
        if authorize:
            round_.authorize_new_feature("order-export", basis=EvidenceId("E1"))
        round_.integrate(draft=Draft(draft_id, RID, items))
        with PgUnitOfWork(self.connection):
            self.documents.save(self.document)
            self.rounds.save(round_)

    def rows(self):
        return tuple(self.connection.execute(query).fetchall() for query in (
            "SELECT * FROM documents ORDER BY project_id",
            "SELECT * FROM document_versions ORDER BY project_id, version_no",
            "SELECT round_id, step, payload FROM rounds ORDER BY round_id",
            "SELECT * FROM project_memory ORDER BY project_id",
            "SELECT * FROM round_digests ORDER BY round_id",
            "SELECT * FROM evidence_ledgers ORDER BY project_id",
        ))

    async def confirm(self, accepted=("p01",), *, draft_id=str(DID)):
        async with async_client(self.app) as client:
            return await client.post(f"/api/rounds/{RID}/confirm",
                                     json={"draft_id": draft_id, "accepted": list(accepted)})


@pytest.fixture
async def scenario(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        project = await create_project(client)
    owner = app.state.auth.users.get_by_name(E2E_USERNAME).user_id
    token = owner_scope.set_owner_scope(owner)
    try:
        with open_direct() as connection:
            ledger = EvidenceLedger.empty(project)
            ledger.record(kind=EvidenceKind.USER_INPUT, text="真实输入",
                          source=EvidenceSource(round_id=RID, raw_span="真实输入"), now=NOW)
            ledger.record(kind=EvidenceKind.MANUAL, text="后续依据",
                          source=EvidenceSource(), now=NOW)
            SqliteEvidenceRepository(connection).save(ledger)
            yield Scenario(app, project, connection)
    finally:
        owner_scope.clear_owner_scope(token)


@pytest.mark.parametrize("accepted", [("p02", "p01"), ("p01", "p02", "p01")])
async def test_set_selection_restores_original_two_replace_order(scenario, accepted):
    scenario.apply(write())
    scenario.card(proposal(text="B"), proposal("p02", text="C"))
    response = await scenario.confirm(accepted)
    assert response.status_code == 200, response.text
    stored = scenario.documents.get(scenario.project)
    assert stored.locate_entry(EntryId("old")).entry.text == "C"
    assert [v.version_no for v in stored.versions] == [1, 2, 3]


@pytest.mark.parametrize("first", [WriteOp.REMOVE, WriteOp.REPLACE])
async def test_mixed_same_entry_order_is_atomic_and_reports_combination_conflict(scenario, first):
    scenario.apply(write())
    second = WriteOp.REPLACE if first is WriteOp.REMOVE else WriteOp.REMOVE
    scenario.card(proposal(op=first), proposal("p02", op=second))
    before = scenario.rows()
    response = await scenario.confirm(("p02", "p01"))
    if first is WriteOp.REMOVE:
        assert response.status_code == 409, response.text
        details = response.json()["details"]
        assert details["proposal_id"] == "p02"
        assert details["entry_id"] == "old"
        assert details["base_version_no"] == details["current_version_no"] == 2
        assert details["reason"] == "candidate_conflict"
        assert scenario.rows() == before
    else:
        assert response.status_code == 200, response.text
        assert not any(e.entry_id == "old" for b in scenario.documents.get(scenario.project).blocks
                       for e in b.entries)


@pytest.mark.parametrize("accepted,ok", [(('p-new',), True), (('p-old',), False),
                                       (('p-new', 'p-old'), False)])
async def test_cross_base_remove_is_checked_before_merge(scenario, accepted, ok):
    scenario.apply(write())
    scenario.apply(write(text="B", op=WriteOp.REPLACE))
    scenario.card(proposal("p-old", op=WriteOp.REMOVE),
                  proposal("p-new", op=WriteOp.REMOVE, base=3))
    before = scenario.rows()
    response = await scenario.confirm(accepted)
    assert response.status_code == (200 if ok else 409), response.text
    if not ok:
        assert response.json()["code"] == "proposal_stale"
        assert scenario.rows() == before


@pytest.mark.parametrize("change", ["refs", "missing", "owner", "kind", "d5", "restored"])
async def test_current_entry_and_structure_must_match_history(scenario, change):
    scenario.apply(write())
    if change == "refs":
        scenario.apply(write(op=WriteOp.REPLACE, refs=(EvidenceId("E2"),)))
    elif change == "missing":
        scenario.apply(write(op=WriteOp.REMOVE))
    elif change == "restored":
        scenario.apply(write(text="临时值", op=WriteOp.REPLACE))
        scenario.document.rollback_to(2, now=NOW, summary="全部恢复")
    else:
        scenario.document = change_structure(scenario.document, lambda b: (
            replace(b, parent_id="s2") if change == "owner" and b.entries else
            replace(b, kind=BlockKind.BULLET_LIST) if change == "kind" and b.entries else
            replace(b, manually_edited=True) if change == "d5" and b.entries else b
        ))
        scenario.apply(write("unrelated", "记录结构改变", target=Target.body("s3")))
    scenario.card(proposal(override=True))
    before = scenario.rows()
    response = await scenario.confirm()
    assert response.status_code == (200 if change == "restored" else 409), response.text
    if change != "restored":
        assert response.json()["code"] == "proposal_stale"
        assert scenario.rows() == before


async def test_unrelated_same_field_entry_changes_can_merge(scenario):
    scenario.apply(write("a", "A", target=MULTI), write("b", "B", target=MULTI))
    scenario.apply(write("b", "B2", op=WriteOp.REPLACE, target=MULTI))
    scenario.card(proposal(entry="a", text="A2", target=MULTI))
    response = await scenario.confirm()
    assert response.status_code == 200, response.text
    document = scenario.documents.get(scenario.project)
    entries = document.field_block("order-export", "exceptions").entries
    assert [(str(e.entry_id), e.text) for e in entries] == [("a", "A2"), ("b", "B2")]


@pytest.mark.parametrize("change", ["appeared", "parent", "unchanged"])
async def test_missing_carrier_tracks_the_real_existing_parent(scenario, change):
    scenario.apply(write("root", "功能", target=Target.body("s5", feature_slug="order-export")))
    pending = proposal(entry="new", text="新例外", op=WriteOp.ADD, target=MULTI)
    if change == "appeared":
        scenario.apply(write("other", "已有例外", target=MULTI))
    elif change == "parent":
        scenario.document = change_structure(scenario.document, lambda b:
            replace(b, manually_edited=True) if b.kind is BlockKind.FEATURE else b)
        scenario.apply(write("unrelated", "记下结构改动", target=Target.body("s3")))
    scenario.card(pending)
    before = scenario.rows()
    response = await scenario.confirm()
    assert response.status_code == (200 if change == "unchanged" else 409), response.text
    if change != "unchanged":
        assert scenario.rows() == before


@pytest.mark.parametrize("base", [None, 99])
async def test_legacy_baseline_is_readable_but_never_resigned(scenario, base):
    scenario.apply(write())
    scenario.card(proposal(base=base))
    before = scenario.rows()
    response = await scenario.confirm()
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "baseline_missing"
    assert scenario.rows() == before


async def test_reused_p01_cannot_confirm_the_unseen_new_draft(scenario):
    scenario.apply(write())
    scenario.card(proposal(), draft_id=DraftId("d-new"))
    before = scenario.rows()
    response = await scenario.confirm(("forged",))
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "draft_mismatch"
    assert scenario.rows() == before


async def test_empty_single_add_competitors_fail_as_a_set_but_each_is_legal(scenario):
    scenario.card(proposal(entry="a", text="P0", op=WriteOp.ADD, target=SINGLE, base=1),
                  proposal("p02", entry="b", text="P1", op=WriteOp.ADD, target=SINGLE, base=1),
                  authorize=True)
    # 新功能命名是独立授权；不能由预览或确认偷偷补。
    before = scenario.rows()
    response = await scenario.confirm(("p02", "p01"))
    assert response.status_code == 409, response.text
    assert response.json()["details"]["reason"] == "single_add_competition"
    assert scenario.rows() == before
    response = await scenario.confirm(("p02",))
    assert response.status_code == 200, response.text
    assert scenario.documents.get(scenario.project).locate_entry(EntryId("b")).entry.text == "P1"


@pytest.mark.parametrize("first,added,ok", [(WriteOp.REMOVE, "A", True),
    (WriteOp.REPLACE, "A", True), (WriteOp.REPLACE, "B", False), (None, " a \n", False)])
async def test_only_selected_add_final_duplicates_are_rejected(scenario, first, added, ok):
    scenario.apply(write())
    items = [] if first is None else [proposal(op=first)]
    items.append(proposal("p02", entry="new", text=added, op=WriteOp.ADD))
    scenario.card(*items)
    before = scenario.rows()
    response = await scenario.confirm(tuple(str(p.proposal_id) for p in items))
    assert response.status_code == (200 if ok else 409), response.text
    if not ok:
        assert response.json()["details"]["reason"] == "added_duplicate"
        assert response.json()["details"]["current_version_no"] == 2
        assert scenario.rows() == before


async def test_repeat_confirmation_keeps_original_terminal_state(scenario):
    scenario.apply(write())
    scenario.card(proposal())
    first = await scenario.confirm()
    assert first.status_code == 200, first.text
    before = scenario.rows()
    second = await scenario.confirm()
    assert second.status_code == 409, second.text
    assert scenario.rows() == before


@pytest.mark.parametrize("override", [False, True])
async def test_original_d5_requires_explicit_override_with_conflict_details(scenario, override):
    scenario.apply(replace(write(), manual=True))
    scenario.card(proposal(override=override))
    before = scenario.rows()
    response = await scenario.confirm()
    assert response.status_code == (200 if override else 409), response.text
    if not override:
        details = response.json()["details"]
        assert details["proposal_id"] == "p01"
        assert details["entry_id"] == "old"
        assert details["base_version_no"] == details["current_version_no"] == 2
        assert scenario.rows() == before


@pytest.mark.parametrize("failure", ["document", "version", "memory", "round"])
async def test_real_pg_confirm_failures_roll_back_without_success(scenario, failure):
    from pmstudio.domain.round.round import MemoryProposal
    from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
    from tests.support.doubles import RecordingPublisher

    scenario.apply(write())
    scenario.card(proposal())
    round_ = scenario.rounds.get(RID)
    round_.remember_memory_proposals((MemoryProposal("mem#1", MemoryChange(
        MemoryOp.ADD, MemoryKind.DECISION, "确认口径", ("E1",))),))
    from tests.support.leases import bind_round_lease

    bind_round_lease(scenario.connection, RID)
    scenario.rounds.save(round_)
    use_case = scenario.app.state.services.runner._confirm_draft
    committer = use_case._committer
    publisher = RecordingPublisher()
    use_case._events = publisher
    before = scenario.rows()

    class FailAfterVersion:
        def __init__(self, executor):
            self.connection = getattr(executor, "connection", executor)
            self.executor = executor

        def execute(self, query, params=None):
            result = self.executor.execute(query, params)
            if "INSERT INTO document_versions" in query:
                raise RuntimeError("version 保存后故障")
            return result

    if failure == "version":
        committer._writer = SqliteDocumentRepository(
            FailAfterVersion(committer._writer._connection)
        )
    else:
        writer = {"document": committer._writer, "memory": committer._memory_writer,
                  "round": committer._round_writer}[failure]
        save = writer.save

        def fail_after_save(item):
            save(item)
            raise RuntimeError(f"{failure} 保存后故障")

        writer.save = fail_after_save
    with pytest.raises(RuntimeError, match="保存后故障"):
        await use_case(round_id=RID, draft_id=str(DID), accepted=("p01",),
                       accepted_memory=("mem#1",))
    assert scenario.rows() == before
    assert publisher.published == []


@pytest.mark.parametrize("related", [False, True])
async def test_sim_http_rechecks_actual_input_k_before_persisting_real_evidence(scenario, related):
    from tests.application.test_sim_review_use_cases import PERSONAS, _all_good
    from tests.support.doubles import ScriptedModelGateway

    scenario.apply(write())  # 原候选 N=2
    flow = Target.body("s5", feature_slug="order-export", field="main_flow")
    acceptance = Target.body("s5", feature_slug="order-export", field="acceptance")
    scenario.apply(write("en-flow", "1. 进入订单列表\n2. 点导出\n3. 选时间范围", target=flow),
                   write("en-prio", "P0", target=SINGLE),
                   write("en-acc1", "5 万条内 60 秒可下载", target=acceptance),
                   write("en-acc2", "无权限不出现导出按钮", target=acceptance))  # 评测 K=3
    scenario.card(proposal())
    before_draft = scenario.rounds.get(RID).draft
    before_ledger = SqliteEvidenceRepository(scenario.connection).get(scenario.project).evidences()
    use_case = scenario.app.state.services.runner._run_sim_review
    use_case._simulator._model = ScriptedModelGateway(persona_replies=_all_good())
    run = use_case._simulator.run
    advanced = False

    async def concurrent_document_write(**kwargs):
        nonlocal advanced
        result = await run(**kwargs)
        if not advanced:
            advanced = True
            # 第二条真实连接提交，模型输入 Document 不被重盖章。
            with open_direct() as connection:
                repo = SqliteDocumentRepository(connection)
                current = repo.get(scenario.project)
                item = (write("en-flow", "已修改的主流程", op=WriteOp.REPLACE, target=flow)
                        if related else write("background", "新背景", target=Target.body("s3")))
                current.apply((item,), now=NOW, summary="评测期间并发提交")
                repo.save(current)
        return result

    use_case._simulator.run = concurrent_document_write
    async with async_client(scenario.app) as client:
        response = await client.post(f"/api/rounds/{RID}/sim-review", json={
            "persona_ids": list(PERSONAS), "feature_slugs": ["order-export"],
        })
    assert response.status_code == (409 if related else 200), response.text
    stored = scenario.rounds.get(RID)
    ledger = SqliteEvidenceRepository(scenario.connection).get(scenario.project)
    if related:
        assert response.json()["code"] == "proposal_stale"
        assert stored.draft == before_draft
        assert stored.sim_review_runs == 0
        assert ledger.evidences() == before_ledger
    else:
        assert stored.sim_review_runs == 1
        assert stored.draft.proposals[:-1] == before_draft.proposals
        sim = stored.draft.proposals[-1]
        assert sim.base_version_no == 3
        assert before_draft.proposals[0].base_version_no == 2
        assert scenario.documents.get(scenario.project).current_version.version_no == 4
        assert all(ledger.get(ref).kind is EvidenceKind.SIMULATED for ref in sim.origin.refs)
        confirmed = await scenario.confirm((str(sim.proposal_id),))
        assert confirmed.status_code == 200, confirmed.text
