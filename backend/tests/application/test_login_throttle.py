"""登录防爆计数器的容量上限（多用户改造终审 M-4）。

海量不同用户名灌进来不能把内存撑爆：三个表的键数都��须有上界（超容量按最旧淘汰）。
行为语义（三维限流）另见 `test_login_guard.py`——这里只管**内存上界**。
"""

from datetime import UTC, datetime, timedelta

from pmstudio.application.use_cases import LoginThrottle

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self._now = NOW

    def now(self) -> datetime:
        return self._now


def _throttle(*, max_keys: int, max_fails: int = 5, **kwargs: object) -> LoginThrottle:
    return LoginThrottle(
        max_fails=max_fails,
        lock_minutes=15,
        attempt_window_seconds=60,
        ip_max_attempts=20,
        username_max_attempts=10,
        clock=_Clock(),
        max_keys=max_keys,
        **kwargs,  # type: ignore[arg-type]
    )


async def test_failures_do_not_grow_without_bound():
    throttle = _throttle(max_keys=3)

    for index in range(100):
        await throttle.record_failure(ip="ip", username=f"user-{index}")

    assert len(throttle._failures) <= 3


async def test_尝试表也不涨():
    """速率表同样会被海量 IP/用户名灌——**两个表都要有上界**，
    只给失败表加上界等于留了个一样的洞。"""

    throttle = _throttle(max_keys=4)

    for index in range(100):
        await throttle.consume_attempt(ip=f"ip-{index}", username="same")

    assert len(throttle._ip_attempts) <= 4
    assert len(throttle._user_attempts) <= 1


async def test_被淘汰的键不会把新键也挤掉():
    """淘汰按最早入表（最旧）——不是随机、不是新键。"""

    throttle = _throttle(max_keys=2)
    await throttle.record_failure(ip="1", username="a")
    await throttle.record_failure(ip="2", username="b")
    await throttle.record_failure(ip="3", username="c")

    assert "3" in {key[0] for key in throttle._failures}
    assert "1" not in {key[0] for key in throttle._failures}


async def test_超容量时最新键照样能锁上():
    """被淘汰的是**别人**，不是新来的那个——否则攻击者灌一批用户名
    就能把管理员自己的失败计数挤掉，等于给自己解锁。"""

    throttle = _throttle(max_keys=2, max_fails=1)
    for index in range(50):
        await throttle.record_failure(ip="filler", username=f"u{index}")

    await throttle.record_failure(ip="victim", username="admin")

    assert (await throttle.locked(ip="victim", username="admin")).limited is True


async def test_窗口外的条目不占容量():
    """过期条目应被剪掉，不该让表一直满。"""

    clock = _Clock()
    throttle = LoginThrottle(
        max_fails=5,
        lock_minutes=15,
        attempt_window_seconds=60,
        ip_max_attempts=20,
        username_max_attempts=10,
        clock=clock,
        max_keys=3,
    )
    for index in range(3):
        await throttle.record_failure(ip=f"ip{index}", username="a")
    assert len(throttle._failures) == 3

    clock._now = NOW + timedelta(minutes=16)
    for index in range(10, 13):
        await throttle.record_failure(ip=f"ip{index}", username="a")

    assert len(throttle._failures) == 3, "剪枝没跑，旧条目还占着容量"