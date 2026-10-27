"""可观测性 v2 · P/R 类：真 SQLite + 真 `WebResearch` 路径 + **真端点**（TestClient）。

**如实标注**：本环境没有 `PMSTUDIO_TAVILY_API_KEY`，所以工具侧降级为**注入的替身**
（`RecordingWebSearch`）——真 `TavilySearchGateway` 会出网。模型侧是脚本化网关
（e2e 一贯如此）。写侧走的是**真** `WebResearch`（含 `search_one` 与真留样器）。
"""

import json
from datetime import UTC, datetime

import pmstudio.bootstrap.container as container_module
from pmstudio.infrastructure.security import hash_password
from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder
from tests.support.api_client import async_client
from tests.support.builders import make_web_packet
from tests.support.doubles import RecordingWebSearch

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "导出要评估数据量"}],
        "web_queries": [{"query": "RBAC 权限模型", "why": "要查行业通行做法"}],
    },
    ensure_ascii=False,
)

QUERY = "RBAC 权限模型"
ADMIN_PW = "admin-s3cret"

SEAT_REPLY = json.dumps(
    {"basis": "读了检索结果", "opinions": []}, ensure_ascii=False
)

INTEGRATION_REPLY = json.dumps(
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


def _seed_active_admin(app):
    users = app.state.auth.users
    admin_id = users.register(
        username="root", password_hash=hash_password(ADMIN_PW), role="admin"
    )
    users.set_status(admin_id, "active")
    return admin_id


def _seed_active_user(app, *, username: str, password: str, role: str = "user"):
    users = app.state.auth.users
    user_id = users.register(
        username=username, password_hash=hash_password(password), role=role
    )
    users.set_status(user_id, "active")
    return user_id


def _injected_web_search(returns=()):  # type: ignore[no-untyped-def]
    """把联网网关的**构造**换成替身（真网关会出网）。"""

    def factory(settings, *, tools, keys):  # type: ignore[no-untyped-def]
        return RecordingWebSearch(returns=returns)

    return factory


async def _admin_cookie(client):
    resp = await client.post(
        "/api/auth/login", json={"username": "root", "password": ADMIN_PW}
    )
    assert resp.status_code == 200, resp.text
    return resp.cookies


async def test_a_real_round_writes_tool_calls_and_serves_them_with_injected_tool_double(
    make_app, monkeypatch
):
    """P：真 SQLite + 真执行层路径 + 真端点；工具侧是注入的替身（本环境无 Tavily key）。"""

    monkeypatch.setattr(
        container_module, "_web_search_gateway", _injected_web_search((make_web_packet(url="https://a"),))
    )
    app, _ = make_app(
        replies={"claims": CLAIMS_REPLY, "integration": INTEGRATION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
        trace_model=True,
        tavily_api_key="test-key",
    )
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"}
            )
        ).json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={"seats": ["seat.data_scale"], "web_queries": [QUERY]},
        )

        # 模型侧也补一条（脚本化网关不留样）：验两侧**并成一份**
        SqliteModelCallRecorder(app.state.auth.users._connection).record(
            trace_id=round_id,
            purpose="claims",
            model="scripted",
            prompt_sha="sha",
            prompt_text="p",
            reply_text="r",
            usage={},
            duration_ms=5,
            created_at=datetime.now(UTC),
        )

        calls = await client.get(f"/api/rounds/{round_id}/calls")

    assert opened.status_code == 200, opened.text
    assert calls.status_code == 200, calls.text
    body = calls.json()
    assert body["tool_calls"] == 1
    assert body["tool_failed"] == 0
    assert body["model_calls"] == 1
    assert {entry["kind"] for entry in body["calls"]} == {"model", "tool"}
    tool_entry = next(entry for entry in body["calls"] if entry["kind"] == "tool")
    assert tool_entry["name"] == "web_search"
    assert tool_entry["ok"] is True
    assert tool_entry["duration_ms"] >= 0
    # 模型侧 error_kind 恒为 NULL（不硬凑）
    assert next(e for e in body["calls"] if e["kind"] == "model")["error_kind"] is None


