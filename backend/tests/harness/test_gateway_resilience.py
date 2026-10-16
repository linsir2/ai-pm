"""网关热路径上的韧性（高并发 P2）。

**判据不是"类存在"，而是"调用真的经过它"**——所以这里打桩 `acompletion` 数次数，
断言熔断打开后**一次网络请求都不再发**（这正是熔断的意义：不做无用功）。
"""

import asyncio

import pytest

from pmstudio.harness.model.contracts import ModelMessage, ModelRequest
from pmstudio.harness.model.providers import litellm as provider
from pmstudio.harness.model.providers.litellm import ModelCallFailed
from pmstudio.harness.model.resilience import (
    BreakerConfig,
    BulkheadConfig,
    RetryBudgetConfig,
)


class _Timeout(Exception):
    """供应商超时。用真实的超时异常类型，不自造一个 classify 认不出的类。"""

    def __init__(self) -> None:
        super().__init__("litellm.Timeout: APITimeoutError - Request timed out")


def _request() -> ModelRequest:
    return ModelRequest(
        purpose="integration",
        messages=(ModelMessage(role="user", content="写一句"),),
        schema_name="reply",
        response_schema={"type": "object"},
    )


def _gateway(monkeypatch, *, ok: bool, calls: list[int], **kwargs):
    """造一个网关，`acompletion` 被换成计数替身。"""

    async def fake(**_: object):
        calls.append(1)
        if not ok:
            raise _Timeout()
        return _fake_response()

    monkeypatch.setattr(provider, "acompletion", fake)
    return provider.LiteLLMGateway(
        model="m",
        api_key="k",
        max_attempts=kwargs.pop("max_attempts", 1),
        sleeper=_record_sleeper([]),
        **kwargs,
    )


def _record_sleeper(into: list[float]):
    """记下每次退避请求的秒数。**不要写成 `lambda s: asyncio.sleep(0) or into.append(s)`**
    ——`asyncio.sleep(0)` 返回的协程是 truthy，`or` 短路，append 永远不执行。"""

    async def sleeper(seconds: float) -> None:
        into.append(seconds)

    return sleeper


def _fake_response():
    class _Msg:
        content = '{"ok": true}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    return _Resp()


async def test_熔断打开后不再发任何上游请求(monkeypatch):
    """这是熔断的全部意义：坏的时候**一个包都不发**。"""

    calls: list[int] = []
    gateway = _gateway(
        monkeypatch,
        ok=False,
        calls=calls,
        breaker_config=BreakerConfig(minimum_samples=2, recovery_timeout=60),
        retry_budget_config=RetryBudgetConfig(burst=10, ratio=1.0, refill_per_second=0),
    )

    with pytest.raises(ModelCallFailed):
        await gateway.complete(_request())
    with pytest.raises(ModelCallFailed):
        await gateway.complete(_request())
    before = len(calls)
    assert before >= 2, "前两次请求应当真的发出去了"

    # 熔断已开（2 次失败 ≥ 最小样本 2）
    with pytest.raises(ModelCallFailed):
        await gateway.complete(_request())

    assert len(calls) == before, f"熔断后又发了 {len(calls) - before} 个请求——闸门没起作用"


async def test_隔离舱满时快速失败而不是继续堆(monkeypatch):
    calls: list[int] = []
    gateway = _gateway(
        monkeypatch,
        ok=False,
        calls=calls,
        bulkhead_config=BulkheadConfig(capacity=1, queue_wait_seconds=0.01),
        breaker_config=BreakerConfig(minimum_samples=1000),  # 别让熔断干扰这一条
        retry_budget_config=RetryBudgetConfig(burst=0.0, ratio=0.0, refill_per_second=0),
    )

    async def slow(**_: object):
        calls.append(1)
        await asyncio.sleep(0.3)
        raise _Timeout()

    monkeypatch.setattr(provider, "acompletion", slow)

    results = await asyncio.gather(
        *(gateway.complete(_request()) for _ in range(4)),
        return_exceptions=True,
    )
    # 有请求因为舱满而快速失败（而不是 4 个都排 0.3 秒队）
    assert any(isinstance(r, ModelCallFailed) for r in results)
    assert len(calls) <= 2, f"发了 {len(calls)} 个上游请求，超过容量 1 的预期"


