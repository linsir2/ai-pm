"""登录防爆计数器的容量上限（多用户改造终审 M-4）。

海量不同用户名灌进来不能把内存撑爆：`_failures` 键数必须有上界（超容量按最旧淘汰）。
"""

from datetime import UTC, datetime

from pmstudio.application.use_cases import LoginThrottle

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_failures_do_not_grow_without_bound():
    throttle = LoginThrottle(max_fails=5, lock_minutes=15, max_keys=3)

    for index in range(100):
        throttle.record(now=NOW, key=f"ip|user-{index}")

    assert len(throttle._failures) <= 3
    assert "ip|user-99" in throttle._failures


def test_locked_key_still_works_within_capacity():
    throttle = LoginThrottle(max_fails=2, lock_minutes=15, max_keys=8)

    throttle.record(now=NOW, key="ip|alice")
    throttle.record(now=NOW, key="ip|alice")

    assert throttle.locked(now=NOW, key="ip|alice") is True
    assert throttle.locked(now=NOW, key="ip|bob") is False