async def test_the_admin_stats_report_both_failure_rates(make_app):
    """P：真库灌行后，模型侧与工具侧的失败率**分别**算（两个分母）。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        conn = app.state.auth.users._connection
        # 灌行要挂在一个**真实存在**的用户上（读模型按 users 表裁人）
        owner = str(_seed_active_user(app, username="bob", password="bob-s3cret"))
        conn.execute(
            "INSERT INTO rounds (round_id, project_id, step, waiting_on, payload,"
            " created_at, updated_at, owner_id) VALUES ('r-x','p-x','done','none','{}',"
            " '2026-10-23T00:00:00+00:00','2026-10-23T00:00:00+00:00', %s)",
            (owner,),
        )
        for duration, ok in ((1000, True), (3000, False)):
            conn.execute(
                "INSERT INTO model_calls (round_id, purpose, model, prompt_sha,"
                " prompt_text, reply_text, usage_json, duration_ms, ok, created_at)"
                " VALUES ('r-x','integration','m','sha','p','r','{}',%s,%s,"
                " '2026-10-23T00:00:00+00:00')",
                (duration, ok),
            )
        for duration, ok in ((200, True), (400, True), (900, False)):
            conn.execute(
                "INSERT INTO tool_calls (round_id, tool_id, arguments, result_note,"
                " duration_ms, created_at, ok) VALUES ('r-x','web_search','{}','x',%s,"
                " '2026-10-23T00:00:00+00:00',%s)",
                (duration, ok),
            )

        resp = await client.get("/api/auth/stats", cookies=admin)

    assert resp.status_code == 200, resp.text
    row = {item["user_id"]: item for item in resp.json()["stats"]}[owner]

    # 模型侧：2 次调用、1 次失败、均延迟 2000ms
    assert row["calls"] == 2
    assert row["failed"] == 1
    assert row["avg_duration_ms"] == 2000
    # 工具侧：3 次调用、1 次失败、均延迟 500ms —— **分开算**（改前这里恒为 0）
    assert row["tool_calls"] == 3
    assert row["tool_failed"] == 1
    assert row["tool_avg_duration_ms"] == 500


async def test_round_calls_of_another_owner_is_404(make_app):
    """I/R：别人的轮次读不到（404）——归属校验走 `rounds.get`，与 `GetRound` 同一条路。"""

    app, _ = make_app(replies={"claims": CLAIMS_REPLY}, autologin=False)
    async with async_client(app) as client:
        _seed_active_admin(app)
        _seed_active_user(app, username="mallory", password="mallory-s3cret")
        mallory = await client.post(
            "/api/auth/login", json={"username": "mallory", "password": "mallory-s3cret"}
        )
        assert mallory.status_code == 200
        client.cookies = mallory.cookies
        project_id = (
            await client.post("/api/projects", json={"title": "Mallory 的项目"})
        ).json()["project_id"]
        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"}
        )
        assert started.status_code == 200, started.text
        round_id = started.json()["round_id"]

        # 换回 admin（autologin=False 时要自己登录）
        client.cookies = await _admin_cookie(client)
        resp = await client.get(f"/api/rounds/{round_id}/calls")

    assert resp.status_code == 404
    assert resp.json()["code"] == "unknown_round"


async def test_trace_off_writes_nothing(make_app, monkeypatch):
    """R：留样关时 `tool_calls` 零行，业务行为逐字不变。"""

    monkeypatch.setattr(
        container_module, "_web_search_gateway", _injected_web_search((make_web_packet(url="https://a"),))
    )
    app, _ = make_app(
        replies={"claims": CLAIMS_REPLY, "integration": INTEGRATION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
        trace_model=False,  # 默认（生产）状态
        tavily_api_key="test-key",
    )
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"}
            )
        ).json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={"seats": ["seat.data_scale"], "web_queries": [QUERY]},
        )
        calls = await client.get(f"/api/rounds/{round_id}/calls")
        conn = app.state.auth.users._connection
        rows = conn.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"]

    assert opened.status_code == 200, opened.text
    assert rows == 0
    assert calls.json()["calls"] == []  # 读侧如实回空，不造假


async def test_the_existing_stats_fields_are_unchanged(make_app):
    """R：`/auth/stats` 的老字段**只多不少**（三个新字段是加法，不是替换）。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        resp = await client.get("/api/auth/stats", cookies=admin)

    assert resp.status_code == 200, resp.text
    row = resp.json()["stats"][0]
    assert {
        "user_id",
        "username",
        "role",
        "status",
        "projects",
        "rounds",
        "calls",
        "failed",
        "avg_duration_ms",
        "total_tokens",
        "active_sessions",
        "last_seen_at",
        "created_at",
        "approved_at",
        "approved_by",
    } <= set(row)
