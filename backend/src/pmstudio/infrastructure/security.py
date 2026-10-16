"""密码哈希的**异步落点**（高并发 P1）。

**为什么要异步**：argon2 是 CPU 密集且**故意慢**的（OWASP 口径，实测约 80ms/次）。
原先 `verify_password` 是同步函数，被 `AuthenticateUser.__call__` 直接调用——
于是它跑在事件循环里。实测后果（`docs/plans/2026-10-16-高并发与生产级防护.md` §0）：
并发从 1 涨到 50，吞吐恒定在 13 req/s 不动，p50 从 81ms 涨到 2354ms；
**登录期间整个事件循环被占住，SSE 推送与其他所有请求一起停摆**。
并发不换吞吐，只换延迟。

**为什么要隔离舱**：`asyncio.to_thread` 把哈希挪出事件循环后，不加限制的话
N 个并发登录会同时开 N 个 argon2。argon2 LOW_MEMORY 每次约 64MB 内存，
200 并发就是 12GB——把机器打死。所以同时进行的哈希数要封顶：
超过上限的**排队**，而不是一起跑。并发上限同时也是内存上限。

**为什么不自己写参数**：`_hasher` 用 argon2 默认参数（RFC 9106 LOW_MEMORY，
已超 OWASP 下限），并发上限同理——给一个默认、可用环境变量拧的旋钮，不自创安全参数。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # 默认即 RFC 9106 LOW_MEMORY，已超 OWASP 下限，不自创参数

#: 同时进行的密码哈希数上限。默认 `min(4, CPU 数)`：
#: 4 个并发 argon2 约占 256MB 内存；再多对单事件循环里的登录吞吐没有额外收益，
#: 只会挤占内存与 CPU。
DEFAULT_CONCURRENCY = max(1, min(4, (os.cpu_count() or 1)))

_gate: asyncio.Semaphore | None = None
_gate_limit: int = DEFAULT_CONCURRENCY


def configure_gate(concurrency: int) -> None:
    """设定隔离舱容量。**必须在事件循环跑起来之前调**（装配点做）。

    换容量时重建信号量：已经持有许可的协程不受影响，新容量对后续调用生效。
    """

    global _gate, _gate_limit
    _gate_limit = max(1, concurrency)
    _gate = None


def gate_limit() -> int:
    return _gate_limit


def _semaphore() -> asyncio.Semaphore:
    """懒建信号量：模块导入时还没有事件循环，不能在那里建。"""

    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(_gate_limit)
    return _gate


def _gate_in_thread(work: Callable[[], object]) -> object:
    """在信号量许可内跑同步工作（同步函数，供 `to_thread` 之前已在许可内的情况）。"""

    return work()


async def _guarded(work: Callable[[], object]) -> object:
    async with _semaphore():
        return await asyncio.to_thread(work)


def hash_password(password: str) -> str:
    """同步版（**只给启动期与同步场景**：构造哨兵哈希、脚本、测试）。"""

    return _hasher.hash(password)


def verify_password(hash: str, password: str) -> bool:
    """同步版（同上）。"""

    try:
        return _hasher.verify(hash, password)
    except (VerificationError, InvalidHashError):
        return False


async def hash_password_async(password: str) -> str:
    """哈希密码：离开事件循环，并受隔离舱限制。"""

    return str(await _guarded(lambda: _hasher.hash(password)))


async def verify_password_async(hash: str, password: str) -> bool:
    """校验密码：离开事件循环，并受隔离舱限制。

    `InvalidHashError` 与 `VerificationError` 都在这里吞成 `False`——和同步版同一口径：
    查不到就说不通过，不把"哈希坏了"当成一个能透出去的技术细节。
    """

    def work() -> bool:
        try:
            return _hasher.verify(hash, password)
        except (VerificationError, InvalidHashError):
            return False

    return bool(await _guarded(work))