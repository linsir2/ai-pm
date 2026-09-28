"""放弃这一轮的端点（`01` F15）：先写状态、再取消正在跑的调用。"""

import json

import pytest

from pmstudio.api.app import create_app
from tests.support.api_client import async_client
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "导出要快",
                "raw_span": "导出要快一点",
                "label": "constraint",
                "zone": "body",
                "section_id": "s8",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)


@pytest.fixture()
def app():
    harness = Harness(replies={"claims": CLAIMS_REPLY}, seat_replies={})
    harness.seed_project()
    return create_app(harness.api_services(model_name="m"))


async def test_abandon_endpoint_marks_the_round(app):
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "导出要快一点"}
        )
        round_id = started.json()["round_id"]

        response = await client.post(f"/api/rounds/{round_id}/abandon")

    assert response.status_code == 200
    body = response.json()
    assert (body["step"], body["waiting_on"]) == ("abandoned", "none")
    assert body["abandon_reason"] == "用户放弃了这一轮"


async def test_abandon_unknown_round_is_404(app):
    async with async_client(app) as client:
        response = await client.post("/api/rounds/r-nope/abandon")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_round"
