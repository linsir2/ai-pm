"""联网申请的逐条决策（P3a B4）：批准即执行、拒绝不联网、错误码分档。

决策走 `RoundRunner`：404 = 申请不存在；409 = 已决策 / 轮终态 / 执行名额满
（以及 `RoundBusy`，那条映射已由 `test_routes.py` 钉住）。
"""

import asyncio

import pytest

from pmstudio.api.app import create_app
from pmstudio.application.errors import WebRequestNotPending
from pmstudio.application.use_cases import DecideWebRequest
from pmstudio.application.web_research import WebResearch
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.round.web import WebRequest, WebRequestState
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from tests.support.api_client import async_client
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim, make_web_packet
from tests.support.doubles import RecordingWebSearch
from tests.support.harness import Harness

QUERY = "RBAC 权限模型的主流做法"
DECISION_URL = f"/api/rounds/{ROUND_ID}/web-requests/{{request_id}}/decision"


def _request(
    request_id: str = "w1",
    *,
    query: str = QUERY,
    origin: str = "analyzer",
    state: WebRequestState = WebRequestState.PROPOSED,
) -> WebRequest:
    return WebRequest(
        request_id=request_id,
        query=query,
        why="要查行业通行做法",
        origin=origin,
        seat_id=SEAT if origin == "seat" else None,
        state=state,
    )


def _seed(harness: Harness, requests: list[WebRequest]) -> None:
    """一轮停在 claims_ready：申请都还是 proposed，可以直接走决策端点。"""

    harness.seed_project()
    round_ = Round.start(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.attach_web_requests(requests)
    harness.rounds.seed(round_)


def _app(harness: Harness):  # type: ignore[no-untyped-def]
    return create_app(harness.api_services(model_name="m"))


async def test_unknown_web_request_is_404():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w-nope"), json={"decision": "approve"}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_web_request"
    assert harness.web_search.calls == []


async def test_approving_executes_once_and_marks_done():
    harness = Harness(
        replies={},
        seat_replies={},
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert response.status_code == 200
    body = response.json()
    assert [(item["request_id"], item["state"]) for item in body["web_requests"]] == [
        ("w1", "done")
    ]
    assert harness.web_search.calls == [(QUERY, 5)]
    round_ = harness.rounds.get(ROUND_ID)
    assert len(round_.materials) == 1
    assert str(round_.materials[0].eid) == "E1"
    assert round_.web_requests[0].result_note == "搜到 1 条结果"
    assert harness.events.names.count("round.updated") == 1


async def test_rejecting_never_calls_the_network():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "reject"}
        )

    assert response.status_code == 200
    assert response.json()["web_requests"][0]["state"] == "rejected"
    assert harness.web_search.calls == []
    assert harness.rounds.get(ROUND_ID).materials == ()
    assert harness.events.names.count("round.updated") == 1


async def test_a_request_that_is_already_decided_is_409():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request(state=WebRequestState.REJECTED)])

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert response.status_code == 409
    assert response.json()["code"] == "web_request_not_pending"
    assert harness.web_search.calls == []


async def test_a_terminal_round_cannot_decide_even_a_proposed_request():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])
    round_ = harness.rounds.get(ROUND_ID)
    round_.abandon("不做了")
    harness.rounds.save(round_)

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert response.status_code == 409
    assert response.json()["code"] == "web_request_not_pending"
    assert harness.rounds.get(ROUND_ID).web_requests[0].state is WebRequestState.PROPOSED
    assert harness.web_search.calls == []


async def test_approval_beyond_the_execution_slots_is_409():
    harness = Harness(replies={}, seat_replies={})
    _seed(
        harness,
        [
            _request("w1", query="a", state=WebRequestState.DONE),
            _request("w2", query="b", state=WebRequestState.APPROVED),
            _request("w3", query="c", state=WebRequestState.FAILED),
            _request("w4", query="d", origin="seat"),
        ],
    )

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w4"), json={"decision": "approve"}
        )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "web_execution_limit_reached"
    assert body["details"]["limit"] == 3
    assert body["details"]["used"] == 3
    assert harness.rounds.get(ROUND_ID).web_requests[-1].state is WebRequestState.PROPOSED
    assert harness.web_search.calls == []


