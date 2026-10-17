"""登录限流闸：内存实现 + 三维度的行为。

**这一份是 Redis 版的地基**：Redis 掉线时降级到的就是它。两份实现的
行为必须逐条对齐——差异就是"生产上会限、测试里限不住"的裂缝。

三维度的判据（为什么是三个，不是凭感觉加的）：

| 攻击形状 | 失败锁 `(ip,user)` | per-IP 尝试 | per-user 尝试 |
|---|---|---|---|
| 单 IP 撞单账号 | 挡 | — | — |
| 单 IP 喷 10000 个账号 | **不挡**（每个用户名各错 1 次） | 挡 | — |
| 1000 个 IP 撞单账号 | **不挡**（每个 IP 各错 1 次） | — | 挡 |
"""

from datetime import UTC, datetime, timedelta

import pytest

from pmstudio.application.ports import LoginVerdict
from pmstudio.application.use_cases import LoginThrottle

NOW = datetime(2026, 10, 17, 12, 0, 0, tzinfo=UTC)


class _Clock:
    """可控时钟。限流的时间语义必须能被测试拨动，否则测不了窗口边界。"""

    def __init__(self, start: datetime = NOW) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now = self._now + timedelta(**kwargs)


def _guard(
    *, max_fails: int = 5, lock_minutes: int = 15, attempt_window: int = 60,
    ip_max: int = 20, user_max: int = 10,
) -> tuple[LoginThrottle, _Clock]:
    clock = _Clock()
    return (
        LoginThrottle(
            max_fails=max_fails,
            lock_minutes=lock_minutes,
            attempt_window_seconds=attempt_window,
            ip_max_attempts=ip_max,
            username_max_attempts=user_max,
            clock=clock,
        ),
        clock,
    )


# ── 失败锁维度 ───────────────────────────────────────────────────────────────


async def test_失败到阈值才锁():
    """R：窗口内失败到阈值才锁；P：记 max_fails 次失败；R：第 max_fails+1 次判定为锁。"""

    guard, _ = _guard(max_fails=3)

    for _ in range(3):
        await guard.record_failure(ip="1.1.1.1", username="alice")

    verdict = await guard.locked(ip="1.1.1.1", username="alice")
    assert verdict.limited is True
    assert verdict.retry_after > 0


