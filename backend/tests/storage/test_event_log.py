"""事件流水仓库：append-only，按轮次可查。"""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.contracts.enums import EventType, ProducerIdentity, RoundEntry, RoundPhase
from pmstudio.contracts.skeleton.events import EventLogEntry, RoundUpdatedPayload
from pmstudio.storage.db import Database
from pmstudio.storage.event_log import EventLog

AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path):
    database = Database.open(tmp_path / "pmstudio.sqlite3")
    yield database
    database.close()


@pytest.fixture
def log(db: Database) -> EventLog:
    return EventLog(db)


def _entry(round_id: str = "rnd_1", event_id: str = "evt_1") -> EventLogEntry:
    return EventLogEntry(
        event_id=event_id,
        at=AT,
        type=EventType.ROUND_UPDATED,
        producer=ProducerIdentity.ORCHESTRATION,
        payload=RoundUpdatedPayload(
            round_id=round_id,
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            phase=RoundPhase.WORKING,
        ),
        round_id=round_id,
        project_id="prj_1",
    )


def test_appended_entry_reads_back_unchanged(log: EventLog) -> None:
    entry = _entry()
    asyncio.run(log.append(entry))
    rows = log.read_by_round("rnd_1")
    assert rows == [entry]
    assert isinstance(rows[0].payload, RoundUpdatedPayload)
    assert rows[0].payload.phase is RoundPhase.WORKING


def test_reader_filters_by_round(log: EventLog) -> None:
    asyncio.run(log.append(_entry("rnd_1", "evt_1")))
    asyncio.run(log.append(_entry("rnd_2", "evt_2")))
    assert [row.event_id for row in log.read_by_round("rnd_2")] == ["evt_2"]
    assert log.read_by_round("rnd_404") == []


def test_rows_keep_insertion_order(log: EventLog) -> None:
    for index in range(3):
        asyncio.run(log.append(_entry("rnd_1", f"evt_{index}")))
    assert [row.event_id for row in log.read_by_round("rnd_1")] == ["evt_0", "evt_1", "evt_2"]


def test_duplicate_event_id_is_refused(log: EventLog) -> None:
    """同一条事件不能落两次。"""
    asyncio.run(log.append(_entry()))
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(log.append(_entry()))


def test_flow_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    first = Database.open(path)
    asyncio.run(EventLog(first).append(_entry()))
    first.close()

    second = Database.open(path)
    try:
        assert len(EventLog(second).read_by_round("rnd_1")) == 1
    finally:
        second.close()


def test_the_log_is_append_only(log: EventLog) -> None:
    """只增不改：仓库里没有改和删的入口。"""
    for forbidden in ("update", "delete", "remove", "replace", "truncate"):
        assert not hasattr(log, forbidden)
