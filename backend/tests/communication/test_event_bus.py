"""M12 事件流：订阅、发布、嵌套排队、异常隔离、流水。"""

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pmstudio.common.errors import ContractViolation, EventDispatchOverflow
from pmstudio.communication.event_bus import EventBus
from pmstudio.contracts.enums import (
    CardGroupState,
    EventType,
    MemoryStatus,
    MemoryType,
    ProducerIdentity,
    RegistryKind,
    RegistryStatus,
    RoundEntry,
    RoundPhase,
    ToolInvocationStatus,
    VersionTrigger,
)
from pmstudio.contracts.skeleton.events import (
    PRODUCERS,
    CardGroupUpdatedPayload,
    DiscussionUtteranceAddedPayload,
    DocChangedPayload,
    Event,
    GenerationFailedPayload,
    MemoryUpdatedPayload,
    RegistryUpdatedPayload,
    RoundUpdatedPayload,
    ToolInvokedPayload,
)

AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


def _payload_for(event_type: EventType) -> object:
    match event_type:
        case EventType.ROUND_UPDATED:
            return RoundUpdatedPayload(
                round_id="rnd_1",
                project_id="prj_1",
                entry=RoundEntry.MAIN,
                phase=RoundPhase.WORKING,
            )
        case EventType.CARD_GROUP_UPDATED:
            return CardGroupUpdatedPayload(
                group_id="grp_1",
                round_id="rnd_1",
                state=CardGroupState.ANSWERING,
                card_count=1,
                has_fill_card=False,
            )
        case EventType.DOC_CHANGED:
            return DocChangedPayload(
                doc_id="doc_1",
                version_id="ver_1",
                seq=1,
                trigger=VersionTrigger.SUBMIT,
                block_ids=("blk_1",),
            )
        case EventType.DISCUSSION_UTTERANCE_ADDED:
            return DiscussionUtteranceAddedPayload(
                round_id="rnd_2", role_id="reg_role_a", packet_id="pkt_1", index=0
            )
        case EventType.MEMORY_UPDATED:
            return MemoryUpdatedPayload(
                memory_id="mry_1",
                project_id="prj_1",
                type=MemoryType.DECISION,
                status=MemoryStatus.CANDIDATE,
            )
        case EventType.REGISTRY_UPDATED:
            return RegistryUpdatedPayload(id="reg_1", kind=RegistryKind.TOOL, status=RegistryStatus.ACTIVE)
        case EventType.TOOL_INVOKED:
            return ToolInvokedPayload(
                round_id="rnd_1", tool_id="reg_tool_web_search", status=ToolInvocationStatus.STARTED
            )
        case EventType.GENERATION_FAILED:
            return GenerationFailedPayload(
                round_id="rnd_1", step=RoundPhase.WORKING, reason="模型超时", retryable=True
            )
        case _:  # pragma: no cover - 枚举穷举，加类型就会在这里炸
            raise AssertionError(event_type)


def _event(event_type: EventType) -> Event:
    return Event(type=event_type, payload=_payload_for(event_type), at=AT)


def _producer_for(event_type: EventType) -> ProducerIdentity:
    return next(iter(PRODUCERS[event_type]))


class RecordingLog:
    """流水替身：只记，不落库。"""

    def __init__(self) -> None:
        self.entries: list[object] = []

    async def append(self, entry: object) -> None:
        self.entries.append(entry)


class FailingLog:
    async def append(self, entry: object) -> None:
        raise RuntimeError("磁盘满了")


def _bus(log: object, **kwargs: object) -> EventBus:
    return EventBus(log, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize("event_type", list(EventType), ids=lambda value: value.value)
def test_every_event_can_be_published_and_subscribed(event_type: EventType) -> None:
    """R0.3 的退出条件：8 个事件全部能发能订。"""

    async def scenario() -> list[Event]:
        bus = _bus(RecordingLog())
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        await bus.subscribe(event_type, handler)
        await bus.publish(_event(event_type), producer=_producer_for(event_type))
        return received

    received = asyncio.run(scenario())
    assert [event.type for event in received] == [event_type]


def test_subscribers_run_in_registration_order() -> None:
    calls: list[str] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def first(event: Event) -> None:
            calls.append("first")

        async def second(event: Event) -> None:
            calls.append("second")

        await bus.subscribe(EventType.ROUND_UPDATED, first)
        await bus.subscribe(EventType.ROUND_UPDATED, second)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)

    asyncio.run(scenario())
    assert calls == ["first", "second"]