async def test_without_a_web_tool_approval_is_a_readable_409_and_schema_says_zero():
    harness = Harness(replies={}, seat_replies={}, available_tool_keys=frozenset())
    _seed(harness, [_request()])

    app = _app(harness)
    async with async_client(app) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )
        read_back = await client.get(f"/api/rounds/{ROUND_ID}")
        schema = await client.get("/api/schema")

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "web_execution_limit_reached"
    assert body["details"]["limit"] == 0
    assert "没有配好联网工具" in body["message"]
    assert read_back.json()["web_search_available"] is False
    assert schema.json()["limits"]["max_web_executions"] == 0
    assert harness.web_search.calls == []


async def test_a_tool_level_failure_lands_as_failed_with_note_and_drop():
    from pmstudio.harness.errors import ToolCallFailed

    harness = Harness(
        replies={},
        seat_replies={},
        web_search=RecordingWebSearch(
            failure=ToolCallFailed("联网搜索失败：HTTP 429", tool_id="tool.web_search", status=429)
        ),
    )
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert response.status_code == 200
    request = response.json()["web_requests"][0]
    assert request["state"] == "failed"
    assert "429" in request["result_note"]
    assert harness.rounds.get(ROUND_ID).materials == ()
    assert [item.source.value for item in harness.rounds.get(ROUND_ID).dropped] == ["tool"]
    assert harness.web_search.calls == [(QUERY, 5)]
    assert harness.events.names.count("round.updated") == 1


async def test_repeating_the_approval_never_executes_twice():
    harness = Harness(
        replies={},
        seat_replies={},
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        first = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )
        second = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "web_request_not_pending"
    assert harness.web_search.calls == [(QUERY, 5)]


async def test_an_invalid_decision_body_is_422_and_leaves_the_request_proposed():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "maybe"}
        )

    assert response.status_code == 422
    round_ = harness.rounds.get(ROUND_ID)
    assert round_.web_requests[0].state is WebRequestState.PROPOSED
    assert harness.web_search.calls == []


class BlockingWebSearch(RecordingWebSearch):
    """第一次检索卡住，用来把 decision 钉在"正在处理中"。"""

    def __init__(self) -> None:
        super().__init__(returns=(make_web_packet(),))
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def search(self, *, query: str, limit: int):  # type: ignore[no-untyped-def]
        self.calls.append((query, limit))
        self.entered.set()
        await self.release.wait()
        return self.returns


async def test_a_second_decision_on_the_same_round_is_409_round_busy():
    search = BlockingWebSearch()
    harness = Harness(replies={}, seat_replies={}, web_search=search)
    _seed(harness, [_request()])
    app = _app(harness)

    async with async_client(app) as client:
        first = asyncio.create_task(
            client.post(DECISION_URL.format(request_id="w1"), json={"decision": "approve"})
        )
        await asyncio.wait_for(search.entered.wait(), timeout=5)
        try:
            second = await client.post(
                DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
            )
        finally:
            search.release.set()
        first_response = await asyncio.wait_for(first, timeout=5)

    assert first_response.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "round_busy"
    assert harness.web_search.calls == [(QUERY, 5)]


async def test_a_non_tool_exception_is_500_failed_without_an_event_or_second_run():
    harness = Harness(
        replies={},
        seat_replies={},
        web_search=RecordingWebSearch(failure=RuntimeError("boom")),
    )
    _seed(harness, [_request()])

    async with async_client(_app(harness), raise_app_exceptions=False) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )
        read_back = await client.get(f"/api/rounds/{ROUND_ID}")
        second = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert response.status_code == 500
    request = read_back.json()["web_requests"][0]
    assert request["state"] == "failed"
    assert "boom" in request["result_note"]
    assert harness.web_search.calls == [(QUERY, 5)]
    # R17：非工具异常不补发 round.updated；客户端收到错误后自己 refetch
    assert harness.events.names.count("round.updated") == 0
    assert second.status_code == 409
    assert second.json()["code"] == "web_request_not_pending"
    assert harness.web_search.calls == [(QUERY, 5)]