async def test_重试受预算约束(monkeypatch):
    """预算耗尽 → 不再重试 → 请求数被钉死。"""

    calls: list[int] = []
    gateway = _gateway(
        monkeypatch,
        ok=False,
        calls=calls,
        max_attempts=3,
        retry_budget_config=RetryBudgetConfig(burst=0.0, ratio=0.0, refill_per_second=0),
        breaker_config=BreakerConfig(minimum_samples=1000),
    )

    with pytest.raises(ModelCallFailed):
        await gateway.complete(_request())

    assert len(calls) == 1, f"预算为 0 却重试了（发了 {len(calls)} 次）"


async def test_成功路径不受影响(monkeypatch):
    """韧性层是透明���：正常调用该成功还是成功，且只发一次。"""

    calls: list[int] = []
    gateway = _gateway(monkeypatch, ok=True, calls=calls)

    await gateway.complete(_request())

    assert len(calls) == 1


async def test_退避带抖动(monkeypatch):
    """退避不是固定值——固定退避会让所有调用方同一刻重试（重试风暴）。"""

    waits: list[float] = []
    calls: list[int] = []

    async def fake(**_: object):
        calls.append(1)
        raise _Timeout()

    monkeypatch.setattr(provider, "acompletion", fake)
    gateway = provider.LiteLLMGateway(
        model="m",
        api_key="k",
        max_attempts=2,
        backoff=(1.0,),
        sleeper=_record_sleeper(waits),
        retry_budget_config=RetryBudgetConfig(burst=10, ratio=1.0, refill_per_second=0),
        breaker_config=BreakerConfig(minimum_samples=1000),
    )

    for _ in range(20):
        with pytest.raises(ModelCallFailed):
            await gateway.complete(_request())

    assert len(waits) >= 10
    assert len(set(round(w, 4) for w in waits)) > 1, "退避是固定值——这会制造重试风暴"

# ── 过载卸载要回 503 + Retry-After，而不是 502 ─────────────────────────────────


def test_舱满_回_503_带_RetryAfter():
    """**卸载不是上游失败**：上游可能好着，是我们没让它发出去。
    回 502 会让客户端去重试上游——那正是把刚卸掉的负载又打回来的做法。"""

    from pmstudio.api.errors import status_for
    from pmstudio.harness.errors import ModelShed

    shed = ModelShed("模型并发已满", purpose="integration", retry_after=5, capacity=4)

    assert status_for(shed) == 503
    assert shed.retry_after == 5


def test_上游真失败_仍然回_502():
    """别把两件事混成一个码：上游超时是 502（重试上游），卸载是 503（等一会再来）。"""

    from pmstudio.api.errors import status_for
    from pmstudio.harness.errors import ModelCallFailed

    assert status_for(ModelCallFailed("超时", purpose="integration")) == 502


async def test_舱满时抛的是_ModelShed_而不是_ModelCallFailed(monkeypatch):
    from pmstudio.harness.errors import ModelShed

    calls: list[int] = []
    gateway = _gateway(
        monkeypatch,
        ok=False,
        calls=calls,
        bulkhead_config=BulkheadConfig(capacity=1, queue_wait_seconds=0.01),
        breaker_config=BreakerConfig(minimum_samples=1000),
        retry_budget_config=RetryBudgetConfig(burst=0.0, ratio=0.0, refill_per_second=0),
    )

    async def slow(**_: object):
        calls.append(1)
        await asyncio.sleep(0.3)
        raise _Timeout()

    monkeypatch.setattr(provider, "acompletion", slow)

    results = await asyncio.gather(
        *(gateway.complete(_request()) for _ in range(4)), return_exceptions=True
    )
    shed = [r for r in results if isinstance(r, ModelShed)]

    assert shed, f"没有舱满的 ModelShed，得到 {[type(r).__name__ for r in results]}"
    assert shed[0].retry_after >= 1
    assert shed[0].details.get("capacity") == 1


async def test_熔断开闸时抛的是_ModelShed(monkeypatch):
    from pmstudio.harness.errors import ModelShed

    calls: list[int] = []
    gateway = _gateway(
        monkeypatch,
        ok=False,
        calls=calls,
        breaker_config=BreakerConfig(minimum_samples=1, recovery_timeout=45),
        retry_budget_config=RetryBudgetConfig(burst=0.0, ratio=0.0, refill_per_second=0),
    )
    with pytest.raises(ModelCallFailed):
        await gateway.complete(_request())

    with pytest.raises(ModelShed) as caught:
        await gateway.complete(_request())

    # Retry-After 就是熔断的恢复窗口：客户端照这个数等，别硬重试
    assert caught.value.retry_after == 45
