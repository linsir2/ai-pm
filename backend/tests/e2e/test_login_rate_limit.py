"""登录**尝试速率闸**的端到端行为（高并发 P3）。

跑真容器 + 真 SQLite + 真 argon2，只把两个尝试上限调小以便触发。

**这一组钉的是三件事**：状态码分得开（429 vs 401）、不泄露账号是否存在、
限流命中时不烧 CPU。

注意：httpx 的 `testclient` 每次请求的源 IP 都是同一个，所以这里**验不了
真正的"跨 IP"**。跨 IP 的语义由 `test_redis_limiter.py`（打真 Redis、真的
换 ip 值）与 `test_login_guard.py`（内存闸）覆盖；这一组只验 HTTP 层的
状态码与响应体。
"""

from tests.e2e.test_auth_hardening import _seed_active_user
from tests.support.api_client import async_client

PW = "alice-s3cret"


def _app(make_app, *, ip_max: int, user_max: int = 10_000):  # noqa: ANN001
    return make_app(
        replies={},
        autologin=False,
        login_ip_max_attempts=ip_max,
        login_username_max_attempts=user_max,
    )[0]


async def _login(client, username: str, password: str):  # noqa: ANN001
    return await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


# ── 状态码分得开 ────────────────────────────────────────────────────────────


async def test_尝试超限回_429_且带_RetryAfter(make_app):
    """R：速率闸不是凭证闸 → 429 + `Retry-After`。
    I：per-IP 上限 3；P：同 IP 连打（密码全错）；R：前 3 次 401，第 4 次 429，
    响应头带正整数 `Retry-After`，body 的 code 是 `rate_limited`。"""

    app = _app(make_app, ip_max=3)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)

        for _ in range(3):
            assert (await _login(client, "alice", "wrong")).status_code == 401

        limited = await _login(client, "alice", "wrong")

        assert limited.status_code == 429
        assert limited.json()["code"] == "rate_limited"
        retry_after = limited.headers.get("Retry-After")
        assert retry_after is not None, "429 必须带 Retry-After，否则客户端只会硬重试"
        assert int(retry_after) > 0


async def test_失败锁仍回_401_不是_429(make_app):
    """R：**加了限流也不能改掉不可枚举性**——锁住这件事不能变成可观测信号。
    I：per-IP 上限放到不可能触发（只让失败锁生效）；P：错满阈值后用正确密码；
    R：401（**不是 429**）。"""

    app = _app(make_app, ip_max=10_000)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        for _ in range(5):
            await _login(client, "alice", "wrong")

        locked = await _login(client, "alice", PW)

        assert locked.status_code == 401, "失败锁回 429 就等于把『你被锁了』说出来了"


# ── 不泄露账号是否存在 ──────────────────────────────────────────────────────


async def test_429_不泄露账号是否存在(make_app):
    """**这是加 per-username 尝试闸的前提**，必须钉死。

    R：429 若是按"库里有这个账号"打的，就成了枚举侧信道。
    I：per-username 上限 2；P：分别对存在的 `alice` 与不存在的 `ghost`
    各打满；R：两者状态码与 body 逐字节相同。"""

    app = _app(make_app, ip_max=10_000, user_max=2)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)

        for name in ("alice", "ghost"):
            for _ in range(3):
                await _login(client, name, "wrong")
            assert (await _login(client, name, "wrong")).status_code == 429

        alice_body = (await _login(client, "alice", "wrong")).json()
        ghost_body = (await _login(client, "ghost", "wrong")).json()

        assert alice_body == ghost_body


async def test_不存在账号的_401_仍与错密码逐字节相同(make_app):
    """加闸之后，**未超限**时的原有不变量不许被破坏。"""

    app = _app(make_app, ip_max=10_000)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)

        ghost = await _login(client, "ghost", "whatever")
        wrong = await _login(client, "alice", "wrong")

        assert ghost.status_code == wrong.status_code == 401
        assert ghost.json() == wrong.json()


