"""启动恢复：上次没跑完的轮标 failed，五个区块留着。"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from pmstudio.bootstrap.runtime import build_runtime_sync
from pmstudio.communication.board import Blackboard, BoardEditor
from pmstudio.communication.event_bus import EventBus
from pmstudio.contracts.enums import (
    EventType,
    RegionName,
    RoundEndReason,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextRegion, RoundRegion
from pmstudio.storage.board_store import BoardStore
from pmstudio.storage.db import Database
from pmstudio.storage.event_log import EventLog

AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


def _round(round_id: str = "rnd_1", phase: RoundPhase = RoundPhase.WORKING) -> RoundRegion:
    return RoundRegion(
        round_id=round_id,
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input="细化一下功能清单",
        scope=Scope(selected_fields=("功能清单",)),
        phase=phase,
        ended_at=AT if phase is RoundPhase.DONE else None,
        end_reason=RoundEndReason.COMPLETED if phase is RoundPhase.DONE else None,
    )


def _seed_round(path: Path, region: RoundRegion, with_context: bool = True) -> None:
    """造一个"上一个进程写到一半就没了"的库。"""
    db = Database.open(path)
    try:
        store = BoardStore(db)
        asyncio.run(store.write_round(region))
        if with_context:
            asyncio.run(
                store.write_region(
                    region.round_id,
                    RegionName.CONTEXT,
                    ContextRegion(assembled_at=AT),
                )
            )
    finally:
        db.close()


def test_unfinished_round_is_marked_failed_on_startup(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round())

    runtime = build_runtime_sync(path, clock=FixedClock())
    try:
        assert runtime.recovered_rounds == ("rnd_1",)
        db = runtime.db
        store = BoardStore(db)
        recovered = asyncio.run(store.read_round("rnd_1"))
        assert recovered is not None
        assert recovered.phase is RoundPhase.FAILED
        assert recovered.end_reason is RoundEndReason.PROCESS_RESTART
        assert recovered.ended_at == AT
    finally:
        runtime.close()


def test_the_failed_round_keeps_its_regions(tmp_path: Path) -> None:
    """保留五个区块，用户才看得到"跑到哪了"；等开新一轮时才删。"""
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round())

    runtime = build_runtime_sync(path, clock=FixedClock())
    try:
        store = BoardStore(runtime.db)
        assert asyncio.run(store.read_region("rnd_1", RegionName.CONTEXT)) is not None
        assert asyncio.run(store.read_round("rnd_1")) is not None
    finally:
        runtime.close()


def test_finished_rounds_are_left_alone(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round(phase=RoundPhase.DONE))

    runtime = build_runtime_sync(path, clock=FixedClock())
    try:
        assert runtime.recovered_rounds == ()
        region = asyncio.run(BoardStore(runtime.db).read_round("rnd_1"))
        assert region is not None
        assert region.phase is RoundPhase.DONE
    finally:
        runtime.close()


def test_every_unfinished_round_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round("rnd_1"), with_context=False)
    _seed_round(path, _round("rnd_2"), with_context=False)

    runtime = build_runtime_sync(path, clock=FixedClock())
    try:
        assert runtime.recovered_rounds == ("rnd_1", "rnd_2")
    finally:
        runtime.close()


def test_recovery_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round())

    first = build_runtime_sync(path, clock=FixedClock())
    first.close()
    second = build_runtime_sync(path, clock=FixedClock())
    try:
        assert second.recovered_rounds == ()
    finally:
        second.close()


def test_recovery_lands_in_the_event_log(tmp_path: Path) -> None:
    """恢复也要留痕：这轮为什么变成 failed，流水要能回答。"""
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round(), with_context=False)

    runtime = build_runtime_sync(path, clock=FixedClock())
    try:
        rows = runtime.event_log.read_by_round("rnd_1")
        assert [row.type for row in rows] == [EventType.ROUND_UPDATED]
        assert rows[0].payload.phase is RoundPhase.FAILED
        assert rows[0].payload.end_reason is RoundEndReason.PROCESS_RESTART
    finally:
        runtime.close()


def test_opening_a_new_round_clears_the_failed_one(tmp_path: Path) -> None:
    """C12："用户开新一轮时再删"——这是第一次被真正验证。"""
    path = tmp_path / "pmstudio.sqlite3"
    _seed_round(path, _round())

    runtime = build_runtime_sync(path, clock=FixedClock())
    db = runtime.db
    store = BoardStore(db)
    blackboard = Blackboard(store, EventBus(EventLog(db)), FixedClock())
    writer = BoardEditor(blackboard)
    try:
        assert asyncio.run(store.read_region("rnd_1", RegionName.CONTEXT)) is not None
        asyncio.run(writer.open_round("rnd_2"))
        assert asyncio.run(store.read_region("rnd_1", RegionName.CONTEXT)) is None
        assert asyncio.run(store.read_round("rnd_1")) is not None
    finally:
        runtime.close()
