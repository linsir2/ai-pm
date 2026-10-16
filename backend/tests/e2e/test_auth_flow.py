"""认证全链路（真 SQLite + 真容器 + HTTP；模型零调用）。

闭环：注册 → pending 登录被拒 → 管理员审批 → 登录（发 `pm_session`）→ /me → 登出 →
会话失效。另加三条：批准后 active、封禁后下一次请求即 401、过期会话即 401。

管理员由测试直接经仓储种下（`app.state.auth.users`），再走 HTTP 登录拿 cookie——
不依赖 env 里的首管理员种子（那条走的是 `Settings.from_env` 的另一条路）。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.security import hash_password
from tests.support.api_client import async_client

ADMIN_PW = "admin-s3cret"
ROUND_INPUT = "想做个订单导出"

#: 起一轮够用的一份 analyzer 回包（照 `test_isolation_matrix.py` 的体例）。
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


def _seed_active_admin(app):
    """建一个 active 的 admin，返回它的 user_id（测试侧，不演变接口）。"""

    users = app.state.auth.users
    admin_id = users.register(
        username="root", password_hash=hash_password(ADMIN_PW), role="admin"
    )
    users.set_status(admin_id, "active")
    return admin_id


def _seed_active_user(app, *, username: str, password: str, role: str = "user"):
    """建一个 active 的普通用户，返回它的 user_id（测试侧，不演变接口）。"""

    users = app.state.auth.users
    user_id = users.register(
        username=username, password_hash=hash_password(password), role=role
    )
    users.set_status(user_id, "active")
    return user_id


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


async def test_session_past_absolute_cap_is_unauthorized(make_app):
    """绝对上限（90 天）：会话再新（滑动未过期），从 created_at 起超 90 天即 401。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        admin_id = _seed_active_admin(app)
        token = "aged-token"
        long_ago = datetime.now(UTC) - timedelta(days=91)
        app.state.auth.sessions.create(
            user_id=UserId(str(admin_id)),
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            # 滑动没过期（30 天后才到），唯独 created_at 超了绝对上限
            expires_at=datetime.now(UTC) + timedelta(days=30),
            now=long_ago,
        )
        resp = await client.get("/api/auth/me", cookies={"pm_session": token})
        assert resp.status_code == 401


async def test_logout_revokes_only_current_session(make_app):
    """两设备登录：一台登出只踢它自己，另一台仍可用（per-device 会话，Ruling 18）。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        _seed_active_admin(app)
        first = await client.post(
            "/api/auth/login", json={"username": "root", "password": ADMIN_PW}
        )
        second = await client.post(
            "/api/auth/login", json={"username": "root", "password": ADMIN_PW}
        )
        assert first.status_code == second.status_code == 200
        token_a = first.cookies["pm_session"]
        token_b = second.cookies["pm_session"]
        assert token_a != token_b

        out = await client.post("/api/auth/logout", cookies={"pm_session": token_a})
        assert out.status_code == 200

        dead = await client.get("/api/auth/me", cookies={"pm_session": token_a})
        assert dead.status_code == 401
        alive = await client.get("/api/auth/me", cookies={"pm_session": token_b})
        assert alive.status_code == 200


async def test_absolute_cap_revokes_only_matched_session(make_app):
    """90 天绝对上限命中哪条吊销哪条：同一用户的另一条会话不受影响（Ruling 18）。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        admin_id = _seed_active_admin(app)
        now = datetime.now(UTC)
        aged_token = "aged-token"
        fresh_token = "fresh-token"
        aged_hash = hashlib.sha256(aged_token.encode("utf-8")).hexdigest()
        app.state.auth.sessions.create(
            user_id=UserId(str(admin_id)),
            token_hash=aged_hash,
            expires_at=now + timedelta(days=30),
            now=now - timedelta(days=91),
        )
        app.state.auth.sessions.create(
            user_id=UserId(str(admin_id)),
            token_hash=hashlib.sha256(fresh_token.encode("utf-8")).hexdigest(),
            expires_at=now + timedelta(days=30),
            now=now,
        )

        aged = await client.get("/api/auth/me", cookies={"pm_session": aged_token})
        assert aged.status_code == 401
        assert app.state.auth.sessions.get_by_token_hash(aged_hash) is None
        fresh = await client.get("/api/auth/me", cookies={"pm_session": fresh_token})
        assert fresh.status_code == 200


