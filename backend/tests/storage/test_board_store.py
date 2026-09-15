"""工作台仓库：rounds（永久）+ board_regions（只装当前轮的四个区块）。"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import RegionName, RoundEntry, RoundPhase
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ConfirmedRegion, ContextRegion, RoundRegion
from pmstudio.storage.board_store import BoardStore
from pmstudio.storage.db import Database

AT = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path):
    database = Database.open(tmp_path / "pmstudio.sqlite3")
    yield database
    database.close()


@pytest.fixture
def store(db: Database) -> BoardStore:
    return BoardStore(db)


def _round(phase: RoundPhase = RoundPhase.ASSEMBLING) -> RoundRegion:
    from pmstudio.contracts.enums import RoundEndReason

    return RoundRegion(
        round_id="rnd_1",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input="细化一下功能清单",
        scope=Scope(selected_fields=("功能清单",)),
        phase=phase,
        ended_at=AT if phase is RoundPhase.DONE else None,
        end_reason=RoundEndReason.COMPLETED if phase is RoundPhase.DONE else None,
    )


def test_round_region_is_upserted_not_duplicated(store: BoardStore) -> None:
    from pmstudio.contracts.enums import RoundEndReason

    asyncio.run(store.write_round(_round()))
    done = _round(RoundPhase.DONE).model_copy(update={"end_reason": RoundEndReason.COMPLETED, "ended_at": AT})
    asyncio.run(store.write_round(done))
    region = asyncio.run(store.read_round("rnd_1"))
    assert region is not None
    assert region.phase is RoundPhase.DONE
    assert region.end_reason is RoundEndReason.COMPLETED


def test_round_region_keeps_scope_and_entry(store: BoardStore) -> None:
    asyncio.run(store.write_round(_round()))
    region = asyncio.run(store.read_round("rnd_1"))
    assert region is not None
    assert region.scope.selected_fields == ("功能清单",)
    assert region.entry is RoundEntry.MAIN


def test_round_region_does_not_live_in_board_regions(store: BoardStore) -> None:
    with pytest.raises(ContractViolation):
        asyncio.run(store.write_region("rnd_1", RegionName.ROUND, _round()))
    with pytest.raises(ContractViolation):
        asyncio.run(store.read_region("rnd_1", RegionName.ROUND))


def test_one_row_per_round_and_region(store: BoardStore) -> None:
    first = ContextRegion(assembled_at=AT)
    second = ContextRegion(assembled_at=AT, dropped=())
    asyncio.run(store.write_region("rnd_1", RegionName.CONTEXT, first))
    asyncio.run(store.write_region("rnd_1", RegionName.CONTEXT, second))
    assert asyncio.run(store.read_region("rnd_1", RegionName.CONTEXT)) == second


def test_claims_region_round_trips_as_an_opaque_sequence(store: BoardStore) -> None:
    claims = ({"claim_id": "clm_1"}, {"claim_id": "clm_2"})
    asyncio.run(store.write_region("rnd_1", RegionName.CLAIMS, claims))
    stored = asyncio.run(store.read_region("rnd_1", RegionName.CLAIMS))
    assert stored == claims


def test_claims_region_rejects_unserializable_objects(store: BoardStore) -> None:
    with pytest.raises(ContractViolation):
        asyncio.run(store.write_region("rnd_1", RegionName.CLAIMS, (object(),)))


def test_confirmed_region_round_trips(store: BoardStore) -> None:
    region = ConfirmedRegion(card_group_ids=("grp_1",), memory_ids=("mry_1",))
    asyncio.run(store.write_region("rnd_1", RegionName.CONFIRMED, region))
    assert asyncio.run(store.read_region("rnd_1", RegionName.CONFIRMED)) == region


def test_drop_regions_only_touches_that_round(store: BoardStore) -> None:
    asyncio.run(store.write_region("rnd_1", RegionName.CONTEXT, ContextRegion(assembled_at=AT)))
    asyncio.run(store.write_region("rnd_2", RegionName.CONTEXT, ContextRegion(assembled_at=AT)))
    asyncio.run(store.drop_regions("rnd_1"))
    assert asyncio.run(store.read_region("rnd_1", RegionName.CONTEXT)) is None
    assert asyncio.run(store.read_region("rnd_2", RegionName.CONTEXT)) is not None


def test_drop_other_regions_keeps_the_current_round(store: BoardStore) -> None:
    """开新一轮时把旧的清掉（C12）。"""
    asyncio.run(store.write_round(_round()))
    asyncio.run(store.write_region("rnd_1", RegionName.CONTEXT, ContextRegion(assembled_at=AT)))
    asyncio.run(store.drop_other_regions("rnd_2"))
    assert asyncio.run(store.read_region("rnd_1", RegionName.CONTEXT)) is None
    assert asyncio.run(store.read_round("rnd_1")) is not None
