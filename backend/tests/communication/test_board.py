"""M11 黑板：五区块读写、写入即广播、先提交后广播、轮次生命周期。"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.communication.board import Blackboard, BoardEditor, BoardReader
from pmstudio.communication.event_bus import EventBus
from pmstudio.contracts.enums import (
    BlockOpKind,
    CardGroupState,
    CardKind,
    EventType,
    ProducerIdentity,
    RegionName,
    RoundEndReason,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.card import Card, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ConfirmedRegion, ContextBlock, ContextRegion, RoundRegion
from pmstudio.contracts.skeleton.events import (
    CardGroupUpdatedPayload,
    Event,
    RoundUpdatedPayload,
)
from pmstudio.storage.board_store import BoardStore
from pmstudio.storage.db import Database
from pmstudio.storage.event_log import EventLog

AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = AT) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class RecordingBus:
    """总线替身：记下发布了什么，可选地在发布那一刻做点事（用来验"先提交后广播"）。"""

    def __init__(self, on_publish: Callable[[Event], Awaitable[None]] | None = None) -> None:
        self.events: list[Event] = []
        self.producers: list[ProducerIdentity] = []
        self._on_publish = on_publish

    async def publish(self, event: Event, *, producer: ProducerIdentity) -> None:
        self.events.append(event)
        self.producers.append(producer)
        if self._on_publish is not None:
            await self._on_publish(event)


class FailingBus:
    async def publish(self, event: Event, *, producer: ProducerIdentity) -> None:
        raise RuntimeError("广播炸了")


def _round_region(**overrides: object) -> RoundRegion:
    base: dict[str, object] = {
        "round_id": "rnd_1",
        "project_id": "prj_1",
        "entry": RoundEntry.MAIN,
        "user_input": "细化一下功能清单",
        "scope": Scope(selected_fields=("功能清单",)),
        "phase": RoundPhase.ASSEMBLING,
    }
    base.update(overrides)
    return RoundRegion(**base)  # type: ignore[arg-type]


def _context_region() -> ContextRegion:
    return ContextRegion(
        blocks=(ContextBlock(source="document", ref="blk_1", content="现在写的是……", priority=10),),
        assembled_at=AT,
    )


def _fill_group(state: CardGroupState = CardGroupState.ANSWERING) -> CardGroup:
    return CardGroup(
        group_id="grp_1",
        round_id="rnd_1",
        state=state,
        cards=(
            Card(
                card_id="crd_fill",
                kind=CardKind.FILL,
                prompt="这次我打算改动 1 处",
                proposals=(
                    Proposal(
                        proposal_id="prp_1",
                        target_label="功能清单",
                        op=BlockOpKind.APPEND,
                        content="支持讨论候选进主闭环",
                    ),
                ),
            ),
        ),
    )


class Bench:
    """一个开好轮的黑板，连着真存储。"""

    def __init__(self, tmp_path: Path, bus: object | None = None) -> None:
        self.db = Database.open(tmp_path / "pmstudio.sqlite3")
        self.store = BoardStore(self.db)
        self.bus = bus if bus is not None else RecordingBus()
        self.blackboard = Blackboard(self.store, self.bus, FixedClock())  # type: ignore[arg-type]
        self.writer = BoardEditor(self.blackboard)
        self.board = BoardReader(self.blackboard)
        asyncio.run(self.writer.open_round("rnd_1"))

    def close(self) -> None:
        self.db.close()

    @property
    def events(self) -> list[Event]:
        return self.bus.events  # type: ignore[attr-defined]


@pytest.fixture
def bench(tmp_path: Path):
    harness = Bench(tmp_path)
    yield harness
    harness.close()


def test_all_five_regions_can_be_written_and_read(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    asyncio.run(bench.writer.write(RegionName.CONTEXT, _context_region()))
    asyncio.run(bench.writer.write(RegionName.CARD_GROUP, _fill_group()))
    asyncio.run(bench.writer.write(RegionName.CLAIMS, ({"claim_id": "clm_1"},)))
    asyncio.run(bench.writer.write(RegionName.CONFIRMED, ConfirmedRegion(memory_ids=("mry_1",))))

    assert asyncio.run(bench.board.read(RegionName.ROUND)) == _round_region()
    assert asyncio.run(bench.board.read(RegionName.CONTEXT)) == _context_region()
    assert asyncio.run(bench.board.read(RegionName.CARD_GROUP)) == _fill_group()
    assert asyncio.run(bench.board.read(RegionName.CLAIMS)) == ({"claim_id": "clm_1"},)
    assert asyncio.run(bench.board.read(RegionName.CONFIRMED)) == ConfirmedRegion(memory_ids=("mry_1",))


def test_unwritten_region_reads_as_none(bench: Bench) -> None:
    """开轮时只有 round 有值，另外四个"还没写"是正常状态。"""
    assert asyncio.run(bench.board.read(RegionName.CONTEXT)) is None
    assert asyncio.run(bench.board.read(RegionName.ROUND)) is None


def test_opening_a_round_broadcasts_without_a_previous_phase(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    event = bench.events[0]
    assert event.type is EventType.ROUND_UPDATED
    assert isinstance(event.payload, RoundUpdatedPayload)
    assert event.payload.phase is RoundPhase.ASSEMBLING
    assert event.payload.from_phase is None
    assert bench.bus.producers == [ProducerIdentity.ORCHESTRATION]  # type: ignore[attr-defined]


def test_phase_change_carries_the_previous_phase(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region(phase=RoundPhase.WORKING)))
    payload = bench.events[1].payload
    assert isinstance(payload, RoundUpdatedPayload)
    assert payload.from_phase is RoundPhase.ASSEMBLING
    assert payload.phase is RoundPhase.WORKING


def test_finishing_a_round_reports_why_it_stopped(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    asyncio.run(
        bench.writer.write(
            RegionName.ROUND,
            _round_region(phase=RoundPhase.DONE, ended_at=AT, end_reason=RoundEndReason.COMPLETED),
        )
    )
    payload = bench.events[1].payload
    assert isinstance(payload, RoundUpdatedPayload)
    assert payload.end_reason is RoundEndReason.COMPLETED


@pytest.mark.parametrize(
    ("region", "value"),
    [
        (RegionName.CONTEXT, _context_region()),
        (RegionName.CLAIMS, ({"claim_id": "clm_1"},)),
        (RegionName.CONFIRMED, ConfirmedRegion(memory_ids=("mry_1",))),
    ],
)
def test_silent_regions_do_not_broadcast(bench: Bench, region: RegionName, value: object) -> None:
    """写 context / claims / confirmed 不发事件——它们的消费者就在编排层手里。"""
    asyncio.run(bench.writer.write(region, value))  # type: ignore[arg-type]
    assert bench.events == []


def test_card_group_write_derives_the_payload(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.CARD_GROUP, _fill_group()))
    payload = bench.events[0].payload
    assert isinstance(payload, CardGroupUpdatedPayload)
    assert (payload.card_count, payload.has_fill_card) == (1, True)
    assert payload.state is CardGroupState.ANSWERING
    assert payload.from_state is None


def test_confirming_says_what_it_changed_from(bench: Bench) -> None:
    """P7：M20 靠这个分清"刚确认"和"确认之后又被写了一次"。"""
    asyncio.run(bench.writer.write(RegionName.CARD_GROUP, _fill_group()))
    asyncio.run(bench.writer.write(RegionName.CARD_GROUP, _fill_group(CardGroupState.CONFIRMED)))
    payload = bench.events[1].payload
    assert isinstance(payload, CardGroupUpdatedPayload)
    assert (payload.from_state, payload.state) == (
        CardGroupState.ANSWERING,
        CardGroupState.CONFIRMED,
    )


def test_rewriting_the_same_group_still_broadcasts(bench: Bench) -> None:
    """写即广播是机械规则，不做"值没变就不发"。"""
    asyncio.run(bench.writer.write(RegionName.CARD_GROUP, _fill_group(CardGroupState.CONFIRMED)))
    asyncio.run(bench.writer.write(RegionName.CARD_GROUP, _fill_group(CardGroupState.CONFIRMED)))
    assert len(bench.events) == 2
    payload = bench.events[1].payload
    assert isinstance(payload, CardGroupUpdatedPayload)
    assert (payload.from_state, payload.state) == (
        CardGroupState.CONFIRMED,
        CardGroupState.CONFIRMED,
    )


def test_writing_the_wrong_value_type_is_refused(bench: Bench) -> None:
    with pytest.raises(ContractViolation):
        asyncio.run(bench.writer.write(RegionName.ROUND, _context_region()))
    assert bench.events == []
    assert asyncio.run(bench.board.read(RegionName.ROUND)) is None


def test_a_region_from_another_round_is_refused(bench: Bench) -> None:
    with pytest.raises(ContractViolation):
        asyncio.run(bench.writer.write(RegionName.ROUND, _round_region(round_id="rnd_404")))


def test_writing_before_opening_a_round_is_refused(tmp_path: Path) -> None:
    db = Database.open(tmp_path / "pmstudio.sqlite3")
    try:
        blackboard = Blackboard(BoardStore(db), RecordingBus(), FixedClock())  # type: ignore[arg-type]
        with pytest.raises(ContractViolation):
            asyncio.run(BoardEditor(blackboard).write(RegionName.CONTEXT, _context_region()))
    finally:
        db.close()


def test_broadcast_happens_after_the_commit(tmp_path: Path) -> None:
    """把"先提交后广播"变成可断言的事：发布那一刻回读，必须读得到刚写的东西。"""
    db = Database.open(tmp_path / "pmstudio.sqlite3")
    try:
        seen: list[object] = []
        holder: dict[str, Blackboard] = {}

        async def on_publish(event: Event) -> None:
            seen.append(await holder["blackboard"].read(RegionName.ROUND))

        bus = RecordingBus(on_publish=on_publish)
        blackboard = Blackboard(BoardStore(db), bus, FixedClock())  # type: ignore[arg-type]
        holder["blackboard"] = blackboard
        writer = BoardEditor(blackboard)

        asyncio.run(writer.open_round("rnd_1"))
        asyncio.run(writer.write(RegionName.ROUND, _round_region()))
        assert seen == [_round_region()]
    finally:
        db.close()


def test_broadcast_failure_does_not_roll_back_the_write(bench: Bench) -> None:
    """提交已经完成，广播失败不回滚；异常照样传给调用方。"""
    bench.blackboard._bus = FailingBus()  # type: ignore[assignment]
    with pytest.raises(RuntimeError):
        asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    assert asyncio.run(bench.board.read(RegionName.ROUND)) == _round_region()


def test_reader_handle_has_no_write(bench: Bench) -> None:
    assert not hasattr(bench.board, "write")
    assert not hasattr(bench.board, "open_round")
    assert not hasattr(bench.writer, "read")


def test_drop_round_clears_regions_but_keeps_the_round_row(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    asyncio.run(bench.writer.write(RegionName.CONTEXT, _context_region()))
    asyncio.run(bench.writer.drop_round())

    assert asyncio.run(bench.board.read(RegionName.CONTEXT)) is None
    assert asyncio.run(bench.store.read_round("rnd_1")) is not None
    assert bench.blackboard.current_round_id is None


def test_opening_a_new_round_clears_the_leftovers(tmp_path: Path) -> None:
    harness = Bench(tmp_path)
    try:
        asyncio.run(harness.writer.write(RegionName.CONTEXT, _context_region()))
        asyncio.run(harness.writer.open_round("rnd_2"))
        assert asyncio.run(harness.store.read_region("rnd_1", RegionName.CONTEXT)) is None
    finally:
        harness.close()


def test_a_crashed_round_can_be_reopened_and_marked_failed(tmp_path: Path) -> None:
    """R0.5 的那一步：把上次没跑完的轮标成 failed，五个区块留着。"""
    harness = Bench(tmp_path)
    try:
        asyncio.run(harness.writer.write(RegionName.ROUND, _round_region(phase=RoundPhase.WORKING)))
        asyncio.run(harness.writer.write(RegionName.CONTEXT, _context_region()))
        asyncio.run(harness.writer.open_round("rnd_1"))  # 重启后重新打开那个轮
        asyncio.run(
            harness.writer.write(
                RegionName.ROUND,
                _round_region(phase=RoundPhase.FAILED, ended_at=AT, end_reason=RoundEndReason.FAILED),
            )
        )
        region = asyncio.run(harness.board.read(RegionName.ROUND))
        assert isinstance(region, RoundRegion)
        assert region.phase is RoundPhase.FAILED
        assert asyncio.run(harness.board.read(RegionName.CONTEXT)) == _context_region()
    finally:
        harness.close()


def test_events_are_stamped_by_the_injected_clock(bench: Bench) -> None:
    asyncio.run(bench.writer.write(RegionName.ROUND, _round_region()))
    assert bench.events[0].at == AT


def test_board_writes_land_in_the_event_log(tmp_path: Path) -> None:
    """与真总线、真流水接起来：写一次 round，流水里就多一行。"""
    db = Database.open(tmp_path / "pmstudio.sqlite3")
    try:
        log = EventLog(db)
        bus = EventBus(log)
        blackboard = Blackboard(BoardStore(db), bus, FixedClock())
        writer = BoardEditor(blackboard)
        asyncio.run(writer.open_round("rnd_1"))
        asyncio.run(writer.write(RegionName.ROUND, _round_region()))

        rows = log.read_by_round("rnd_1")
        assert [row.type for row in rows] == [EventType.ROUND_UPDATED]
        assert rows[0].payload.phase is RoundPhase.ASSEMBLING
    finally:
        db.close()
