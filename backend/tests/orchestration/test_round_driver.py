"""R1.3 轮次状态机：把一轮从用户输入跑到等待用户。

测试全程用注入的假组件（不碰网络/模型），验证 11 步的正确顺序。
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.communication.board import Blackboard, BoardEditor, BoardReader
from pmstudio.contracts.enums import (
    EventType,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RegionName
from pmstudio.orchestration.cards import CardAssembler
from pmstudio.orchestration.consensus import ConsensusGenerator
from pmstudio.orchestration.round_driver import RoundDriver
from pmstudio.storage.board_store import BoardStore
from pmstudio.storage.db import Database

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[tuple[EventType, Any]] = []

    async def publish(self, event: Any, *, producer: Any) -> None:
        self.published.append((event.type, event.payload))


class _FakeModelGateway:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, model_ref: str, messages: list[PromptMessage]) -> str:
        self.calls += 1
        return "我理解你要细化功能清单，当前该字段内容为空。"


class _FakeHarness:
    def __init__(self, gateway: _FakeModelGateway) -> None:
        self._gateway = gateway

    async def complete(self, model_ref: str, messages: list[PromptMessage]) -> str:
        return await self._gateway.complete(model_ref, messages)

    async def trim(self, blocks: Any, project_id: str, model_ref: str) -> Any:
        class _Result:
            blocks = ()
            dropped = ()

        return _Result()


@pytest.fixture
def driver_setup(tmp_path: Path):
    """构造 RoundDriver 及其全部依赖。"""
    db = Database.open(tmp_path / "test.sqlite3")
    store = BoardStore(db)
    bus = _FakeBus()
    clock = FixedClock()

    blackboard = Blackboard(store, bus, clock)
    board_reader = BoardReader(blackboard)
    board_writer = BoardEditor(blackboard)

    gateway = _FakeModelGateway()
    harness = _FakeHarness(gateway)
    consensus = ConsensusGenerator(harness, TimestampIdGenerator())
    cards = CardAssembler(TimestampIdGenerator())

    driver = RoundDriver(
        writer=board_writer,
        board=board_reader,
        consensus=consensus,
        cards=cards,
        clock=clock,
        ids=TimestampIdGenerator(),
    )

    yield driver, bus, blackboard, gateway
    db.close()


def test_it_walks_the_eleven_steps_in_order(driver_setup: Any) -> None:
    """11 步走完后：round→context→card_group 三区块写入。"""
    driver, _, board, _ = driver_setup

    rid = asyncio.run(
        driver.start_round("prj_1", RoundEntry.MAIN, "细化功能清单", Scope())
    )

    r = asyncio.run(board.read(RegionName.ROUND))
    assert r is not None
    assert r.round_id == rid

    ctx = asyncio.run(board.read(RegionName.CONTEXT))
    assert ctx is not None

    cg = asyncio.run(board.read(RegionName.CARD_GROUP))
    assert cg is not None
    assert len(cg.cards) == 1


def test_phases_advance_in_order(driver_setup: Any) -> None:
    """assembling → restating → awaiting_user。"""
    driver, bus, _, _ = driver_setup
    asyncio.run(
        driver.start_round("prj_1", RoundEntry.MAIN, "细化功能清单", Scope())
    )

    round_events = [p for t, p in bus.published if t == EventType.ROUND_UPDATED]
    phases = [p.phase for p in round_events]

    assert RoundPhase.ASSEMBLING in phases
    assert RoundPhase.RESTATING in phases
    assert RoundPhase.AWAITING_USER in phases
    # 顺序：assembling 在 restating 前，restating 在 awaiting_user 前
    assert phases.index(RoundPhase.ASSEMBLING) < phases.index(RoundPhase.RESTATING)
    assert phases.index(RoundPhase.RESTATING) < phases.index(RoundPhase.AWAITING_USER)


def test_events_are_published_and_logged(driver_setup: Any) -> None:
    """context 不发；card_group 发。"""
    driver, bus, _, _ = driver_setup
    asyncio.run(
        driver.start_round("prj_1", RoundEntry.MAIN, "细化功能清单", Scope())
    )

    event_types = [t for t, _ in bus.published]
    assert event_types.count(EventType.ROUND_UPDATED) >= 3
    assert event_types.count(EventType.CARD_GROUP_UPDATED) == 1


def test_it_returns_the_round_id(driver_setup: Any) -> None:
    """返回值 = 黑板当前轮。"""
    driver, _, _, _ = driver_setup
    rid = asyncio.run(
        driver.start_round("prj_1", RoundEntry.MAIN, "细化功能清单", Scope())
    )
    assert rid.startswith("rnd_")


def test_no_network_in_tests(driver_setup: Any) -> None:
    """全程用注入的假 gateway，不调真模型。"""
    driver, _, _, gateway = driver_setup
    asyncio.run(
        driver.start_round("prj_1", RoundEntry.MAIN, "细化功能清单", Scope())
    )
    assert gateway.calls == 1  # 只调一次（理解卡）