async def test_me_reissues_cookie_when_sliding_renews(make_app):
    """`/me` 滑动续期时重发 `pm_session`（同 cookie、新 Max-Age=ttl），浏览器侧滑动才生效。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        admin_id = _seed_active_admin(app)
        token = "sliding-token"
        now = datetime.now(UTC)
        app.state.auth.sessions.create(
            user_id=UserId(str(admin_id)),
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=now + timedelta(days=30),
            now=now - timedelta(days=20),  # last_seen 20 天前 > ttl/2（15 天），触发续期
        )

        resp = await client.get("/api/auth/me", cookies={"pm_session": token})

        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "pm_session=" in set_cookie
        assert f"Max-Age={app.state.auth.cookie_max_age}" in set_cookie
        assert "httponly" in set_cookie.lower()


async def test_login_cookie_secure_only_behind_https(make_app):
    """Secure 按请求协议给：`X-Forwarded-Proto: https`（Caddy 反代）才带，本地 http 不带。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        plain = await client.post(
            "/api/auth/login", json={"username": "root", "password": ADMIN_PW}
        )
        assert "Secure" not in plain.headers["set-cookie"]
        forwarded = await client.post(
            "/api/auth/login",
            json={"username": "root", "password": ADMIN_PW},
            headers={"X-Forwarded-Proto": "https"},
        )
        assert "Secure" in forwarded.headers["set-cookie"]


def test_seed_first_admin_builds_once(tmp_path):
    """首管理员种子：空库首启建一个 active admin；非空库再启动不重复建、不覆盖。"""

    settings = Settings(
        db_path=tmp_path / "pmstudio.db",
        model="scripted",
        api_key="test-key",
        api_base=None,
        first_admin_username="root",
        first_admin_password="root-s3cret",
    )

    first = build_container(settings)
    seeded = first.app.state.auth.users.list_all()
    assert [record.username for record in seeded] == ["root"]
    assert seeded[0].role == "admin"
    assert seeded[0].status == "active"

    second = build_container(settings)
    assert [record.username for record in second.app.state.auth.users.list_all()] == ["root"]


#: 全部管理端点（`require_admin` 必须逐条挂上——漏一条这里就红）。
ADMIN_ENDPOINTS = [
    ("post", "/api/auth/approve"),
    ("post", "/api/auth/reject"),
    ("post", "/api/auth/ban"),
    ("get", "/api/auth/users"),
    ("get", "/api/auth/usage"),
]


async def _call_admin_endpoint(client, method, path, *, cookies=None):
    """对管理端点发一次请求；POST 发**空 body**——鉴权在依赖里先于业务校验，403/401 应先生效。"""

    if method == "get":
        return await client.get(path, cookies=cookies)
    return await client.post(path, json={}, cookies=cookies)


@pytest.mark.parametrize("method, path", ADMIN_ENDPOINTS)
async def test_admin_endpoints_require_login(make_app, method, path):
    """未登录调任一管理端点 → 401（不是 403：先身份后角色）。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        resp = await _call_admin_endpoint(client, method, path)
        assert resp.status_code == 401


@pytest.mark.parametrize("method, path", ADMIN_ENDPOINTS)
async def test_non_admin_gets_403_on_admin_endpoint(make_app, method, path):
    """有身份但非 admin → 403，且先生效于 body 校验（空 body 也不该是 422）。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        reg = await client.post(
            "/api/auth/register", json={"username": "dave", "password": "dave-s3cret"}
        )
        dave_id = reg.json()["user_id"]
        await client.post("/api/auth/approve", json={"user_id": dave_id}, cookies=admin)
        login = await client.post(
            "/api/auth/login", json={"username": "dave", "password": "dave-s3cret"}
        )
        assert login.status_code == 200
        resp = await _call_admin_endpoint(client, method, path, cookies=login.cookies)
        assert resp.status_code == 403