# ── 维度独立 ────────────────────────────────────────────────────────────────


async def test_per_IP_闸打满会拦住该_IP_上的其他人(make_app):
    """R：per-IP 闸不许退化成"按用户名"——同 IP 上换个账号也该被拦。
    拦不住的话，一个滥用者就能拿自己的账号绕过闸去试别人的。"""

    app = _app(make_app, ip_max=3)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        _seed_active_user(app, username="bob", password="bob-s3cret")

        for _ in range(3):
            await _login(client, "alice", "wrong")
        assert (await _login(client, "alice", "wrong")).status_code == 429

        # 另一个用户名、同一个源 IP
        assert (await _login(client, "bob", "bob-s3cret")).status_code == 429


async def test_per_username_闸按用户名生效(make_app):
    """R：分布式撞库打的是"账号"这一维——闸必须认用户名。
    I：per-username 上限 2、per-IP 放到不可能触发；P：错两次后第三次。"""

    app = _app(make_app, ip_max=10_000, user_max=2)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        for _ in range(2):
            assert (await _login(client, "alice", "wrong")).status_code == 401

        assert (await _login(client, "alice", "wrong")).status_code == 429


async def test_per_username_闸不影响别的用户名(make_app):
    """R：per-username 闸不许退化成全局闸——alice 被限不影响 bob。"""

    app = _app(make_app, ip_max=10_000, user_max=2)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        _seed_active_user(app, username="bob", password="bob-s3cret")
        for _ in range(3):
            await _login(client, "alice", "wrong")

        assert (await _login(client, "bob", "bob-s3cret")).status_code == 200


# ── 限流时不烧 CPU ──────────────────────────────────────────────────────────


async def test_限流命中时不跑_argon2(make_app):
    """R：被限流的请求不该继续消耗 CPU，更不该让攻击者免费用我们的算力。
    I：per-IP 上限 1；P：第一次错密码后数 `verify_password_async` 的调用次数；
    R：第二次（429）一次都没调。"""

    # patch `container` 的模块属性而不是 `security` 的：`container.py` 是
    # `from ... import verify_password_async` 按名字引进来的，改源头那个模块
    # 对已经绑定的名字无效（这个坑踩过一次）。
    from pmstudio.bootstrap import container as container_module

    calls: list[str] = []
    original = container_module.verify_password_async

    async def counting(hash_value: str, password: str) -> bool:  # noqa: ANN001
        calls.append(password)
        return await original(hash_value, password)

    container_module.verify_password_async = counting
    try:
        app = _app(make_app, ip_max=1)
        async with async_client(app) as client:
            _seed_active_user(app, username="alice", password=PW)
            first = await _login(client, "alice", "wrong")
            assert first.status_code == 401
            assert len(calls) == 1, "第一次应当真的跑了 argon2"

            second = await _login(client, "alice", "wrong")

            assert second.status_code == 429
            assert len(calls) == 1, "限流后仍然跑了 argon2——限流没省下 CPU"
    finally:
        container_module.verify_password_async = original


# ── 不误伤真人 ──────────────────────────────────────────────────────────────


async def test_正常登录不受影响(make_app):
    """R：闸门不许误伤真人——先确认正确密码还能进，否则前面那些测试都说不清算不算对。"""

    app = _app(make_app, ip_max=3)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)

        for _ in range(3):
            assert (await _login(client, "alice", PW)).status_code == 200


async def test_登录成功不消耗_失败锁额度(make_app):
    """R：手滑错一次再登录成功，之后还能再错几次——成功要清失败计数。
    少了这条，真人改完密码就再也登不进去。"""

    app = _app(make_app, ip_max=10_000)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)

        for _ in range(4):
            assert (await _login(client, "alice", "wrong")).status_code == 401
            assert (await _login(client, "alice", PW)).status_code == 200

        assert (await _login(client, "alice", PW)).status_code == 200