def test_a_subscriber_only_hears_its_own_type() -> None:
    calls: list[str] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def handler(event: Event) -> None:
            calls.append(event.type.value)

        await bus.subscribe(EventType.MEMORY_UPDATED, handler)
        await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)
        await bus.publish(_event(EventType.REGISTRY_UPDATED), producer=ProducerIdentity.REGISTRY)

    asyncio.run(scenario())
    assert calls == ["memory.updated"]


def test_publishing_without_subscribers_is_fine() -> None:
    async def scenario() -> None:
        await _bus(RecordingLog()).publish(
            _event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION
        )

    asyncio.run(scenario())


def test_nested_publish_is_queued_until_the_current_handler_returns() -> None:
    order: list[str] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def on_group(event: Event) -> None:
            order.append("group-handler")

        async def on_round(event: Event) -> None:
            order.append("round-start")
            await bus.publish(_event(EventType.CARD_GROUP_UPDATED), producer=ProducerIdentity.ORCHESTRATION)
            order.append("round-end")

        await bus.subscribe(EventType.CARD_GROUP_UPDATED, on_group)
        await bus.subscribe(EventType.ROUND_UPDATED, on_round)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)

    asyncio.run(scenario())
    assert order == ["round-start", "round-end", "group-handler"]


def test_three_level_nesting_drains_in_fifo_order() -> None:
    order: list[str] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def on_registry(event: Event) -> None:
            order.append("registry")

        async def on_memory(event: Event) -> None:
            order.append("memory")
            await bus.publish(_event(EventType.REGISTRY_UPDATED), producer=ProducerIdentity.REGISTRY)

        async def on_doc(event: Event) -> None:
            order.append("doc")
            await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)

        await bus.subscribe(EventType.REGISTRY_UPDATED, on_registry)
        await bus.subscribe(EventType.MEMORY_UPDATED, on_memory)
        await bus.subscribe(EventType.DOC_CHANGED, on_doc)
        await bus.publish(_event(EventType.DOC_CHANGED), producer=ProducerIdentity.DOCUMENT_WRITER)

    asyncio.run(scenario())
    assert order == ["doc", "memory", "registry"]


def test_publish_returns_only_after_the_whole_chain_ran() -> None:
    order: list[str] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def on_round(event: Event) -> None:
            order.append("round")

        await bus.subscribe(EventType.ROUND_UPDATED, on_round)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)
        order.append("after-publish")

    asyncio.run(scenario())
    assert order == ["round", "after-publish"]


def test_a_failing_subscriber_does_not_block_the_others() -> None:
    calls: list[str] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def first(event: Event) -> None:
            calls.append("first")

        async def broken(event: Event) -> None:
            calls.append("broken")
            raise RuntimeError("前端渲染炸了")

        async def third(event: Event) -> None:
            calls.append("third")

        await bus.subscribe(EventType.ROUND_UPDATED, first)
        await bus.subscribe(EventType.ROUND_UPDATED, broken)
        await bus.subscribe(EventType.ROUND_UPDATED, third)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)

    asyncio.run(scenario())
    assert calls == ["first", "broken", "third"]


def test_isolated_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    async def scenario() -> None:
        bus = _bus(RecordingLog())

        async def broken(event: Event) -> None:
            raise RuntimeError("记忆失效判定炸了")

        await bus.subscribe(EventType.DOC_CHANGED, broken)
        await bus.publish(_event(EventType.DOC_CHANGED), producer=ProducerIdentity.DOCUMENT_WRITER)

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "doc.changed" in caplog.text
    assert "记忆失效判定炸了" in caplog.text


def test_foreign_producer_is_refused_before_anything_happens() -> None:
    """窄发布：文档写入不能替编排层发轮次事件；拒的时候什么都不该发生。"""
    log = RecordingLog()
    calls: list[str] = []

    async def scenario() -> None:
        bus = _bus(log)

        async def handler(event: Event) -> None:
            calls.append("called")

        await bus.subscribe(EventType.ROUND_UPDATED, handler)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.DOCUMENT_WRITER)

    with pytest.raises(ContractViolation):
        asyncio.run(scenario())
    assert calls == []
    assert log.entries == []


