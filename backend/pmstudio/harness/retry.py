"""M26 重试与降级：包装 M22，失败时按策略重试；最终失败发 generation.failed。

它实现了 HarnessPort（complete + trim），其余方法暂不实现（M23/M25 留到对应里程碑）。
"""

from collections.abc import Sequence

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import EventType, ProducerIdentity, RegionName
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.results import TrimResult
from pmstudio.contracts.skeleton.board import ContextBlock
from pmstudio.contracts.skeleton.events import Event, GenerationFailedPayload
from pmstudio.harness.model_gateway import GenerationFailure


class RetryingHarness:
    """M26：重试包装 + 失败上报。

    实现了 HarnessPort 的 `complete` 和 `trim`；其余方法在对应里程碑落地。
    """

    def __init__(
        self,
        gateway: object,
        board: object,
        bus: object,
        clock: object,
        trimmer: object,
        max_attempts: int = 3,
    ) -> None:
        self._gateway = gateway
        self._board = board
        self._bus = bus
        self._clock = clock
        self._trimmer = trimmer
        self._max_attempts = max_attempts

    async def complete(self, model_ref: str, messages: Sequence[PromptMessage]) -> str:
        """带重试的模型调用。"""
        last_error: GenerationFailure | None = None

        for attempt in range(self._max_attempts):
            try:
                return await self._gateway.complete(model_ref, messages)  # type: ignore[attr-defined]
            except GenerationFailure as error:
                last_error = error
                if not error.retryable:
                    break

        # 最终失败 → 上报
        assert last_error is not None
        await self._report_failure(last_error)
        raise last_error

    async def _report_failure(self, error: "GenerationFailure") -> None:
        """发 generation.failed 事件。不在轮里则只抛不发。"""
        current = await self._board.read(RegionName.ROUND)  # type: ignore[attr-defined]
        if current is None:
            raise ContractViolation(
                "调模型必须在一轮里 — 没有当前轮，失败无法上报"
            )

        await self._bus.publish(  # type: ignore[attr-defined]
            Event(
                type=EventType.GENERATION_FAILED,
                payload=GenerationFailedPayload(
                    round_id=current.round_id,
                    step=current.phase,
                    reason=str(error),
                    retryable=error.retryable,
                ),
                at=self._clock.now(),  # type: ignore[attr-defined]
            ),
            producer=ProducerIdentity.HARNESS,
        )

    async def trim(
        self,
        blocks: Sequence[ContextBlock],
        project_id: str,
        model_ref: str,
    ) -> TrimResult:
        """委托给内部的 Trimmer。"""
        return await self._trimmer.trim(blocks, project_id, model_ref)  # type: ignore[attr-defined]
