"""认证加固回归（多用户改造 Task 9 · `§0.10`）：爆破锁定 + 恒定耗时。

真 PostgreSQL + 真容器 + HTTP；argon2 **真跑**（不 monkeypatch `verify_password`），
计时才有意义。锁定与恒定耗时都要求"真触发"，不接受假证明。
"""

import time

from pmstudio.bootstrap.settings import LOGIN_MAX_FAILS
from tests.support.api_client import async_client

PW = "alice-s3cret"

#: 锁定阈值**取自真实配置**（`Settings.login_max_fails` 的默认源），不硬编码——
#: 改默认值/环境变量时测试自动跟随，防"测试写死 5、实现漂到别的值"的错位。
#: `make_app` 不覆写 `login_max_fails`，故这里读到的默认即本测试引擎的实际阈值。
MAX_FAILS = LOGIN_MAX_FAILS


def _seed_active_user(app, *, username: str, password: str, role: str = "user"):
    from pmstudio.infrastructure.security import hash_password

    users = app.state.auth.users
    user_id = users.register(
        username=username, password_hash=hash_password(password), role=role
    )
    users.set_status(user_id, "active")
    return user_id


async def _login(client, username: str, password: str):  # type: ignore[no-untyped-def]
    return await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )


async def test_lockout_after_max_fails_blocks_correct_password(make_app):
    """窗口内连败到阈值即锁：之后连**正确密码**也 401（锁优先于密码校验）。

    先确认正确密码能进——否则下面"被锁"分不清到底是锁生效还是密码本就打错。
    """

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        ok = await _login(client, "alice", PW)
        assert ok.status_code == 200, ok.text
        for attempt in range(MAX_FAILS):
            bad = await _login(client, "alice", "wrong-password")
            assert bad.status_code == 401, f"第 {attempt + 1} 次错密码应 401"
        locked = await _login(client, "alice", PW)
        assert locked.status_code == 401, "连败到阈值后，正确密码也应被拒"


async def test_lockout_is_keyed_per_username(make_app):
    """锁定按 `ip|用户名` 分键：alice 被锁不影响 bob（不是全局闸门）。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        _seed_active_user(app, username="bob", password="bob-s3cret")
        for _ in range(MAX_FAILS):
            assert (await _login(client, "alice", "wrong")).status_code == 401
        assert (await _login(client, "alice", PW)).status_code == 401  # alice 锁了
        assert (await _login(client, "bob", "bob-s3cret")).status_code == 200


async def test_login_failure_is_uniform_for_ghost_and_wrong_password(make_app):
    """账号枚举侧信道：不存在用户与错密码返回**同一份** 401 体（code/message/details）。"""

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        ghost = await _login(client, "ghost", "whatever")
        wrong = await _login(client, "alice", "wrong")
        assert ghost.status_code == wrong.status_code == 401
        assert ghost.json() == wrong.json()


async def test_failed_login_timing_same_order_as_nonexistent(make_app):
    """恒定耗时：不存在用户 vs「存在但密码错」耗时**同数量级**。

    两条路径都恰好跑一次 argon2 `verify`（不存在走构造时备好的 decoy 哈希），
    所以差距应远小于一个数量级。**口径放宽说明**：墙钟计时受机器负载/调度抖动影响，
    这里只要求比值落在 `[1/3, 3]`（同数量级），并配合 `test_login_failure_is_uniform…`
    与既有 `test_auth_flow.test_nonexistent_login_runs_verify_once`（verify 恰好一次）一起锁死——
    这条测的是"没有那条『查无此人就直接返回』的 0 耗时早退分支"，不是精确常数时间。
    """

    app, _ = make_app(replies={}, autologin=False)
    async with async_client(app) as client:
        _seed_active_user(app, username="alice", password=PW)
        rounds = 3  # < MAX_FAILS：两条路径各自都不会把自己锁上

        start = time.perf_counter()
        for index in range(rounds):
            await _login(client, f"ghost-{index}", "whatever")  # 不同用户名 = 不同 key
        nonexistent = time.perf_counter() - start

        start = time.perf_counter()
        for index in range(rounds):
            await _login(client, "alice", f"wrong-{index}")
        wrong_password = time.perf_counter() - start

        ratio = nonexistent / wrong_password
        assert 1 / 3 <= ratio <= 3, (
            f"不存在用户 {nonexistent * 1000:.0f}ms vs 错密码 {wrong_password * 1000:.0f}ms"
            f"（比值 {ratio:.2f}）——超出同数量级，疑似某条路径少了 verify 或多了早退"
        )
