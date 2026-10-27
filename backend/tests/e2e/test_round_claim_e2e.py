"""两个 app（同库、各一条 PostgreSQL 连接）抢同一轮：一个进图，另一个 409。

这是 Task 2 的跨进程互斥在**真 HTTP + 真 PostgreSQL** 上的证据：两个 `RoundRunner`
各自有独立的 `_running_rounds`（内存快路径互相看不见），只有行上的 DB lease
能挡住第二个。跑中跳过（借用 action lease 的控制写）也在这条路径上验一次。
"""

from __future__ import annotations

import asyncio
import os

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import AppSettings
from pmstudio.domain.shared.ids import UserId
from pmstudio.domain.shared.lease import LeaseKind
from pmstudio.infrastructure.postgres.leases import PgLeaseStore
from tests.e2e.conftest import _ensure_authed_user
from tests.support.api_client import async_client
from tests.support.doubles import ScriptedModelGateway
from tests.support.postgres import open_direct

CLAIMS_REPLY = """{
  "basis": "读了空文档",
  "claims": [
    {
      "text": "要做一个订单导出功能",
      "raw_span": "想做个订单导出的功能",
      "label": "need",
      "zone": "body",
      "section_id": "s5",
      "feature_slug": "order-export"
    }
  ],
  "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}]
}"""

SEAT = "seat.data_scale"


class BlockingSeatGateway(ScriptedModelGateway):
    def __init__(self, *, block_on: str = "seat_review") -> None:
        super().__init__(
            replies={
                "claims": CLAIMS_REPLY,
                "integration": """{
                  "questions": [],
                  "list_items": [],
                  "proposals": [
                    {
                      "op": "add",
                      "target": {
                        "zone": "body",
                        "section_id": "s5",
                        "feature_slug": "order-export",
                        "field": "exceptions"
                      },
                      "text": "无导出权限的用户不显示导出入口",
                      "origin_type": "derived",
                      "evidence_refs": ["E1"],
                      "rationale": "订单含客户信息"
                    }
                  ],
                  "dropped_opinions": []
                }""",
            },
            seat_replies={
                SEAT: """{
                  "basis": "读了空文档",
                  "opinions": [
                    {
                      "local_id": "O1",
                      "form": "judgment",
                      "about": "异常与边界",
                      "claim": "无导出权限的用户不显示导出入口",
                      "because": "订单含客户信息",
                      "evidence_refs": []
                    }
                  ]
                }"""
            },
        )
        self.block_on = block_on
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.blocked = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        if request.purpose == self.block_on:
            self.blocked += 1
            self.entered.set()
            await self.release.wait()
        return await super().complete(request)


def _app(gateway: ScriptedModelGateway):  # type: ignore[no-untyped-def]
    settings = AppSettings(
        database_url=os.environ["PMSTUDIO_DATABASE_URL"],
        model="scripted",
        api_key="test-key",
        api_base=None,
        login_ip_max_attempts=10_000,
        login_username_max_attempts=10_000,
    )
    from pmstudio.infrastructure.postgres.repositories import SqliteTemplateRepository
    from pmstudio.infrastructure.templates.loader import seed_builtin_templates

    with open_direct() as connection:
        seed_builtin_templates(SqliteTemplateRepository(connection))
    app = build_container(settings, gateway=gateway).app
    _ensure_authed_user(app)
    return app


async def test_second_worker_gets_409_and_skip_does_not_steal_the_lease() -> None:
    gateway = BlockingSeatGateway()
    first = _app(gateway)
    second = _app(ScriptedModelGateway(replies={"claims": CLAIMS_REPLY}))

    async with async_client(first) as a, async_client(second) as b:
        project_id = (
            await a.post("/api/projects", json={"title": "租约真跑"})
        ).json()["project_id"]
        round_id = (
            await a.post(
                f"/api/projects/{project_id}/rounds",
                json={"input": "想做个订单导出的功能"},
            )
        ).json()["round_id"]

        opening = asyncio.create_task(
            a.post(f"/api/rounds/{round_id}/opening", json={"seats": [SEAT]})
        )
        await asyncio.wait_for(gateway.entered.wait(), timeout=5)

        busy = await b.post(f"/api/rounds/{round_id}/opening", json={"seats": [SEAT]})
        assert busy.status_code == 409, busy.text[:300]
        assert busy.json()["code"] == "round_busy"
        assert gateway.blocked == 1

        # 跳过是控制写：借当前 action lease，不抢、不取消在跑的 opening。
        skipped = await b.post(
            f"/api/rounds/{round_id}/skip-seat", json={"seat_id": SEAT}
        )
        assert skipped.status_code == 200, skipped.text[:300]

        gateway.release.set()
        opened = await asyncio.wait_for(opening, timeout=5)
        assert opened.status_code == 200, opened.text[:300]

        view = (await b.get(f"/api/rounds/{round_id}")).json()
        assert view["waiting_on"] == "draft_card"
        packet = next(
            item for item in view["seat_packets"] if item["seat_id"] == SEAT
        )
        assert packet["status"] == "skipped"



def _round_lease_owner(round_id: str) -> tuple[UserId, str]:
    connection = open_direct()
    try:
        row = connection.execute(
            "SELECT owner_id, lease_owner FROM rounds WHERE round_id = %s", (round_id,)
        ).fetchone()
    finally:
        connection.close()
    return UserId(row["owner_id"]), str(row["lease_owner"])


async def test_lost_lease_returns_409_round_lease_lost() -> None:
    gateway = BlockingSeatGateway(block_on="claims")
    app = _app(gateway)
    app.state.services.runner._lease_heartbeat_seconds = 0.02

    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "租约失守"})
        ).json()["project_id"]
        pending = asyncio.create_task(
            client.post(
                f"/api/projects/{project_id}/rounds",
                json={"input": "想做个订单导出的功能"},
            )
        )
        await asyncio.wait_for(gateway.entered.wait(), timeout=5)
        connection = open_direct()
        try:
            round_id = str(
                connection.execute("SELECT round_id FROM rounds").fetchone()["round_id"]
            )
        finally:
            connection.close()
        owner, holder = _round_lease_owner(round_id)
        assert holder != "thief"

        thief = open_direct()
        try:
            PgLeaseStore(thief).force_acquire(
                LeaseKind.ROUND, round_id, owner, "thief", 90_000
            )
        finally:
            thief.close()

        lost = await asyncio.wait_for(pending, timeout=5)

    assert lost.status_code == 409, lost.text[:300]
    assert lost.json()["code"] == "round_lease_lost"
    assert _round_lease_owner(round_id)[1] == "thief"
