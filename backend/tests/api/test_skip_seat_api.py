"""用户跳过席位的端点：`POST /rounds/{round_id}/skip-seat`。"""

import asyncio
import contextlib
import json

import pytest

from pmstudio.api.app import create_app
from tests.support.api_client import async_client
from tests.support.doubles import HangingSeatGateway
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
    # 席位调用挂住 30 秒：opening 在后台跑，跳过才能落在"跑中"。
    # 这同时验了设计要点：跳过不走 runner 串行闸，与跑中动作并发。
    gateway = HangingSeatGateway(replies={"claims": CLAIMS_REPLY}, hang_seconds=30.0)
    harness = Harness(gateway=gateway)
    harness.seed_project()
    return create_app(harness.api_services(model_name="m"))


async def _running_round_id(client, project_id: str) -> str:
    started = await client.post(
        f"/api/projects/{project_id}/rounds", json={"input": "导出要快一点"}
    )
    return started.json()["round_id"]


async def _open_in_background(client, round_id: str):
    """开场确认扔后台跑（席位挂起），返回 task。调用方负责 cancel 掉。"""

    task = asyncio.create_task(
        client.post(
            f"/api/rounds/{round_id}/opening", json={"seats": ["seat.data_scale"]}
        )
    )
    await asyncio.sleep(0.05)  # 让它跑到席位那次 await（挂住）
    return task


async def test_skip_seat_endpoint_skips_and_returns_view(app):
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        round_id = await _running_round_id(client, project_id)
        opening = await _open_in_background(client, round_id)
        try:
            response = await client.post(
                f"/api/rounds/{round_id}/skip-seat", json={"seat_id": "seat.data_scale"}
            )
        finally:
            opening.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await opening

    assert response.status_code == 200
    body = response.json()
    packet = next(p for p in body["seat_packets"] if p["seat_id"] == "seat.data_scale")
    assert packet["status"] == "skipped"


async def test_skip_seat_unknown_seat_is_409(app):
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        round_id = await _running_round_id(client, project_id)
        opening = await _open_in_background(client, round_id)
        try:
            response = await client.post(
                f"/api/rounds/{round_id}/skip-seat", json={"seat_id": "seat.nope"}
            )
        finally:
            opening.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await opening

    assert response.status_code == 409


async def test_skip_seat_unknown_round_is_404(app):
    async with async_client(app) as client:
        response = await client.post(
            "/api/rounds/r-nope/skip-seat", json={"seat_id": "seat.data_scale"}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_round"