async def test_cancelling_a_decision_fails_the_request_without_an_event():
    search = BlockingWebSearch()
    harness = Harness(replies={}, seat_replies={}, web_search=search)
    _seed(harness, [_request()])
    use_case = DecideWebRequest(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_research=harness.web_research(),
        uow=harness.uow,
        clock=harness.clock,
        events=harness.events,
    )

    task = asyncio.create_task(
        use_case(round_id=ROUND_ID, request_id="w1", decision="approve")
    )
    await asyncio.wait_for(search.entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    round_ = harness.rounds.get(ROUND_ID)
    assert round_.web_requests[0].state is WebRequestState.FAILED
    assert "执行中断" in (round_.web_requests[0].result_note or "")
    assert [item.source.value for item in round_.dropped] == ["tool"]
    assert harness.events.published == []
    assert search.calls == [(QUERY, 5)]

    with pytest.raises(WebRequestNotPending):
        await use_case(round_id=ROUND_ID, request_id="w1", decision="approve")
    assert search.calls == [(QUERY, 5)]


async def test_an_unknown_request_on_a_terminal_round_is_still_404():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])
    round_ = harness.rounds.get(ROUND_ID)
    round_.abandon("不做了")
    harness.rounds.save(round_)

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w-nope"), json={"decision": "approve"}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_web_request"


async def test_allow_web_defaults_to_false():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])

    async with async_client(_app(harness)) as client:
        read_back = await client.get(f"/api/rounds/{ROUND_ID}")

    assert read_back.json()["allow_web"] is False


async def test_decision_tool_drop_uses_the_current_seat_pass():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    round_ = Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="想做个订单导出的功能",
        created_at=NOW,
        step=Step.SEATS_RUNNING,
        waiting_on=WaitingOn.NONE,
        seats=(SEAT,),
        seat_round_count=2,
        web_requests=(_request(),),
    )
    harness.rounds.seed(round_)

    async with async_client(_app(harness)) as client:
        response = await client.post(
            DECISION_URL.format(request_id="w1"), json={"decision": "approve"}
        )

    assert response.status_code == 200
    dropped = harness.rounds.get(ROUND_ID).dropped
    assert [(item.source.value, item.pass_index) for item in dropped] == [("tool", 2)]


async def test_an_unwired_gateway_is_refused_before_the_claim():
    """目录说工具可用、实例却没接网关：装配错不能留下 approved 占名额。"""

    harness = Harness(replies={}, seat_replies={})
    _seed(harness, [_request()])
    research = WebResearch(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        materials=harness.materials,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_search=None,
        index=harness.material_index,
        uow=harness.uow,
        clock=harness.clock,
    )
    use_case = DecideWebRequest(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        tools=harness.tools,
        available_tool_keys=harness.available_tool_keys,
        web_research=research,
        uow=harness.uow,
        clock=harness.clock,
        events=harness.events,
    )

    with pytest.raises(ShapeError):
        await use_case(round_id=ROUND_ID, request_id="w1", decision="approve")

    round_ = harness.rounds.get(ROUND_ID)
    assert round_.web_requests[0].state is WebRequestState.PROPOSED
    assert round_.materials == ()
    assert harness.events.published == []
    assert harness.web_search.calls == []


RECOVERY_URL = f"/api/rounds/{ROUND_ID}/web-requests/{{request_id}}/recovery"


def _over_limit_requests() -> list[WebRequest]:
    return [
        _request("w1", query="a", state=WebRequestState.DONE),
        _request("w2", query="b", state=WebRequestState.DONE),
        _request("w3", query="c", state=WebRequestState.DONE),
        _request("w4", query="d", origin="seat", state=WebRequestState.APPROVED),
    ]


