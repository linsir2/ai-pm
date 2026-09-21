"""M26 重试与降级：包装 M22，失败时按策略重试；最终失败发 generation.failed。"""

import asyncio
import os
from datetime import UTC, datetime

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    EventType,
    ProducerIdentity,
    PromptRole,
    RegionName,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.harness.model_gateway import GenerationFailure
from pmstudio.harness.retry import RetryingHarness

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


class _Clock:
    def now(self) -> datetime:
        return AT


class _Recorder:
    """记录所有发布的事件。"""

    def __init__(self) -> None:
        self.published: list = []

    async def publish(self, event: object, *, producer: object) -> None:
        self.published.append((event, producer))


class _FakeBoard:
    """只读黑板替身：返回预设的当前轮。"""

    def __init__(self, current: RoundRegion | None) -> None:
        self._current = current

    async def read(self, region: RegionName) -> object:
        if region is RegionName.ROUND:
            return self._current
        return None


class _FakeTrimmer:
    """M24 替身：直通不裁。"""

    async def trim(self, blocks: object, project_id: str, model_ref: str) -> object:
        class _Result:
            blocks = ()
            dropped = ()

        return _Result()


class _FailGateway:
    """前 N 次失败，之后成功的 ModelGateway 替身。"""

    def __init__(self, fail_times: int, retryable: bool = True) -> None:
        self._fail_times = fail_times
        self._retryable = retryable
        self.attempts = 0

    async def complete(
        self, model_ref: str, messages: list[PromptMessage], response_format: dict | None = None,
    ) -> str:
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise GenerationFailure(
                f"第 {self.attempts} 次失败", retryable=self._retryable
            )
        return "成功文本"


def _round_region(phase: RoundPhase) -> RoundRegion:
    return RoundRegion(
        round_id="rnd_test",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input="细化功能清单",
        scope=Scope(),
        phase=phase,
    )


def _messages() -> list[PromptMessage]:
    return [PromptMessage(role=PromptRole.USER, text="测试")]


@pytest.fixture
def bus() -> _Recorder:
    return _Recorder()


def test_succeeds_first_try_no_event(bus: _Recorder) -> None:
    """一次成功 → 无事件、返回文本。"""
    gateway = _FailGateway(fail_times=0)
    harness = RetryingHarness(
        gateway=gateway,
        board=_FakeBoard(_round_region(RoundPhase.RESTATING)),
        bus=bus,
        clock=_Clock(),
        trimmer=_FakeTrimmer(),
    )
    result = asyncio.run(harness.complete("model_ref", _messages()))
    assert result == "成功文本"
    assert bus.published == []


def test_retries_then_succeeds(bus: _Recorder) -> None:
    """第 2 次成功 → 返回文本，无失败事件。"""
    gateway = _FailGateway(fail_times=1)
    harness = RetryingHarness(
        gateway=gateway,
        board=_FakeBoard(_round_region(RoundPhase.RESTATING)),
        bus=bus,
        clock=_Clock(),
        trimmer=_FakeTrimmer(),
    )
    result = asyncio.run(harness.complete("model_ref", _messages()))
    assert result == "成功文本"
    assert gateway.attempts == 2
    assert bus.published == []


def test_gives_up_after_max_attempts_and_reports_the_round(bus: _Recorder) -> None:
    """超过最大重试次数 → 发 generation.failed。"""
    gateway = _FailGateway(fail_times=99)
    harness = RetryingHarness(
        gateway=gateway,
        board=_FakeBoard(_round_region(RoundPhase.RESTATING)),
        bus=bus,
        clock=_Clock(),
        trimmer=_FakeTrimmer(),
        max_attempts=3,
    )
    with pytest.raises(GenerationFailure):
        asyncio.run(harness.complete("model_ref", _messages()))

    assert gateway.attempts == 3
    assert len(bus.published) == 1
    event, producer = bus.published[0]
    assert event.type is EventType.GENERATION_FAILED
    assert producer is ProducerIdentity.HARNESS
    payload = event.payload
    assert payload.round_id == "rnd_test"
    assert payload.step is RoundPhase.RESTATING
    assert payload.retryable is True


def test_auth_error_is_not_retried(bus: _Recorder) -> None:
    """鉴权错误不重试。"""
    gateway = _FailGateway(fail_times=99, retryable=False)
    harness = RetryingHarness(
        gateway=gateway,
        board=_FakeBoard(_round_region(RoundPhase.RESTATING)),
        bus=bus,
        clock=_Clock(),
        trimmer=_FakeTrimmer(),
        max_attempts=3,
    )
    with pytest.raises(GenerationFailure):
        asyncio.run(harness.complete("model_ref", _messages()))

    assert gateway.attempts == 1
    assert len(bus.published) == 1
    event, _ = bus.published[0]
    assert event.payload.retryable is False


def test_no_current_round_means_no_event(bus: _Recorder) -> None:
    """不在轮里失败 → 不发事件、直接抛。"""
    gateway = _FailGateway(fail_times=99)
    harness = RetryingHarness(
        gateway=gateway,
        board=_FakeBoard(None),
        bus=bus,
        clock=_Clock(),
        trimmer=_FakeTrimmer(),
        max_attempts=1,
    )
    with pytest.raises(ContractViolation):
        asyncio.run(harness.complete("model_ref", _messages()))

    assert bus.published == []
