import asyncio
import time

from pmstudio.infrastructure.security import (
    DEFAULT_CONCURRENCY,
    _guarded,
    configure_gate,
    gate_limit,
    hash_password,
    verify_password,
    verify_password_async,
)


def test_round_trip():
    hashed = hash_password("s3cret-密码")
    assert hashed != "s3cret-密码"
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "s3cret-密码") is True


def test_wrong_password_is_false_not_raise():
    hashed = hash_password("s3cret-密码")
    assert verify_password(hashed, "错的") is False


def test_same_password_hashes_differ():
    assert hash_password("x") != hash_password("x")


def test_empty_hash_is_false_not_raise():
    assert verify_password("", "x") is False


def test_garbage_hash_is_false_not_raise():
    assert verify_password("not-a-argon2-hash", "x") is False


# ── 高并发 P1：异步落点与隔离舱 ────────────────────────────────────────────
# 判据不是"哈希还准不准"（那是老用例），而是**并发时事件循环还动不动**。


async def test_verify_async_returns_the_same_verdict_as_sync():
    h = hash_password("s3cret-密码")
    assert await verify_password_async(h, "s3cret-密码") is True
    assert await verify_password_async(h, "wrong") is False


async def test_verify_async_swallows_a_corrupt_hash():
    """查不到就说不通过——和同步版同一口径，不把"哈希坏了"透出去。"""

    assert await verify_password_async("not-a-real-hash", "x") is False


async def test_gate_caps_concurrent_hashes():
    """隔离舱：同时进行的哈希数不超过上限，多出来的排队。"""

    configure_gate(2)
    try:
        assert gate_limit() == 2
        running = 0
        peak = 0

        def slow(_: int) -> None:
            time.sleep(0.05)

        # 计数必须发生在**闸门内部**（也就是真正占 CPU 的那段），
        # 在闸门外数的话量到的是"已启动的调用方"，恒等于并发数——那什么都证明不了。
        def work() -> None:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            try:
                slow(0)
            finally:
                running -= 1

        await asyncio.gather(*[_guarded(work) for _ in range(8)])
        assert peak <= 2, f"峰值 {peak} 超过上限 2——隔离舱没生效"
    finally:
        configure_gate(DEFAULT_CONCURRENCY)


async def test_gate_never_blocks_the_event_loop():
    """真正的判据：哈希在飞的时候，事件循环还能跑别的东西。

    同步版做不到这件事——它跑在循环里，别的协程根本没机会被调度。
    """

    configure_gate(4)
    try:
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            for _ in range(20):
                await asyncio.sleep(0.005)
                ticks += 1

        await asyncio.gather(
            ticker(),
            *(verify_password_async(hash_password("pw"), "pw") for _ in range(4)),
        )
        assert ticks >= 15, f"事件循环只被调度了 {ticks} 次——哈希把它堵住了"
    finally:
        configure_gate(DEFAULT_CONCURRENCY)
