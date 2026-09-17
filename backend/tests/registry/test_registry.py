"""M27 注册中心：按 kind 存取、登记时校验本体、改状态发事件（L0 的唯一写入口）。"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.communication.event_bus import EventBus
from pmstudio.contracts.enums import (
    EventType,
    ProducerIdentity,
    RegistryKind,
    RegistryOwner,
    RegistryStatus,
)
from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.models.registry import RegistryEntry
from pmstudio.contracts.skeleton.events import Event, RegistryUpdatedPayload
from pmstudio.registry.entries import InMemoryRegistry
from pmstudio.storage.db import Database
from pmstudio.storage.event_log import EventLog

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


class RecordingBus:
    """总线替身：记下发布了什么（真实落流水已经在 `test_event_log` / `test_event_bus` 里验过）。"""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.producers: list[ProducerIdentity] = []

    async def publish(self, event: Event, *, producer: ProducerIdentity) -> None:
        self.events.append(event)
        self.producers.append(producer)


def _tool(entry_id: str = "reg_tool_1", tags: tuple[str, ...] = ("web_search",)) -> RegistryEntry:
    return RegistryEntry(
        id=entry_id,
        kind=RegistryKind.TOOL,
        name="联网搜索",
        content={"endpoint": "search_web"},
        description="按关键词搜网上的资料",
        tags=tags,
        when_to_use="需要外部事实、文档里没有的时候",
        owner=RegistryOwner.PRESET,
    )


def _template(entry_id: str = "reg_tpl_initial", content: Any = None) -> RegistryEntry:
    return RegistryEntry(
        id=entry_id,
        kind=RegistryKind.TEMPLATE,
        name="初版产品共识文档",
        content={"fields": [{"label": "目标", "required": True}]} if content is None else content,
        owner=RegistryOwner.PRESET,
    )


@pytest.fixture
def mem_registry() -> tuple[InMemoryRegistry, RecordingBus]:
    bus = RecordingBus()
    return InMemoryRegistry(bus, FixedClock()), bus  # type: ignore[arg-type]


@pytest.fixture
def logged_registry(tmp_path: Path):
    db = Database.open(tmp_path / "pmstudio.sqlite3")
    log = EventLog(db)
    registry = InMemoryRegistry(EventBus(log), FixedClock())
    yield registry, log
    db.close()


def test_the_registry_satisfies_its_port(mem_registry: tuple[InMemoryRegistry, RecordingBus]) -> None:
    registry, _ = mem_registry
    assert isinstance(registry, RegistryPort)


def test_resolve_returns_the_entry(mem_registry: tuple[InMemoryRegistry, RecordingBus]) -> None:
    registry, _ = mem_registry
    asyncio.run(registry.register(_tool()))

    entry = asyncio.run(registry.resolve(RegistryKind.TOOL, "reg_tool_1"))

    assert entry.kind is RegistryKind.TOOL
    assert entry.name == "联网搜索"


def test_resolve_refuses_missing_ids_and_wrong_kinds(
    mem_registry: tuple[InMemoryRegistry, RecordingBus],
) -> None:
    """找不到就抛错，不返回 None 让调用方猜（RegistryPort 的约定）。"""
    registry, _ = mem_registry
    asyncio.run(registry.register(_tool()))

    with pytest.raises(ContractViolation):
        asyncio.run(registry.resolve(RegistryKind.TOOL, "reg_nope"))
    with pytest.raises(ContractViolation):
        asyncio.run(registry.resolve(RegistryKind.TEMPLATE, "reg_tool_1"))


def test_list_filters_by_kind_then_tags(mem_registry: tuple[InMemoryRegistry, RecordingBus]) -> None:
    registry, _ = mem_registry
    asyncio.run(registry.register(_tool("reg_tool_1", ("web_search",))))
    asyncio.run(registry.register(_tool("reg_tool_2", ("summarize",))))
    asyncio.run(registry.register(_template()))

    assert {entry.id for entry in asyncio.run(registry.list(RegistryKind.TOOL))} == {
        "reg_tool_1",
        "reg_tool_2",
    }
    assert [entry.id for entry in asyncio.run(registry.list(RegistryKind.TOOL, ("web_search",)))] == [
        "reg_tool_1"
    ]
    assert [entry.id for entry in asyncio.run(registry.list(RegistryKind.TEMPLATE))] == [
        "reg_tpl_initial"
    ]


def test_register_refuses_a_duplicate_id(mem_registry: tuple[InMemoryRegistry, RecordingBus]) -> None:
    registry, _ = mem_registry
    asyncio.run(registry.register(_tool()))

    with pytest.raises(ContractViolation):
        asyncio.run(registry.register(_tool()))


def test_register_validates_the_template_body(
    mem_registry: tuple[InMemoryRegistry, RecordingBus],
) -> None:
    """模板本体形状不对，要在**登记时**炸——而不是等建项目时才发现种子是坏的。"""
    registry, _ = mem_registry

    with pytest.raises(ContractViolation):
        asyncio.run(registry.register(_template(content={"fields": []})))
    with pytest.raises(ContractViolation):
        asyncio.run(registry.register(_template(content={"nope": 1})))


def test_update_status_publishes_registry_updated(
    mem_registry: tuple[InMemoryRegistry, RecordingBus],
) -> None:
    registry, bus = mem_registry
    asyncio.run(registry.register(_template()))

    asyncio.run(registry.update_status("reg_tpl_initial", RegistryStatus.RETIRED))

    assert bus.producers == [ProducerIdentity.REGISTRY]
    event = bus.events[0]
    assert event.type is EventType.REGISTRY_UPDATED
    assert event.at == AT
    assert isinstance(event.payload, RegistryUpdatedPayload)
    assert event.payload.status is RegistryStatus.RETIRED
    assert event.payload.from_status is RegistryStatus.ACTIVE
    assert asyncio.run(registry.resolve(RegistryKind.TEMPLATE, "reg_tpl_initial")).status is (
        RegistryStatus.RETIRED
    )


def test_update_status_refuses_an_unknown_id(
    mem_registry: tuple[InMemoryRegistry, RecordingBus],
) -> None:
    registry, bus = mem_registry

    with pytest.raises(ContractViolation):
        asyncio.run(registry.update_status("reg_nope", RegistryStatus.RETIRED))

    assert bus.events == []


def test_registry_updated_lands_in_the_log_without_a_round(logged_registry) -> None:
    """注册条目状态变化不绑轮次（C13）：流水里那两列是空的，行照样在。"""
    registry, log = logged_registry
    asyncio.run(registry.register(_template()))

    asyncio.run(registry.update_status("reg_tpl_initial", RegistryStatus.RETIRED))

    rows = log._db._connection.execute("SELECT type, producer, round_id, project_id FROM events").fetchall()
    assert [tuple(row) for row in rows] == [("registry.updated", "registry", None, None)]


def test_remove_deletes_the_entry(mem_registry: tuple[InMemoryRegistry, RecordingBus]) -> None:
    registry, _ = mem_registry
    asyncio.run(registry.register(_template()))

    asyncio.run(registry.remove("reg_tpl_initial"))

    with pytest.raises(ContractViolation):
        asyncio.run(registry.resolve(RegistryKind.TEMPLATE, "reg_tpl_initial"))
    with pytest.raises(ContractViolation):
        asyncio.run(registry.remove("reg_tpl_initial"))
