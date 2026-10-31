"""B3 真 PG 端到端：opening 执行一次、下一步自动批准并执行、编号复用。

工具侧是 `RecordingWebSearch`（本环境没有 Tavily key；真网关会出网），
模型侧照 e2e 惯例脚本化；材料、账本、轮次、事务全部是真的。
"""

import json

import pytest

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.errors import WebRequestRecoveryRequired
from pmstudio.application.use_cases import (
    RECOVERY_ABANDON_NOTE,
    IntegrateRound,
    RecoverWebRequest,
    RunSeatRound,
    StartRound,
    SubmitOpening,
)
from pmstudio.application.web_research import WebResearch
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.round.step import Step
from pmstudio.domain.round.web import WebRequest, WebRequestState, used_web_request_count
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.template import standard_template
from pmstudio.harness.tools.registry import DEFINITIONS_DIR, TomlToolCatalog
from pmstudio.infrastructure.postgres.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.postgres.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.materials import SqliteMaterialStore
from pmstudio.infrastructure.postgres.observations_repo import SqliteObservationRepository
from pmstudio.infrastructure.postgres.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.support.builders import NOW, PROJECT, SEAT, make_seat, make_web_packet
from tests.support.doubles import (
    FixedClock,
    RecordingPrompts,
    RecordingPublisher,
    RecordingWebSearch,
    ScriptedModelGateway,
    StaticSeatCatalog,
)
from tests.support.harness import Harness
from tests.support.leases import TTL_MS, prepare_lease_scope
from tests.support.postgres import open_direct

QUERY_A = "RBAC 权限模型的主流做法"
QUERY_B = "多租户隔离怎么做"

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量"}],
        "web_queries": [{"query": QUERY_A, "why": "要查行业做法"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了检索结果",
        "opinions": [],
        "web_requests": [{"query": QUERY_B, "why": "席位要查多租户隔离"}],
    },
    ensure_ascii=False,
)

INTEGRATION_REPLY = json.dumps(
    {
        "basis": "读了主张、材料与席位意见",
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "entry_id": None,
                "target": {"zone": "body", "section_id": "s1"},
                "text": "订单导出用于把订单批量导出成表格。",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "来自用户说的导出需求",
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

MEMORY_REPLY = json.dumps({"changes": []}, ensure_ascii=False)


async def test_real_pg_executes_across_steps_and_reuses_the_shared_result_number():
    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    memory = SqliteMemoryRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = PgUnitOfWork(connection)
    leases = PgLeaseStore(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="联网执行 e2e", now=NOW, template=standard_template()
    )
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": INTEGRATION_REPLY},
        seat_replies={SEAT: SEAT_REPLY},
        web_search=RecordingWebSearch(
            returns_by_query={
                QUERY_A: (make_web_packet(url="https://shared"),),
                QUERY_B: (
                    make_web_packet(url="https://shared"),
                    make_web_packet(url="https://second"),
                ),
            }
        ),
    )
    clock = FixedClock()
    events = RecordingPublisher()
    web_research = WebResearch(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_search=harness.web_search,
        index=index,
        uow=uow,
        clock=clock,
    )

    start = StartRound(
        documents=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        analyzer=harness.analyzer,
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=clock,
        ids=ids,
        events=events,
        materials=store,
        explorations=SqliteExplorationRepository(connection),
        discussions=SqliteDiscussionRepository(connection),
        leases=leases,
        lease_ttl_ms=TTL_MS,
    )
    scope = prepare_lease_scope()
    round_id = await start.create(
        project_id=PROJECT, user_input="想做个订单导出的功能"
    )
    token = scope.ensure_held()
    assert token.target_id == str(round_id)
    assert leases.holds(token)
    await start.analyze(round_id=round_id)
    opening_request = rounds.get(round_id).web_requests[0]

    opening = SubmitOpening(
        rounds=rounds,
        round_writer=rounds,
        catalog=harness.seats,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_search=harness.web_search,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        index=index,
        uow=uow,
        clock=clock,
        events=events,
        web_research=web_research,
    )
    await opening(
        round_id=round_id,
        seats=(SEAT,),
        allow_web=True,
        approved_request_ids=(opening_request.request_id,),
    )

    seat_round = RunSeatRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        reviewer=harness.reviewer,
        catalog=harness.seats,
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=clock,
        events=events,
        materials=store,
        memory=memory,
        observations_repo=SqliteObservationRepository(connection),
        web_research=web_research,
    )
    await seat_round(round_id=round_id)

    integrate = IntegrateRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        integrator=harness.integrator,
        catalog=harness.seats,
        index=index,
        checker=ReadinessChecker(),
        clock=clock,
        ids=ids,
        events=events,
        materials=store,
        curator=harness.curator,
        memory=memory,
        observations_repo=SqliteObservationRepository(connection),
        digest_repo=SqliteDigestRepository(connection),
        inspiration_repo=SqliteInspirationRepository(connection),
        web_research=web_research,
    )
    await integrate(round_id=round_id)

    final = rounds.get(round_id)
    states = {item.query: item.state for item in final.web_requests}
    assert states == {QUERY_A: WebRequestState.DONE, QUERY_B: WebRequestState.DONE}
    assert [query for query, _ in harness.web_search.calls] == [QUERY_A, QUERY_B]

    by_material: dict[str, set[str]] = {}
    for ref in final.materials:
        by_material.setdefault(ref.material_id, set()).add(str(ref.eid))
    assert len(by_material) == 2, "shared + second 两条结果"
    assert all(len(eids) == 1 for eids in by_material.values()), by_material

    externals = [
        item
        for item in evidences.get(PROJECT).evidences()
        if item.kind.value == "external"
    ]
    assert len(externals) == 2, "同一条 shared 结果跨两步只发一个号"
    assert final.web_selection == tuple(
        (ref.material_id, ref.chunk_id) for ref in final.materials
    )
    connection.close()