async def test_reject_user_blocks_login(make_app):
    """admin 拒绝 pending 用户 → 该用户登录 401。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        reg = await client.post(
            "/api/auth/register", json={"username": "erin", "password": "erin-s3cret"}
        )
        erin_id = reg.json()["user_id"]
        rejected = await client.post(
            "/api/auth/reject", json={"user_id": erin_id}, cookies=admin
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        login = await client.post(
            "/api/auth/login", json={"username": "erin", "password": "erin-s3cret"}
        )
        assert login.status_code == 401


async def test_admin_lists_users_with_base_fields(make_app):
    """admin 列用户 → 200，每条含基础字段（无用量占位键）。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        _seed_active_admin(app)
        admin = await _admin_cookie(client)
        await client.post(
            "/api/auth/register", json={"username": "frank", "password": "frank-s3cret"}
        )
        resp = await client.get("/api/auth/users", cookies=admin)
        assert resp.status_code == 200
        users = resp.json()["users"]
        assert {item["username"] for item in users} == {"root", "frank"}
        for item in users:
            assert set(item) == {"user_id", "username", "role", "status"}


async def test_admin_usage_aggregates_model_calls_by_owner(make_app):
    """用量端点（Ruling 7）：按 owner 聚合其项目下轮次的 `model_calls` 行数（join，不加列）。

    真表真 join：起一轮拿到 `round_id`，经留样器写两行 `model_calls`，再读 `/auth/usage`。
    没有轮次 / 没有调用的用户计 0——用量是行数，不是"有没有这个人"。
    """

    from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder

    app, _ = make_app(replies={"claims": CLAIMS_REPLY}, autologin=False)
    async with async_client(app) as client:
        admin_id = _seed_active_admin(app)
        alice_id = _seed_active_user(app, username="alice", password="alice-s3cret")
        admin = await _admin_cookie(client)

        alice = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "alice-s3cret"}
        )
        assert alice.status_code == 200, alice.text
        client.cookies = alice.cookies
        project_id = (
            await client.post("/api/projects", json={"title": "A 的项目"})
        ).json()["project_id"]
        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": ROUND_INPUT}
        )
        assert started.status_code == 200, started.text
        round_id = started.json()["round_id"]

        # 留样器写两行（生产由网关旁路写；这里只借它的写路径，真表真列）
        recorder = SqliteModelCallRecorder(app.state.auth.users._connection)
        for _ in range(2):
            recorder.record(
                trace_id=round_id,
                purpose="analyze",
                model="scripted",
                prompt_sha="sha",
                prompt_text="p",
                reply_text="r",
                usage={},
                duration_ms=1,
                created_at=datetime.now(UTC),
            )

        client.cookies = admin
        resp = await client.get("/api/auth/usage")
        assert resp.status_code == 200, resp.text
        counts = {row["user_id"]: row["calls"] for row in resp.json()["usage"]}
        assert counts[str(alice_id)] == 2
        assert counts[str(admin_id)] == 0


async def test_nonexistent_login_runs_verify_once(make_app, monkeypatch):
    """恒定耗时锚点：登录一个不存在的用户名，也要对 decoy 哈希跑一次 verify_password。"""

    calls: list[tuple[str, str]] = []

    def counting_verify(password_hash: str, password: str) -> bool:
        calls.append((password_hash, password))
        return False

    monkeypatch.setattr("pmstudio.bootstrap.container.verify_password", counting_verify)
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        resp = await client.post(
            "/api/auth/login", json={"username": "ghost", "password": "ghost-s3cret"}
        )
        assert resp.status_code == 401
    assert len(calls) == 1
