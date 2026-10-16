"""认证全链路（真 SQLite + 真容器 + HTTP；模型零调用）。

闭环：注册 → pending 登录被拒 → 管理员审批 → 登录（发 `pm_session`）→ /me → 登出 →
会话失效。另加三条：批准后 active、封禁后下一次请求即 401、过期会话即 401。

管理员由测试直接经仓储种下（`app.state.auth.users`），再走 HTTP 登录拿 cookie——
不依赖 env 里的首管理员种子（那条走的是 `Settings.from_env` 的另一条路）。
"""

import hashlib
from datetime import UTC, datetime, timedelta

from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.security import hash_password
from tests.support.api_client import async_client

ADMIN_PW = "admin-s3cret"


def _seed_active_admin(app):
    """建一个 active 的 admin，返回它的 user_id（测试侧，不演变接口）。"""

    users = app.state.auth.users
    admin_id = users.register(
        username="root", password_hash=hash_password(ADMIN_PW), role="admin"
    )
    users.set_status(admin_id, "active")
    return admin_id


async def _admin_cookie(client):
    resp = await client.post(
        "/api/auth/login", json={"username": "root", "password": ADMIN_PW}
    )
    assert resp.status_code == 200
    return resp.cookies


async def test_register_approve_login_logout_ban_over_http(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        reg = await client.post(
            "/api/auth/register", json={"username": "alice", "password": "s3cret-密码"}
        )
        assert reg.status_code == 201
        alice_id = reg.json()["user_id"]
        bad_login = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "s3cret-密码"}
        )
        assert bad_login.status_code == 401  # pending 不给进
        approved = await client.post(
            "/api/auth/approve", json={"user_id": alice_id}, cookies=admin
        )
        assert approved.status_code == 200
        ok = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "s3cret-密码"}
        )
        assert ok.status_code == 200
        assert "pm_session" in ok.cookies
        me = await client.get("/api/auth/me", cookies=ok.cookies)
        assert me.json()["username"] == "alice"
        out = await client.post("/api/auth/logout", cookies=ok.cookies)
        assert out.status_code == 200
        dead = await client.get("/api/auth/me", cookies=ok.cookies)
        assert dead.status_code == 401


async def test_approve_activates_pending_user(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        reg = await client.post(
            "/api/auth/register", json={"username": "bob", "password": "bob-s3cret"}
        )
        bob_id = reg.json()["user_id"]
        approved = await client.post(
            "/api/auth/approve", json={"user_id": bob_id}, cookies=admin
        )
        assert approved.status_code == 200
        login = await client.post(
            "/api/auth/login", json={"username": "bob", "password": "bob-s3cret"}
        )
        assert login.status_code == 200
        me = await client.get("/api/auth/me", cookies=login.cookies)
        assert me.json()["status"] == "active"


async def test_ban_blocks_next_request(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        reg = await client.post(
            "/api/auth/register", json={"username": "carol", "password": "carol-s3cret"}
        )
        carol_id = reg.json()["user_id"]
        await client.post("/api/auth/approve", json={"user_id": carol_id}, cookies=admin)
        login = await client.post(
            "/api/auth/login", json={"username": "carol", "password": "carol-s3cret"}
        )
        assert (await client.get("/api/auth/me", cookies=login.cookies)).status_code == 200
        banned = await client.post(
            "/api/auth/ban", json={"user_id": carol_id}, cookies=admin
        )
        assert banned.status_code == 200
        dead = await client.get("/api/auth/me", cookies=login.cookies)
        assert dead.status_code == 401


async def test_expired_session_is_unauthorized(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        admin_id = _seed_active_admin(app)
        token = "expired-token"
        past = datetime.now(UTC) - timedelta(days=1)
        app.state.auth.sessions.create(
            user_id=UserId(str(admin_id)),
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=past,
            now=past,
        )
        resp = await client.get("/api/auth/me", cookies={"pm_session": token})
        assert resp.status_code == 401