async def test_没到阈值不锁():
    guard, _ = _guard(max_fails=3)

    for _ in range(2):
        await guard.record_failure(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is False


async def test_登录成功清掉失败计数():
    """R：手滑错两次然后登录成功，不该被锁；P：错 2 次再 clear；R：不锁。"""

    guard, _ = _guard(max_fails=3)
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.clear(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is False


async def test_过了锁窗口自动解锁():
    """R：锁是有时限的；P：记满后时钟推过 lock_minutes；R：不再锁。"""

    guard, clock = _guard(max_fails=2, lock_minutes=15)
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.record_failure(ip="1.1.1.1", username="alice")
    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is True

    clock.advance(minutes=16)

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is False


async def test_锁按_ip_与用户名_分键():
    """R：alice 被锁不影响 bob（现状语义，不许退化成全局闸）。"""

    guard, _ = _guard(max_fails=2)
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.record_failure(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="1.1.1.1", username="alice")).limited is True
    assert (await guard.locked(ip="1.1.1.1", username="bob")).limited is False


async def test_换_ip_不受另一个_ip_的锁影响():
    guard, _ = _guard(max_fails=2)
    await guard.record_failure(ip="1.1.1.1", username="alice")
    await guard.record_failure(ip="1.1.1.1", username="alice")

    assert (await guard.locked(ip="2.2.2.2", username="alice")).limited is False


# ── 尝试维度 ────────────────────────────────────────────────────────────────


async def test_per_ip_尝试到上限才限():
    """R：单 IP 喷很多账号是失败锁挡不住的，只有尝试闸能挡。"""

    guard, _ = _guard(ip_max=3)

    for _ in range(3):
        assert (await guard.consume_attempt(ip="1.1.1.1", username="x")).limited is False

    assert (await guard.consume_attempt(ip="1.1.1.1", username="y")).limited is True


async def test_per_username_尝试到上限才限():
    """R：很多 IP 撞一个账号，靠 per-username 闸挡。"""

    guard, _ = _guard(user_max=3)

    for index in range(3):
        verdict = await guard.consume_attempt(ip=f"9.9.9.{index}", username="alice")
        assert verdict.limited is False

    assert (await guard.consume_attempt(ip="9.9.9.9", username="alice")).limited is True


async def test_两个尝试维度互相独立():
    """R：per-IP 打满不许连带把 per-username 也打满。"""

    guard, _ = _guard(ip_max=2, user_max=100)

    await guard.consume_attempt(ip="1.1.1.1", username="alice")
    await guard.consume_attempt(ip="1.1.1.1", username="alice")

    # 同一个用户名，换 IP：per-user 维度只被扣了 2 次，还很富余
    verdict = await guard.consume_attempt(ip="2.2.2.2", username="alice")
    assert verdict.limited is False


async def test_尝试过了窗口自动恢复():
    """R：限流要有时限，不能一次触发就永久封。"""

    guard, clock = _guard(ip_max=2, attempt_window=60)
    await guard.consume_attempt(ip="1.1.1.1", username="a")
    await guard.consume_attempt(ip="1.1.1.1", username="a")
    assert (await guard.consume_attempt(ip="1.1.1.1", username="a")).limited is True

    clock.advance(seconds=61)

    assert (await guard.consume_attempt(ip="1.1.1.1", username="a")).limited is False


async def test_尝试计数_成功登录_不清零():
    """R：**只有失败锁会被成功清掉**。尝试闸要靠自然过期恢复——
    否则攻击者只要偶尔猜对一次就能把计数洗白。"""

    guard, _ = _guard(ip_max=3)
    await guard.consume_attempt(ip="1.1.1.1", username="alice")
    await guard.consume_attempt(ip="1.1.1.1", username="alice")

    await guard.clear(ip="1.1.1.1", username="alice")

    await guard.consume_attempt(ip="1.1.1.1", username="alice")
    assert (await guard.consume_attempt(ip="1.1.1.1", username="alice")).limited is True


async def test_超限时给出正整数_RetryAfter():
    """R：客户端要知道等多久；空/0/负数等于让它硬重试。"""

    guard, _ = _guard(ip_max=1)
    await guard.consume_attempt(ip="1.1.1.1", username="a")

    verdict = await guard.consume_attempt(ip="1.1.1.1", username="b")

    assert verdict.limited is True
    assert isinstance(verdict.retry_after, int)
    assert verdict.retry_after > 0


# ── 键注入 ──────────────────────────────────────────────────────────────────


async def test_拼接键会撞车_所以不许拼():
    """R：`f"{ip}|{username}"` 拼出来的键有歧义：
    `(ip="a|b", user="c")` 与 `(ip="a", user="b|c")` 会变成同一个键，
    攻击者能用用户名里的 `|` 撞掉别人的限额。P：两边各记满；R：互不影响。

    维度拆成结构化参数后这个键由实现自己构造，调用方不再拼字符串。
    """

    guard, _ = _guard(max_fails=2)
    await guard.record_failure(ip="a|b", username="c")
    await guard.record_failure(ip="a|b", username="c")

    assert (await guard.locked(ip="a|b", username="c")).limited is True
    assert (await guard.locked(ip="a", username="b|c")).limited is False


# ── 内存上界 ────────────────────────────────────────────────────────────────


async def test_键数有上界_攻击者灌不爆内存():
    """R：攻击者用海量不同用户名灌内存，不能把 `_failures` 撑爆。
    P：记 1500 个不同用户名的失败（默认上界 1024）；R：表大小 ≤ 上界。"""

    guard, _ = _guard(max_fails=1000)
    for index in range(1500):
        await guard.record_failure(ip="1.1.1.1", username=f"u{index}")

    assert guard.entry_count <= guard.max_keys


async def test_空_username_也算一次尝试():
    """R：未登录/被摘 cookie 的请求会带空 username，不能让它绕过 per-IP 闸。"""

    guard, _ = _guard(ip_max=2)
    await guard.consume_attempt(ip="1.1.1.1", username="")
    await guard.consume_attempt(ip="1.1.1.1", username="")

    assert (await guard.consume_attempt(ip="1.1.1.1", username="")).limited is True


async def test_空_ip_退化成按用户名限():
    """R：拿不到 IP（本地直连等）时仍要有闸门，否则退化成不限流。"""

    guard, _ = _guard(user_max=2)
    await guard.consume_attempt(ip="", username="alice")

    assert (await guard.consume_attempt(ip="", username="alice")).limited is False
    assert (await guard.consume_attempt(ip="", username="alice")).limited is True


@pytest.mark.parametrize(
    "field",
    ["max_fails", "lock_minutes", "ip_max_attempts", "username_max_attempts"],
)
def test_配了零或负数直接报错(field: str):
    """R：0 会让"超限"判断永远不成立——限流器静默失效是最坏的失效方式。"""

    kwargs = {
        "max_fails": 5, "lock_minutes": 15, "attempt_window_seconds": 60,
        "ip_max_attempts": 20, "username_max_attempts": 10, "clock": _Clock(),
    }
    kwargs[field] = 0

    with pytest.raises(ValueError, match=field):
        LoginThrottle(**kwargs)


async def test_判定结果就是契约里的那个类型():
    """R：两份实现必须能互换；P：任意判定；R：类型是 LoginVerdict。"""

    guard, _ = _guard()
    assert isinstance(await guard.locked(ip="1.1.1.1", username="a"), LoginVerdict)
    assert isinstance(await guard.consume_attempt(ip="1.1.1.1", username="a"), LoginVerdict)
