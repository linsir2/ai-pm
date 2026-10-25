"""联网申请的逐条决策（P3a B4）：批准即执行、拒绝不联网、错误码分档。

决策走 `RoundRunner`：404 = 申请不存在；409 = 已决策 / 轮终态 / 执行名额满
（以及 `RoundBusy`，那条映射已由 `test_routes.py` 钉住）。
"""

from pmstudio.api.app import create_app
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.web import WebRequest, WebRequestState
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