def test_payload_that_does_not_match_the_type_is_refused() -> None:
    """绕过契约校验构造出来的事件，也会在落流水那一步被拦住。"""
    forged = Event.model_construct(
        type=EventType.ROUND_UPDATED, payload=_payload_for(EventType.DOC_CHANGED), at=AT
    )

    async def scenario() -> None:
        await _bus(RecordingLog()).publish(forged, producer=ProducerIdentity.ORCHESTRATION)

    with pytest.raises(ValidationError):
        asyncio.run(scenario())


def test_every_publish_lands_in_the_log_in_order() -> None:
    log = RecordingLog()

    async def scenario() -> None:
        bus = _bus(log)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)
        await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)
        await bus.publish(_event(EventType.REGISTRY_UPDATED), producer=ProducerIdentity.REGISTRY)

    asyncio.run(scenario())
    assert [entry.type for entry in log.entries] == [
        EventType.ROUND_UPDATED,
        EventType.MEMORY_UPDATED,
        EventType.REGISTRY_UPDATED,
    ]


def test_log_rows_carry_the_locators_from_the_payload() -> None:
    log = RecordingLog()

    async def scenario() -> None:
        bus = _bus(log)
        await bus.publish(_event(EventType.CARD_GROUP_UPDATED), producer=ProducerIdentity.ORCHESTRATION)
        await bus.publish(_event(EventType.REGISTRY_UPDATED), producer=ProducerIdentity.REGISTRY)

    asyncio.run(scenario())
    group_entry, registry_entry = log.entries
    assert (group_entry.round_id, group_entry.project_id) == ("rnd_1", None)
    assert (registry_entry.round_id, registry_entry.project_id) == (None, None)
    assert group_entry.at == AT
    assert group_entry.producer is ProducerIdentity.ORCHESTRATION


def test_the_event_is_still_logged_when_a_subscriber_fails() -> None:
    """流水回答的是"这件事发了没有"——它不受订阅者成败影响。"""
    log = RecordingLog()

    async def scenario() -> None:
        bus = _bus(log)

        async def broken(event: Event) -> None:
            raise RuntimeError("炸了")

        await bus.subscribe(EventType.DOC_CHANGED, broken)
        await bus.publish(_event(EventType.DOC_CHANGED), producer=ProducerIdentity.DOCUMENT_WRITER)

    asyncio.run(scenario())
    assert len(log.entries) == 1


def test_log_failure_stops_the_dispatch() -> None:
    """留不下痕就不算发出去——宁可炸在调用方，也不静默丢事件。"""
    calls: list[str] = []

    async def scenario() -> None:
        bus = _bus(FailingLog())

        async def handler(event: Event) -> None:
            calls.append("called")

        await bus.subscribe(EventType.ROUND_UPDATED, handler)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)

    with pytest.raises(RuntimeError):
        asyncio.run(scenario())
    assert calls == []


def test_log_entries_get_unique_evt_ids() -> None:
    log = RecordingLog()

    async def scenario() -> None:
        bus = _bus(log)
        for _ in range(50):
            await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)

    asyncio.run(scenario())
    ids = [entry.event_id for entry in log.entries]
    assert all(event_id.startswith("evt_") for event_id in ids)
    assert len(set(ids)) == 50


def test_runaway_nesting_is_stopped() -> None:
    """订阅者之间形成循环时，不许把进程挂死。"""
    log = RecordingLog()

    async def scenario() -> None:
        bus = _bus(log, max_dispatch_per_publish=5)

        async def loop(event: Event) -> None:
            await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)

        await bus.subscribe(EventType.MEMORY_UPDATED, loop)
        await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)

    with pytest.raises(EventDispatchOverflow):
        asyncio.run(scenario())
    assert len(log.entries) > 0


def test_the_bus_still_works_after_an_overflow() -> None:
    received: list[EventType] = []

    async def scenario() -> None:
        bus = _bus(RecordingLog(), max_dispatch_per_publish=3)

        async def loop(event: Event) -> None:
            await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)

        async def recorder(event: Event) -> None:
            received.append(event.type)

        await bus.subscribe(EventType.MEMORY_UPDATED, loop)
        with pytest.raises(EventDispatchOverflow):
            await bus.publish(_event(EventType.MEMORY_UPDATED), producer=ProducerIdentity.MEMORY)

        await bus.subscribe(EventType.ROUND_UPDATED, recorder)
        await bus.publish(_event(EventType.ROUND_UPDATED), producer=ProducerIdentity.ORCHESTRATION)

    asyncio.run(scenario())
    assert received == [EventType.ROUND_UPDATED]