RECOVERY_CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量"}],
        "web_queries": [],
    },
    ensure_ascii=False,
)


def _seed_over_limit(rounds, round_id):  # type: ignore[no-untyped-def]
    """真库上 seed `3 done + 1 approved`：used=4 > limit=3。"""

    round_ = rounds.get(round_id)
    requests = [
        WebRequest(
            request_id=f"w{200 + index}",
            query=f"q{index + 1}",
            why="超额恢复 e2e",
            origin="analyzer" if index < 3 else "seat",
            seat_id=SEAT if index >= 3 else None,
            pass_index=1 if index >= 3 else None,
        )
        for index in range(4)
    ]
    round_.attach_web_requests(requests)
    for item in requests[:3]:
        round_.decide_web_request(item.request_id, approve=True)
        round_.mark_web_request(item.request_id, WebRequestState.DONE, result_note="已查")
    round_.decide_web_request(requests[3].request_id, approve=True)
    rounds.save(round_)
    return requests[3].request_id


async def _prepare_over_limit_round(*, web_search):
    """真 PG + 真用例；只有模型与 Tavily 是脚本化替身（不用内存 Harness）。"""

    connection = open_direct()
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = PgUnitOfWork(connection)
    leases = PgLeaseStore(connection)
    SqliteProjectFactory(connection, ids=ids).create(
        project_id=PROJECT, title="恢复 e2e", now=NOW, template=standard_template()
    )
    catalog = StaticSeatCatalog((make_seat(),))
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    keys = frozenset({"tavily_api_key"})
    clock = FixedClock()
    events = RecordingPublisher()
    web_research = WebResearch(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        tools=tools,
        available_tool_keys=keys,
        web_search=web_search,
        index=index,
        uow=uow,
        clock=clock,
    )
    start = StartRound(
        documents=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        analyzer=ClaimAnalyzer(
            model=ScriptedModelGateway(replies={"claims": RECOVERY_CLAIMS_REPLY}),
            prompts=RecordingPrompts(),
            catalog=catalog,
        ),
        assigner=EvidenceAssigner(),
        checker=ReadinessChecker(),
        clock=clock,
        ids=ids,
        events=events,
        materials=store,
        explorations=SqliteExplorationRepository(connection),
        discussions=SqliteDiscussionRepository(connection),
        leases=leases,
        lease_ttl_ms=TTL_MS,
    )
    scope = prepare_lease_scope()
    round_id = await start.create(
        project_id=PROJECT, user_input="想做个订单导出的功能"
    )
    token = scope.ensure_held()
    assert token.target_id == str(round_id)
    assert leases.holds(token)
    await start.analyze(round_id=round_id)
    over_id = _seed_over_limit(rounds, round_id)
    opening = SubmitOpening(
        rounds=rounds,
        round_writer=rounds,
        catalog=catalog,
        tools=tools,
        available_tool_keys=keys,
        web_search=web_search,
        evidence=evidences,
        evidence_writer=evidences,
        materials=store,
        index=index,
        uow=uow,
        clock=clock,
        events=events,
        web_research=web_research,
    )
    recover = RecoverWebRequest(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        tools=tools,
        available_tool_keys=keys,
        web_research=web_research,
        uow=uow,
        clock=clock,
        events=events,
    )
    return connection, round_id, over_id, opening, recover, rounds, web_search


async def test_true_pg_recovery_abandon_then_opening_succeeds():
    web_search = RecordingWebSearch(returns=(make_web_packet(),))
    connection, round_id, over_id, opening, recover, rounds, web_search = (
        await _prepare_over_limit_round(web_search=web_search)
    )

    with pytest.raises(WebRequestRecoveryRequired):
        await opening(round_id=round_id, seats=(SEAT,))

    await recover(round_id=round_id, request_id=over_id, action="abandon")

    recovered = next(
        item for item in rounds.get(round_id).web_requests if item.request_id == over_id
    )
    assert recovered.state is WebRequestState.REJECTED
    assert recovered.result_note == RECOVERY_ABANDON_NOTE
    assert used_web_request_count(rounds.get(round_id).web_requests) == 3

    await opening(round_id=round_id, seats=(SEAT,))

    final = rounds.get(round_id)
    assert final.step is Step.SEATS_RUNNING
    assert web_search.calls == []
    connection.close()


async def test_true_pg_recovery_execute_once_then_opening_is_zero_web():
    web_search = RecordingWebSearch(returns=(make_web_packet(url="https://recovered"),))
    connection, round_id, over_id, opening, recover, rounds, web_search = (
        await _prepare_over_limit_round(web_search=web_search)
    )

    with pytest.raises(WebRequestRecoveryRequired):
        await opening(round_id=round_id, seats=(SEAT,))

    await recover(round_id=round_id, request_id=over_id, action="execute")

    executed = next(
        item for item in rounds.get(round_id).web_requests if item.request_id == over_id
    )
    assert executed.state is WebRequestState.DONE
    assert [query for query, _ in web_search.calls] == ["q4"]
    assert used_web_request_count(rounds.get(round_id).web_requests) == 4

    await opening(round_id=round_id, seats=(SEAT,))

    final = rounds.get(round_id)
    assert final.step is Step.SEATS_RUNNING
    assert [query for query, _ in web_search.calls] == ["q4"]
    connection.close()
