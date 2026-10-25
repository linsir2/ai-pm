"""B3 真 SQLite 端到端：opening 执行一次、下一步自动批准并执行、编号复用。

工具侧是 `RecordingWebSearch`（本环境没有 Tavily key；真网关会出网），
模型侧照 e2e 惯例脚本化；材料、账本、轮次、事务全部是真的。
"""

import json

from pmstudio.application.use_cases import (
    IntegrateRound,
    RunSeatRound,
    StartRound,
    SubmitOpening,
)
from pmstudio.application.web_research import WebResearch
from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.round.web import WebRequestState
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.shared.template import standard_template
from pmstudio.infrastructure.sqlite.database import connect
from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.sqlite.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.sqlite.materials import SqliteMaterialStore
from pmstudio.infrastructure.sqlite.observations_repo import SqliteObservationRepository
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.support.builders import NOW, PROJECT, SEAT, make_web_packet
from tests.support.doubles import FixedClock, RecordingPublisher, RecordingWebSearch
from tests.support.harness import Harness

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


async def test_real_sqlite_executes_across_steps_and_reuses_the_shared_result_number(
    tmp_path,
):
    connection = connect(tmp_path / "web_research_e2e.db")
    ids = UuidIdGenerator()
    store = SqliteMaterialStore(connection, ids=ids)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    memory = SqliteMemoryRepository(connection)
    index = SqliteWorkspaceIndex(connection)
    uow = SqliteUnitOfWork(connection)
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
    )
    round_id = await start.create(
        project_id=PROJECT, user_input="想做个订单导出的功能"
    )
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
