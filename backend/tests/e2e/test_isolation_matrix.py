"""跨租户隔离矩阵（多用户改造 Task 6）+ SSE 按 owner 裁剪。

落笔即定：

- **越权一律 404（与"不存在"同形）**：B 读/写/删 A 的项目都拿 404，绝不 200，
  也绝不 500（那会把"这是别人的"和"服务器炸了"混在一起）。
- **本任务只加身份，不改资源语义**：所有业务端点先过 `require_user`（未登录 401），
  再在用例层按 owner 收窄——A 自己的照常 200。
- **SSE 只推自己的**：订阅在 owner 上下文里做归属判断，别人的事件一个都不投。

`admin 读 A 的`：见报告疑虑 #1——D2（单 owner 环境上下文）下没有跨租户旁路，
`§0.12` 的"admin 可见全站"尚未实现，本任务范围外。这里如实钉住现状。
"""

import asyncio
import json

from tests.support.api_client import async_client

PW = "s3cret-密码"
ADMIN_PW = "admin-s3cret"

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
                "feature_slug": "order-export",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "导出要评估数据量"}],
    },
    ensure_ascii=False,
)

ROUND_INPUT = "想做个订单导出"


def _seed_active_user(app, *, username: str, password: str, role: str = "user"):  # type: ignore[no-untyped-def]
    """测试侧直接种一个 active 用户（照 `test_auth_flow._seed_active_admin` 的体例）。"""

    from pmstudio.infrastructure.security import hash_password

    users = app.state.auth.users
    user_id = users.register(
        username=username, password_hash=hash_password(password), role=role
    )
    users.set_status(user_id, "active")
    return user_id


async def _login(client, *, username: str, password: str):  # type: ignore[no-untyped-def]
    resp = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.cookies


async def test_user_b_cannot_touch_user_a_project(make_app):
    """A 建项目、传材料、起轮次；B 对同一条路径读/写/删**全 404**；A 自己 200。"""

    app, _ = make_app(replies={"claims": CLAIMS_REPLY}, autologin=False)
    async with async_client(app) as client:
        _seed_active_user(app, username="root", password=ADMIN_PW, role="admin")
        _seed_active_user(app, username="alice", password=PW)
        _seed_active_user(app, username="bob", password=PW)
        admin = await _login(client, username="root", password=ADMIN_PW)
        alice = await _login(client, username="alice", password=PW)
        bob = await _login(client, username="bob", password=PW)

        client.cookies = alice
        created = await client.post("/api/projects", json={"title": "A 的项目"})
        assert created.status_code == 201, created.text
        project_id = created.json()["project_id"]

        material = await client.post(
            f"/api/projects/{project_id}/attachments",
            files={"file": ("需求.md", "# 目标\n导出".encode(), "text/markdown")},
        )
        assert material.status_code == 201, material.text
        material_id = material.json()["material_id"]

        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": ROUND_INPUT}
        )
        assert started.status_code == 200, started.text
        round_id = started.json()["round_id"]

        # —— B 全 404（读 / 写 / 删，同一条路径都不漏）——
        client.cookies = bob
        assert (
            await client.get(f"/api/projects/{project_id}/workspace")
        ).status_code == 404
        assert (
            await client.get(f"/api/projects/{project_id}/document")
        ).status_code == 404
        assert (
            await client.get(f"/api/projects/{project_id}/versions")
        ).status_code == 404
        assert (
            await client.get(f"/api/projects/{project_id}/evidence/E1")
        ).status_code == 404
        assert (
            await client.post(
                f"/api/projects/{project_id}/edits",
                json={"op": "add", "target": {"zone": "body", "section_id": "s2"}, "text": "x"},
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": ROUND_INPUT}
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/projects/{project_id}/attachments/{material_id}/reparse"
            )
        ).status_code == 404
        assert (
            await client.delete(
                f"/api/projects/{project_id}/attachments/{material_id}"
            )
        ).status_code == 404
        assert (
            await client.post(
                f"/api/rounds/{round_id}/opening",
                json={"seats": ["seat.data_scale"], "claim_edits": []},
            )
        ).status_code == 404
        assert (
            await client.post(f"/api/rounds/{round_id}/confirm", json={"accepted": []})
        ).status_code == 404

        # A 看自己的照常（隔离不能误伤本人）
        client.cookies = alice
        assert (
            await client.get(f"/api/projects/{project_id}/workspace")
        ).status_code == 200
        assert (
            await client.get(f"/api/projects/{project_id}/document")
        ).status_code == 200

        # 现状：admin 无跨租户旁路（§0.12 未实现）——见模块 docstring 与报告疑虑 #1
        client.cookies = admin
        assert (
            await client.get(f"/api/projects/{project_id}/workspace")
        ).status_code == 404


async def test_business_endpoint_requires_login(make_app):
    """业务端点未登录 = 401（不是 404/500）：身份不通过就不谈资源。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        assert (await client.get("/api/projects")).status_code == 401
        assert (await client.get("/api/projects/p1/workspace")).status_code == 401
        assert (await client.get("/api/schema")).status_code == 401
        assert (await client.get("/api/events")).status_code == 401


def test_sse_only_delivers_my_events(tmp_path):
    """两个 owner 各订一条流：只收到自己项目的事件（别人的一条都不投）。"""

    from pmstudio.api.sse import event_stream, owner_visible
    from pmstudio.application.use_cases import ListProjects
    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.ids import DocumentId, ProjectId, UserId
    from pmstudio.harness.events import Event
    from pmstudio.infrastructure.sqlite import database, owner_scope
    from pmstudio.infrastructure.sqlite.repositories import (
        SqliteDocumentRepository,
        SqliteWorkspaceIndex,
    )
    from tests.support.builders import NOW

    alice = UserId("u-alice")
    bob = UserId("u-bob")

    conn = database.connect(tmp_path / "sse.db")
    docs = SqliteDocumentRepository(conn)
    for project_id, owner in (("p-a", alice), ("p-b", bob)):
        docs.save(
            Document.new(
                document_id=DocumentId(f"doc-{project_id}"),
                project_id=ProjectId(project_id),
                title=project_id,
                now=NOW,
            ),
            owner=owner,
        )

    class StubStream:
        def __init__(self, events: list[Event]) -> None:
            self._events = events

        async def _iterate(self):  # type: ignore[no-untyped-def]
            for event in self._events:
                yield event

        def subscribe(self):  # type: ignore[no-untyped-def]
            return self._iterate()

    def event_for(project_id: str) -> Event:
        return Event(
            name="round.updated",
            project_id=project_id,
            occurred_at=NOW,
            seq=1,
        )

    index = SqliteWorkspaceIndex(conn)

    async def frames(owner, events):  # type: ignore[no-untyped-def]
        visible = owner_visible(
            scope=owner_scope, list_owned=ListProjects(index=index), owner=owner
        )
        return [
            chunk
            async for chunk in event_stream(
                StubStream(events), heartbeat_seconds=5, visible=visible
            )
        ]

    alice_chunks = asyncio.run(frames(alice, [event_for("p-a"), event_for("p-b")]))
    bob_chunks = asyncio.run(frames(bob, [event_for("p-a"), event_for("p-b")]))

    alice_business = [c for c in alice_chunks if c.startswith("event:")]
    bob_business = [c for c in bob_chunks if c.startswith("event:")]
    assert len(alice_business) == 1 and '"p-a"' in alice_business[0]
    assert len(bob_business) == 1 and '"p-b"' in bob_business[0]