async def test_recovery_abandon_rejects_the_over_limit_approved_with_a_note():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, _over_limit_requests())

    async with async_client(_app(harness)) as client:
        response = await client.post(
            RECOVERY_URL.format(request_id="w4"), json={"action": "abandon"}
        )

    assert response.status_code == 200
    item = next(
        entry for entry in response.json()["web_requests"] if entry["request_id"] == "w4"
    )
    assert item["state"] == "rejected"
    assert "超额恢复" in item["result_note"]
    assert harness.web_search.calls == []
    assert harness.events.names.count("round.updated") == 1


async def test_recovery_execute_allows_one_temporary_overage():
    harness = Harness(
        replies={},
        seat_replies={},
        web_search=RecordingWebSearch(returns=(make_web_packet(url="https://recover"),)),
    )
    _seed(harness, _over_limit_requests())

    async with async_client(_app(harness)) as client:
        response = await client.post(
            RECOVERY_URL.format(request_id="w4"), json={"action": "execute"}
        )

    assert response.status_code == 200
    item = next(
        entry for entry in response.json()["web_requests"] if entry["request_id"] == "w4"
    )
    assert item["state"] == "done"
    assert harness.web_search.calls == [("d", 5)]
    # 执行不减少 used（approved → done 仍占名额），但这一条不再是超额 approved。
    assert len(harness.rounds.get(ROUND_ID).materials) == 1


async def test_recovery_refuses_an_approved_request_without_overage():
    harness = Harness(replies={}, seat_replies={})
    _seed(
        harness,
        [
            _request("w1", query="a", state=WebRequestState.DONE),
            _request("w2", query="b", state=WebRequestState.DONE),
            _request("w3", query="c", state=WebRequestState.APPROVED),
        ],
    )

    async with async_client(_app(harness)) as client:
        response = await client.post(
            RECOVERY_URL.format(request_id="w3"), json={"action": "execute"}
        )

    assert response.status_code == 409
    assert response.json()["code"] == "web_request_not_recoverable"
    assert harness.web_search.calls == []


async def test_recovery_refuses_proposed_and_terminal_requests():
    harness = Harness(replies={}, seat_replies={})
    _seed(
        harness,
        [
            _request("w1", query="a", state=WebRequestState.DONE),
            _request("w2", query="b", state=WebRequestState.DONE),
            _request("w3", query="c", state=WebRequestState.DONE),
            _request(
                "w4",
                query="d",
                origin="seat",
                state=WebRequestState.PROPOSED,
            ),
        ],
    )

    async with async_client(_app(harness)) as client:
        proposed = await client.post(
            RECOVERY_URL.format(request_id="w4"), json={"action": "abandon"}
        )

    assert proposed.status_code == 409
    assert proposed.json()["code"] == "web_request_not_recoverable"

    terminal_harness = Harness(replies={}, seat_replies={})
    _seed(terminal_harness, _over_limit_requests())
    round_ = terminal_harness.rounds.get(ROUND_ID)
    round_.abandon("不做了")
    terminal_harness.rounds.save(round_)

    async with async_client(_app(terminal_harness)) as client:
        terminal = await client.post(
            RECOVERY_URL.format(request_id="w4"), json={"action": "abandon"}
        )

    assert terminal.status_code == 409
    assert terminal.json()["code"] == "web_request_not_recoverable"


async def test_recovery_unknown_request_is_404_and_bad_action_is_422():
    harness = Harness(replies={}, seat_replies={})
    _seed(harness, _over_limit_requests())

    async with async_client(_app(harness)) as client:
        unknown = await client.post(
            RECOVERY_URL.format(request_id="w-nope"), json={"action": "abandon"}
        )
        bad_action = await client.post(
            RECOVERY_URL.format(request_id="w4"), json={"action": "retry"}
        )

    assert unknown.status_code == 404
    assert unknown.json()["code"] == "unknown_web_request"
    assert bad_action.status_code == 422
    assert harness.web_search.calls == []
