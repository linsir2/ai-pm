"""真 PostgreSQL：P3b 五态 claim/used、opening、逐条恢复、SkipSeat 行锁。

这些用例走真实用例 + PG 仓储 + `PgLeaseStore`；模型/联网是测试替身，
但数据库事务、`FOR UPDATE`、`round_skips` upsert 与租约 fencing 都是真的。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass

import pytest

from pmstudio.api.errors import status_for
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.errors import (
    SeatAlreadyReviewed,
    UnknownWebRequest,
    WebExecutionLimitReached,
    WebRequestNotRecoverable,
    WebRequestRecoveryRequired,
)
from pmstudio.application.lease import LeaseScope, clear_lease_scope, set_lease_scope
from pmstudio.application.use_cases import (
    RECOVERY_ABANDON_NOTE,
    DecideWebRequest,
    GetRound,
    RecoverWebRequest,
    RunSeatRound,
    SkipSeat,
    SubmitOpening,
)
from pmstudio.application.web_research import (
    WebResearch,
    claim_web_request,
    web_requests_used,
)
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step
from pmstudio.domain.round.web import WebRequest, WebRequestState
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import DocumentId, EvidenceId, RoundId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.lease import LeaseKind
from pmstudio.domain.shared.target import Target
from pmstudio.harness.tools.registry import DEFINITIONS_DIR, TomlToolCatalog
from pmstudio.infrastructure.postgres.database import connect
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from pmstudio.infrastructure.postgres.owner_scope import (
    clear_owner_scope,
    set_owner_scope,
)
from pmstudio.infrastructure.postgres.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from tests.conftest import TEST_OWNER
from tests.support.builders import NOW, PROJECT, SEAT, make_seat, make_web_packet
from tests.support.doubles import (
    FixedClock,
    RecordingPrompts,
    RecordingPublisher,
    RecordingWebSearch,
    ScriptedModelGateway,
    StaticSeatCatalog,
)
from tests.support.fakes import InMemoryMaterialStore
from tests.support.postgres import PgTestDatabase, truncate_data_tables

OWNER = TEST_OWNER
ROUND = RoundId("r-claim")
TTL_MS = 60_000
TOOL_KEYS = frozenset({"tavily_api_key"})
SEAT_REPLY = (
    '{"basis":"读了 E1","opinions":[{"local_id":"O1","form":"judgment",'
    '"about":"权限","claim":"无权限不显示入口","because":"订单含客户信息",'
    '"evidence_refs":["E1"]}]}'
)


@dataclass(frozen=True, slots=True)
class _Database:
    pg: PgTestDatabase


@pytest.fixture(scope="module")
def database(pg_test_database: PgTestDatabase) -> _Database:
    return _Database(pg_test_database)


@pytest.fixture()
def conn(database: _Database) -> Iterator[object]:
    truncate_data_tables(database.pg)
    with connect(database.pg.direct_url) as connection:
        yield connection
    truncate_data_tables(database.pg)


def _second(database: _Database):
    return connect(database.pg.direct_url)


def _bind(connection, round_id: RoundId = ROUND, *, holder: str = "test-holder"):  # type: ignore[no-untyped-def]
    token = PgLeaseStore(connection).acquire(
        LeaseKind.ROUND, str(round_id), OWNER, holder, TTL_MS
    )
    assert token is not None
    scope = LeaseScope(holder=holder, owner=OWNER)
    scope.bind(token)
    set_lease_scope(scope)
    return token


def _unbind() -> None:
    clear_lease_scope()


def _claim() -> Claim:
    return Claim(
        claim_id="c1",
        text="要做一个订单导出功能",
        label=ClaimLabel.NEED,
        target=Target.body("s5"),
        raw_span="想做个订单导出的功能",
        eid=EvidenceId("E1"),
    )


def _web_request(request_id: str, *, origin: str = "analyzer") -> WebRequest:
    return WebRequest(
        request_id=request_id,
        query=f"query-{request_id}",
        why="需要行业做法",
        origin=origin,  # type: ignore[arg-type]
        seat_id=SEAT if origin == "seat" else None,
        pass_index=1 if origin == "seat" else None,
    )


def _seed_round(
    connection,
    requests: tuple[tuple[WebRequest, WebRequestState], ...],
    *,
    round_id: RoundId = ROUND,
) -> Round:
    round_ = Round.start(
        round_id=round_id,
        project_id=PROJECT,
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    round_.attach_claims((_claim(),))
    round_.attach_web_requests([item for item, _state in requests])
    for item, state in requests:
        if state is WebRequestState.PROPOSED:
            continue
        round_.decide_web_request(
            item.request_id, approve=state is not WebRequestState.REJECTED
        )
        if state in (WebRequestState.DONE, WebRequestState.FAILED):
            round_.mark_web_request(item.request_id, state, result_note="已记结果")
    SqliteRoundRepository(connection).save(round_)
    return round_


def _seed_project(connection) -> None:
    document = Document.new(
        document_id=DocumentId("doc-claim"),
        project_id=PROJECT,
        title="订单中台",
        now=NOW,
    )
    SqliteDocumentRepository(connection).save(document)
    SqliteEvidenceRepository(connection).save(EvidenceLedger.empty(PROJECT))


def _web(connection, web_search: RecordingWebSearch) -> WebResearch:  # type: ignore[no-untyped-def]
    rounds = SqliteRoundRepository(connection)
    evidence = SqliteEvidenceRepository(connection)
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    return WebResearch(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidence,
        evidence_writer=evidence,
        materials=InMemoryMaterialStore(),
        tools=tools,
        available_tool_keys=TOOL_KEYS,
        web_search=web_search,
        index=SqliteWorkspaceIndex(connection),
        uow=PgUnitOfWork(connection),
        clock=FixedClock(),
    )


def _opening(
    connection, web_search: RecordingWebSearch, *, tool_keys: frozenset[str] = TOOL_KEYS
) -> SubmitOpening:
    rounds = SqliteRoundRepository(connection)
    evidence = SqliteEvidenceRepository(connection)
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    return SubmitOpening(
        rounds=rounds,
        round_writer=rounds,
        catalog=StaticSeatCatalog((make_seat(),)),
        tools=tools,
        available_tool_keys=tool_keys,
        web_search=web_search,
        evidence=evidence,
        evidence_writer=evidence,
        materials=InMemoryMaterialStore(),
        index=SqliteWorkspaceIndex(connection),
        uow=PgUnitOfWork(connection),
        clock=FixedClock(),
        events=RecordingPublisher(),
        web_research=_web(connection, web_search),
    )


def _recover(connection, web_search: RecordingWebSearch) -> RecoverWebRequest:
    rounds = SqliteRoundRepository(connection)
    evidence = SqliteEvidenceRepository(connection)
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    return RecoverWebRequest(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidence,
        evidence_writer=evidence,
        tools=tools,
        available_tool_keys=TOOL_KEYS,
        web_research=_web(connection, web_search),
        uow=PgUnitOfWork(connection),
        clock=FixedClock(),
        events=RecordingPublisher(),
    )


def _states(connection, round_id: RoundId = ROUND) -> dict[str, WebRequestState]:
    round_ = SqliteRoundRepository(connection).get(round_id)
    return {item.request_id: item.state for item in round_.web_requests}


def _used(connection, round_id: RoundId = ROUND) -> int:
    return web_requests_used(SqliteRoundRepository(connection).get(round_id))


def test_five_state_claim_and_used_are_recomputed_in_the_same_uow(
    conn, database: _Database
) -> None:
    requests = (
        (_web_request("w1"), WebRequestState.DONE),
        (_web_request("w2"), WebRequestState.FAILED),
        (_web_request("w3", origin="seat"), WebRequestState.REJECTED),
        (_web_request("w4", origin="seat"), WebRequestState.PROPOSED),
        (_web_request("w5", origin="seat"), WebRequestState.PROPOSED),
    )
    _seed_round(conn, requests)
    assert _used(conn) == 2

    token = _bind(conn)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def run(request_id: str) -> None:
        with _second(database) as other:
            set_owner_scope(OWNER)
            scope = LeaseScope(holder=token.holder, owner=OWNER)
            scope.bind(token)
            context = set_lease_scope(scope)
            try:
                barrier.wait(timeout=5)
                claim_web_request(
                    rounds=SqliteRoundRepository(other),
                    round_writer=SqliteRoundRepository(other),
                    uow=PgUnitOfWork(other),
                    round_id=ROUND,
                    request_id=request_id,
                    limit=3,
                )
                results.append("ok")
            except WebExecutionLimitReached:
                results.append("limit")
            finally:
                clear_lease_scope(context)
                clear_owner_scope()

    workers = [
        threading.Thread(target=run, args=("w4",)),
        threading.Thread(target=run, args=("w5",)),
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert sorted(results) == ["limit", "ok"]
    states = _states(conn)
    assert states["w1"] is WebRequestState.DONE
    assert states["w2"] is WebRequestState.FAILED
    assert states["w3"] is WebRequestState.REJECTED
    assert sorted((states["w4"].value, states["w5"].value)) == ["approved", "proposed"]
    assert list(states.values()).count(WebRequestState.APPROVED) == 1
    assert _used(conn) == 3
    _unbind()


async def test_opening_over_limit_requires_recovery_before_any_network(conn) -> None:
    _seed_project(conn)
    _seed_round(
        conn,
        (
            (_web_request("w1"), WebRequestState.DONE),
            (_web_request("w2"), WebRequestState.DONE),
            (_web_request("w3"), WebRequestState.DONE),
            (_web_request("w4", origin="seat"), WebRequestState.APPROVED),
        ),
    )
    _bind(conn)
    web_search = RecordingWebSearch(returns=(make_web_packet(),))
    try:
        with pytest.raises(WebRequestRecoveryRequired) as error:
            await _opening(conn, web_search)(round_id=ROUND, seats=(SEAT,))
        assert status_for(error.value) == 409
        assert error.value.details["used"] == 4
        assert error.value.details["limit"] == 3
        assert web_search.calls == []
        assert SqliteRoundRepository(conn).get(ROUND).step is Step.CLAIMS_READY
    finally:
        _unbind()


async def test_opening_refuses_new_approval_at_the_limit_before_any_network(conn) -> None:
    _seed_project(conn)
    _seed_round(
        conn,
        (
            (_web_request("w1"), WebRequestState.DONE),
            (_web_request("w2"), WebRequestState.DONE),
            (_web_request("w3"), WebRequestState.DONE),
            (_web_request("w4", origin="seat"), WebRequestState.PROPOSED),
        ),
    )
    _bind(conn)
    web_search = RecordingWebSearch(returns=(make_web_packet(),))
    try:
        with pytest.raises(ShapeError) as error:
            await _opening(conn, web_search)(
                round_id=ROUND, seats=(SEAT,), approved_request_ids=("w4",)
            )
        assert status_for(error.value) == 422
        assert "先取消新勾选" in error.value.message
        assert web_search.calls == []
        assert _states(conn)["w4"] is WebRequestState.PROPOSED
    finally:
        _unbind()


async def test_opening_with_a_stale_done_id_and_no_tool_runs_zero_web(conn) -> None:
    _seed_project(conn)
    _seed_round(conn, ((_web_request("w1"), WebRequestState.DONE),))
    _bind(conn)
    web_search = RecordingWebSearch(returns=(make_web_packet(),))
    try:
        await _opening(conn, web_search, tool_keys=frozenset())(
            round_id=ROUND, seats=(SEAT,), approved_request_ids=("w1",)
        )
        assert web_search.calls == []
        round_ = SqliteRoundRepository(conn).get(ROUND)
        assert round_.step is Step.SEATS_RUNNING
        assert _states(conn)["w1"] is WebRequestState.DONE
    finally:
        _unbind()


async def test_decision_on_a_stale_request_id_is_404_and_changes_nothing(conn) -> None:
    _seed_project(conn)
    _seed_round(conn, ((_web_request("w1"), WebRequestState.PROPOSED),))
    web_search = RecordingWebSearch()
    decision = DecideWebRequest(
        rounds=SqliteRoundRepository(conn),
        round_writer=SqliteRoundRepository(conn),
        evidence=SqliteEvidenceRepository(conn),
        evidence_writer=SqliteEvidenceRepository(conn),
        tools=TomlToolCatalog(DEFINITIONS_DIR),
        available_tool_keys=TOOL_KEYS,
        web_research=_web(conn, web_search),
        uow=PgUnitOfWork(conn),
        clock=FixedClock(),
        events=RecordingPublisher(),
    )
    with pytest.raises(UnknownWebRequest) as error:
        await decision(round_id=ROUND, request_id="w-stale", decision="approve")
    assert status_for(error.value) == 404
    assert web_search.calls == []
    assert _states(conn)["w1"] is WebRequestState.PROPOSED


async def test_recovery_abandon_turns_approved_into_rejected_and_frees_the_round(conn) -> None:
    _seed_project(conn)
    _seed_round(
        conn,
        (
            (_web_request("w1"), WebRequestState.DONE),
            (_web_request("w2"), WebRequestState.DONE),
            (_web_request("w3"), WebRequestState.DONE),
            (_web_request("w4", origin="seat"), WebRequestState.APPROVED),
        ),
    )
    _bind(conn)
    web_search = RecordingWebSearch(returns=(make_web_packet(),))
    try:
        await _recover(conn, web_search)(
            round_id=ROUND, request_id="w4", action="abandon"
        )
        assert web_search.calls == []
        states = _states(conn)
        assert states["w4"] is WebRequestState.REJECTED
        assert _used(conn) == 3
        recovered = next(
            item
            for item in SqliteRoundRepository(conn).get(ROUND).web_requests
            if item.request_id == "w4"
        )
        assert recovered.result_note == RECOVERY_ABANDON_NOTE

        await _opening(conn, web_search)(round_id=ROUND, seats=(SEAT,))
        assert web_search.calls == []
        assert SqliteRoundRepository(conn).get(ROUND).step is Step.SEATS_RUNNING
    finally:
        _unbind()


async def test_recovery_execute_allows_one_temporary_overage_then_zero_web(conn) -> None:
    _seed_project(conn)
    _seed_round(
        conn,
        (
            (_web_request("w1"), WebRequestState.DONE),
            (_web_request("w2"), WebRequestState.DONE),
            (_web_request("w3"), WebRequestState.DONE),
            (_web_request("w4", origin="seat"), WebRequestState.APPROVED),
        ),
    )
    _bind(conn)
    web_search = RecordingWebSearch(returns=(make_web_packet(),))
    try:
        await _recover(conn, web_search)(
            round_id=ROUND, request_id="w4", action="execute"
        )
        assert web_search.calls == [("query-w4", 5)]
        assert _states(conn)["w4"] is WebRequestState.DONE
        assert _used(conn) == 4

        await _opening(conn, web_search)(round_id=ROUND, seats=(SEAT,))
        assert web_search.calls == [("query-w4", 5)]
        assert SqliteRoundRepository(conn).get(ROUND).step is Step.SEATS_RUNNING
    finally:
        _unbind()


async def test_recovery_rejects_a_request_that_is_not_approved_or_not_over_limit(conn) -> None:
    _seed_project(conn)
    _seed_round(
        conn,
        (
            (_web_request("w1"), WebRequestState.DONE),
            (_web_request("w2"), WebRequestState.DONE),
            (_web_request("w3"), WebRequestState.PROPOSED),
        ),
    )
    _bind(conn)
    try:
        with pytest.raises(WebRequestNotRecoverable):
            await _recover(conn, RecordingWebSearch())(
                round_id=ROUND, request_id="w3", action="execute"
            )
    finally:
        _unbind()


def test_skip_seat_and_review_lock_the_same_round_row(conn, database: _Database) -> None:
    _seed_project(conn)
    round_ = Round.start(
        round_id=ROUND,
        project_id=PROJECT,
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    round_.attach_claims((_claim(),))
    round_.submit_opening(seats=(SEAT,))
    SqliteRoundRepository(conn).save(round_)

    token = _bind(conn)
    barrier = threading.Barrier(2)
    results: list[str] = []

    def run_skip() -> None:
        with _second(database) as other:
            set_owner_scope(OWNER)
            scope = LeaseScope(holder=token.holder, owner=OWNER)
            scope.bind(token)
            context = set_lease_scope(scope)
            try:
                barrier.wait(timeout=5)
                rounds = SqliteRoundRepository(other)
                evidence = SqliteEvidenceRepository(other)
                skip = SkipSeat(
                    rounds=rounds,
                    skips=rounds,
                    uow=PgUnitOfWork(other),
                    events=RecordingPublisher(),
                    clock=FixedClock(),
                    viewer=GetRound(
                        rounds=rounds,
                        evidence=evidence,
                        catalog=StaticSeatCatalog((make_seat(),)),
                        tools=TomlToolCatalog(DEFINITIONS_DIR),
                        available_tool_keys=TOOL_KEYS,
                        materials=InMemoryMaterialStore(),
                    ),
                )
                asyncio.run(skip(round_id=ROUND, seat_id=SEAT, owner=OWNER))
                results.append("skip")
            except SeatAlreadyReviewed:
                results.append("skip_rejected")
            finally:
                clear_lease_scope(context)
                clear_owner_scope()

    def run_review() -> None:
        with _second(database) as other:
            set_owner_scope(OWNER)
            scope = LeaseScope(holder=token.holder, owner=OWNER)
            scope.bind(token)
            context = set_lease_scope(scope)
            try:
                barrier.wait(timeout=5)
                rounds = SqliteRoundRepository(other)
                runner = RunSeatRound(
                    rounds=rounds,
                    round_writer=rounds,
                    documents=None,
                    evidence=None,
                    evidence_writer=None,
                    uow=PgUnitOfWork(other),
                    reviewer=SeatReviewer(
                        model=ScriptedModelGateway(seat_replies={SEAT: SEAT_REPLY}),
                        prompts=RecordingPrompts(),
                    ),
                    catalog=None,
                    assigner=None,
                    checker=None,
                    clock=FixedClock(),
                    events=RecordingPublisher(),
                    materials=None,
                    memory=None,
                    leases=PgLeaseStore(other),
                )
                asyncio.run(
                    runner._review_one(
                        round_id=ROUND,
                        seat=make_seat(),
                        document_state="",
                        claims="",
                        opinions_so_far=(),
                        answered="",
                        ledger=EvidenceLedger.empty(PROJECT),
                        project_memory="",
                        known_eids=frozenset({EvidenceId("E1")}),
                        pass_index=1,
                    )
                )
                results.append("review")
            finally:
                clear_lease_scope(context)
                clear_owner_scope()

    workers = [threading.Thread(target=run_skip), threading.Thread(target=run_review)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert set(results) in ({"skip", "review"}, {"review", "skip_rejected"})
    final = SqliteRoundRepository(conn).get(ROUND)
    if "skip" in results:
        assert final.skipped_seats == (SEAT,)
        assert final.current_reviews == ()
        row = conn.execute(
            "SELECT seat_id, pass_index FROM round_skips WHERE round_id = %s",
            (str(ROUND),),
        ).fetchall()
        assert row == [{"seat_id": SEAT, "pass_index": 1}]
    else:
        assert final.skipped_seats == ()
        assert [item.seat_id for item in final.current_reviews] == [SEAT]
    _unbind()
