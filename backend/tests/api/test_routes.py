import json

from pmstudio.api.app import ApiServices, create_app
from tests.support.api_client import async_client
from tests.support.harness import Harness

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
                "feature_slug": "order-export",
            }
        ],
    },
    ensure_ascii=False,
)

QUESTION_REPLY = json.dumps(
    {
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数是多少？",
                "why": "量级决定同步还是异步",
                "consequence_if_skipped": "按同步实现",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万–10 万"},
                ],
                "evidence_refs": ["E1"],
                "asked_by": ["seat.data_scale"],
            }
        ],
        "list_items": [],
        "proposals": [],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "异常与边界",
                "claim": "无导出权限的用户不显示导出入口",
                "because": "订单含客户信息",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)


def _services(harness: Harness) -> ApiServices:
    return harness.api_services(model_name="openai/gpt-4o-mini")


def make_client():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": QUESTION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )
    return create_app(_services(harness)), harness


async def test_health_reports_model():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "openai/gpt-4o-mini"}


async def test_create_project_then_workspace():
    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        workspace = await client.get(f"/api/projects/{project_id}/workspace")
        projects = await client.get("/api/projects")

    assert created.status_code == 201
    assert created.json()["title"] == "订单中台"

    assert workspace.status_code == 200
    body = workspace.json()
    assert body["project"]["title"] == "订单中台"
    assert [section["section_id"] for section in body["document"]["sections"]] == [
        f"s{index}" for index in range(1, 14)
    ]
    assert body["round"] is None
    assert projects.json()[0]["project_id"] == project_id


async def test_full_loop_over_http():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
        )
        round_id = started.json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )

    assert started.status_code == 200
    assert started.json()["waiting_on"] == "opening_card"
    assert opened.status_code == 200
    assert opened.json()["waiting_on"] == "questionnaire"
    assert opened.json()["form_id"] == "f1"


async def test_opening_accepts_claim_edits():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={
                "seats": ["seat.data_scale"],
                "claim_edits": [
                    {
                        "claim_id": "c1",
                        "label": "goal",
                        "target": {"zone": "body", "section_id": "s2"},
                    }
                ],
            },
        )

    assert opened.status_code == 200
    assert opened.json()["claims"][0]["label"] == "goal"
    assert opened.json()["claims"][0]["target_label"] == "s2"


async def test_confirm_response_shape():
    """没走到草案卡时确认要被状态机挡住（409），这是"AI 不能自己写"的直接体现。"""

    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        response = await client.post(
            f"/api/rounds/{round_id}/confirm", json={"accepted": ["p01"]}
        )

    assert response.status_code == 409
    assert response.json()["code"].startswith("invariant:")


async def test_unknown_round_is_404():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/rounds/r404")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_round"


async def test_bad_body_is_422():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.post("/api/projects", json={"title": ""})
    assert response.status_code == 422


async def test_model_failure_is_502():
    app, harness = make_client()
    harness.gateway._replies.clear()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
        )

    assert response.status_code == 502
    assert response.json()["code"] == "model_call_failed"


async def test_round_view_exposes_discussion_stream_data():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        body = (await client.get(f"/api/rounds/{round_id}")).json()

    assert body["claims"][0]["eid"] == "E1"
    assert body["claims"][0]["label"] == "need"
    assert body["seat_packets"][0]["status"] == "pending"
    assert body["nominated_seats"][0]["seat_id"] == "seat.data_scale"
