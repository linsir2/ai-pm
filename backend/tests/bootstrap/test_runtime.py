"""装配根：交出去的是接口，不是实现；启动顺序钉死。"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.bootstrap.runtime import build_runtime, build_runtime_sync
from pmstudio.common.errors import SingleInstanceViolation
from pmstudio.contracts.enums import EventType, ProducerIdentity, RegionName, RoundEntry, RoundPhase
from pmstudio.contracts.interfaces.communication import Board
from pmstudio.contracts.interfaces.storage import BoardStorePort, EventLogPort
from pmstudio.contracts.skeleton.events import Event, RoundUpdatedPayload

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class CounterIds:
    """可注入的标识生成器：测试里 id 不该是随机的。"""

    def __init__(self) -> None:
        self.count = 0

    def new_id(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}_test_{self.count}"


@pytest.fixture
def runtime(tmp_path: Path):
    built = build_runtime_sync(tmp_path / "pmstudio.sqlite3")
    yield built
    built.close()


def test_runtime_parts_satisfy_their_ports(runtime: object) -> None:
    assert isinstance(runtime.event_log, EventLogPort)
    assert isinstance(runtime.board, Board)
    assert runtime.ledger is not None


def test_boards_store_is_wired_under_the_hood(runtime: object) -> None:
    assert isinstance(runtime.board._blackboard._store, BoardStorePort)


def test_runtime_exposes_no_write_handle(runtime: object) -> None:
    """I19：写句柄只给编排层，装配这一步不许把它散出去。"""
    for forbidden in ("write", "open_round", "drop_round"):
        assert not hasattr(runtime, forbidden)
        assert not hasattr(runtime.board, forbidden)


def test_the_wired_bus_really_logs_events(runtime: object) -> None:
    async def scenario() -> None:
        await runtime.event_bus.publish(
            Event(
                type=EventType.ROUND_UPDATED,
                payload=RoundUpdatedPayload(
                    round_id="rnd_1",
                    project_id="prj_1",
                    entry=RoundEntry.MAIN,
                    phase=RoundPhase.ASSEMBLING,
                ),
                at=NOW,
            ),
            producer=ProducerIdentity.ORCHESTRATION,
        )

    asyncio.run(scenario())
    assert [row.type for row in runtime.event_log.read_by_round("rnd_1")] == [EventType.ROUND_UPDATED]


def test_the_wired_board_starts_empty(runtime: object) -> None:
    assert asyncio.run(runtime.board.read(RegionName.ROUND)) is None


def test_clock_and_ids_can_be_injected(tmp_path: Path) -> None:
    ids = CounterIds()
    built = build_runtime_sync(tmp_path / "pmstudio.sqlite3", clock=FixedClock(), ids=ids)
    try:
        assert built.clock.now() == NOW
        assert built.ids is ids
    finally:
        built.close()


def test_close_is_idempotent_and_releases_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    built = asyncio.run(build_runtime(path))
    built.close()
    built.close()
    again = asyncio.run(build_runtime(path))
    again.close()


def test_second_runtime_on_the_same_database_is_refused(tmp_path: Path, runtime: object) -> None:
    with pytest.raises(SingleInstanceViolation):
        asyncio.run(build_runtime(tmp_path / "pmstudio.sqlite3"))
