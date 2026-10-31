import pytest

from tests.application.test_confirm_and_queries import CLAIMS_REPLY, DRAFT_REPLY, SEAT_REPLY
from tests.e2e.test_closed_loop import create_project, start_round
from tests.support.api_client import async_client
from tests.support.builders import SEAT


@pytest.mark.parametrize("draft_identity", ["missing", "wrong", "seen"])
async def test_http_pg_graph_confirm_requires_the_seen_card(make_app, draft_identity):
    app, _ = make_app(replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
                      seat_replies={SEAT: SEAT_REPLY})
    async with async_client(app) as client:
        project = await create_project(client)
        started = await start_round(client, project, user_input="想做个订单导出的功能")
        rid = started["round_id"]
        response = await client.post(f"/api/rounds/{rid}/opening", json={"seats": [SEAT]})
        assert response.status_code == 200, response.text
        card = response.json()["draft"]
        body = {"accepted": ["p01", "p01"]}
        if draft_identity != "missing":
            body["draft_id"] = card["draft_id"] if draft_identity == "seen" else "d-old"
        confirmed = await client.post(f"/api/rounds/{rid}/confirm", json=body)
        expected = {"missing": 422, "wrong": 409, "seen": 200}[draft_identity]
        assert confirmed.status_code == expected, confirmed.text
        workspace = (await client.get(f"/api/projects/{project}/workspace")).json()
        assert workspace["document"]["version_no"] == (2 if draft_identity == "seen" else 1)
        if draft_identity == "wrong":
            assert confirmed.json()["code"] == "draft_mismatch"
