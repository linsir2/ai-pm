"""RoundRunner 的 DB 租约：start 同事务、心跳续租/失租、finally 释放、abandon 接管。

全部用**真 SQLite + 真容器**（只有模型是脚本化的）。时间相关的用例把
`RoundRunner._lease_heartbeat_seconds` 调小，避免等 30 秒。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from pmstudio.application.commands import RoundCommand
from pmstudio.application.errors import LeaseLost, RoundBusy, RoundLeaseLost
from pmstudio.application.lease import (
    LeaseHeartbeat,
    LeaseScope,
    clear_lease_scope,
    current_lease_scope,
    set_lease_scope,
    wall_clock_ms,
)
from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from pmstudio.domain.shared.ids import RoundId
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.harness.errors import ModelCallFailed
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore
from pmstudio.infrastructure.sqlite.repositories import SqliteRoundRepository
from tests.conftest import TEST_OWNER
from tests.support.doubles import ScriptedModelGateway

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "order-export",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "你说到了数据量"}],
    },
    ensure_ascii=False,
)

SEAT = "seat.data_scale"


class GatedGateway(ScriptedModelGateway):
    """命中的 purpose 先挂住，放行后照脚本回；记下被挂住的次数。"""

    def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(**kwargs)
        self.block_purposes: set[str] = set()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.blocked = 0

    async def complete(self, request):  # type: ignore[no-untyped-def]
        if request.purpose in self.block_purposes:
            self.blocked += 1
            self.entered.set()
            await self.release.wait()
        return await super().complete(request)


def _settings(db_path: Path) -> Settings:
    return Settings(
        db_path=db_path,
        model="scripted",
        api_key="test-key",
        api_base=None,
    )


def _build(db_path: Path, gateway: ScriptedModelGateway):  # type: ignore[no-untyped-def]
    return build_container(_settings(db_path), gateway=gateway).app


async def _new_project(app) -> str:  # type: ignore[no-untyped-def]
    summary = await app.state.services.create_project(title="租约真跑")
    return summary.project_id


def _lease_row(db_path: Path, round_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(db_path, timeout=10.0)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT lease_owner, lease_token, lease_expires_at, step FROM rounds"
            " WHERE round_id = ?",
            (round_id,),
        ).fetchone()
    finally:
        connection.close()


def _count_rounds(db_path: Path) -> int:
    connection = sqlite3.connect(db_path, timeout=10.0)
    try:
        return int(connection.execute("SELECT COUNT(*) FROM rounds").fetchone()[0])
    finally:
        connection.close()


async def test_start_holds_one_lease_from_insert_until_analyze_finishes(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(
        replies={"claims": CLAIMS_REPLY},
        seat_replies={SEAT: "{}"},
    )
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    runner = app.state.services.runner

    gateway.block_purposes = {"claims"}
    task = asyncio.create_task(
        runner.run(
            RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
        )
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)

    row = _lease_row(db_path, _only_round_id(db_path))
    assert row["lease_owner"] is not None
    assert row["lease_expires_at"] is not None
    assert row["lease_expires_at"] > wall_clock_ms()

    gateway.release.set()
    view = await asyncio.wait_for(task, timeout=5)

    assert view.step == "claims_ready"
    row = _lease_row(db_path, view.round_id)
    assert row["lease_owner"] is None


def _only_round_id(db_path: Path) -> str:
    connection = sqlite3.connect(db_path, timeout=10.0)
    try:
        return str(connection.execute("SELECT round_id FROM rounds").fetchone()[0])
    finally:
        connection.close()


async def test_start_insert_rolls_back_when_acquire_fails(
    tmp_path, monkeypatch
) -> None:
    """INSERT 与 acquire 同事务：acquire 失败整笔回滚，模型一次都不调。"""

    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(replies={"claims": CLAIMS_REPLY})
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    runner = app.state.services.runner

    def _no_lease(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr(SqliteLeaseStore, "acquire", _no_lease)

    with pytest.raises(RoundBusy):
        await runner.run(
            RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
        )

    assert _count_rounds(db_path) == 0
    assert gateway.requests == []


async def test_two_runners_share_the_db_and_only_one_enters_the_graph(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(replies={"claims": CLAIMS_REPLY})
    first_app = _build(db_path, gateway)
    second_app = _build(db_path, gateway)
    project_id = await _new_project(first_app)

    first_runner = first_app.state.services.runner
    second_runner = second_app.state.services.runner

    started = await first_runner.run(
        RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
    )

    gateway.block_purposes = {"seat_review"}
    gateway.entered = asyncio.Event()
    gateway.release = asyncio.Event()
    running = asyncio.create_task(
        first_runner.run(
            RoundCommand(kind="submit_opening", round_id=started.round_id, seats=(SEAT,))
        )
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)

    with pytest.raises(RoundBusy):
        await second_runner.run(
            RoundCommand(kind="submit_opening", round_id=started.round_id, seats=(SEAT,))
        )

    assert gateway.blocked == 1

    running.cancel()
    outcome = (await asyncio.gather(running, return_exceptions=True))[0]
    assert isinstance(outcome, asyncio.CancelledError)

    row = _lease_row(db_path, started.round_id)
    assert row["lease_owner"] is None


async def test_lost_lease_cancels_the_run_and_maps_to_round_lease_lost(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(replies={"claims": CLAIMS_REPLY})
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    runner = app.state.services.runner
    runner._lease_heartbeat_seconds = 0.02

    gateway.block_purposes = {"claims"}
    running = asyncio.create_task(
        runner.run(
            RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
        )
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)
    round_id = _only_round_id(db_path)

    connection = database.connect(db_path)
    try:
        stolen = SqliteLeaseStore(connection).force_acquire(
            LeaseKind.ROUND,
            round_id,
            TEST_OWNER,
            "thief",
            wall_clock_ms(),
            90_000,
        )
    finally:
        connection.close()
    assert stolen.holder == "thief"

    with pytest.raises(RoundLeaseLost):
        await asyncio.wait_for(running, timeout=5)

    row = _lease_row(db_path, round_id)
    assert row["lease_owner"] == "thief"


async def test_failed_model_call_still_releases_the_lease(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(replies={})
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    runner = app.state.services.runner

    with pytest.raises(ModelCallFailed):
        await runner.run(
            RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
        )

    round_id = _only_round_id(db_path)
    row = _lease_row(db_path, round_id)
    assert row["lease_owner"] is None
    assert row["step"] == "failed"


async def test_abandon_force_acquires_writes_releases_and_fences_the_old_worker(
    tmp_path,
) -> None:
    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(
        replies={"claims": CLAIMS_REPLY},
    )
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    services = app.state.services
    runner = services.runner

    started = await runner.run(
        RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
    )

    gateway.block_purposes = {"seat_review"}
    running = asyncio.create_task(
        runner.run(
            RoundCommand(kind="submit_opening", round_id=started.round_id, seats=(SEAT,))
        )
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)

    row = _lease_row(db_path, started.round_id)
    old_token = LeaseToken(
        kind=LeaseKind.ROUND,
        target_id=started.round_id,
        user_id=str(TEST_OWNER),
        holder=str(row["lease_owner"]),
        token=int(row["lease_token"]),
        expires_at_ms=int(row["lease_expires_at"]),
    )

    view = await services.abandon_round(
        round_id=RoundId(started.round_id), owner=TEST_OWNER
    )
    assert view.step == "abandoned"

    outcome = (await asyncio.gather(running, return_exceptions=True))[0]
    assert isinstance(outcome, asyncio.CancelledError)

    row = _lease_row(db_path, started.round_id)
    assert row["lease_owner"] is None

    connection = database.connect(db_path)
    try:
        scope = LeaseScope()
        scope.bind(old_token)
        token = set_lease_scope(scope)
        try:
            with pytest.raises(LeaseLost):
                SqliteRoundRepository(connection).save(
                    SqliteRoundRepository(connection).get(RoundId(started.round_id))
                )
        finally:
            clear_lease_scope(token)
    finally:
        connection.close()


async def test_short_action_holds_and_releases_the_lease(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(replies={"claims": CLAIMS_REPLY})
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    runner = app.state.services.runner

    started = await runner.run(
        RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
    )

    async def action() -> str | None:
        row = _lease_row(db_path, started.round_id)
        return row["lease_owner"]

    holder = await runner.short_action(RoundId(started.round_id), action)
    assert holder is not None
    assert _lease_row(db_path, started.round_id)["lease_owner"] is None


async def test_heartbeat_renews_and_marks_lost_only_on_token_mismatch(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    connection = database.connect(db_path)
    connection.execute(
        "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
        " created_at, updated_at) VALUES ('r1', 'p1', ?, 'started', 'none', '{}',"
        " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (str(TEST_OWNER),),
    )
    store = SqliteLeaseStore(connection)
    token = store.acquire(
        LeaseKind.ROUND, "r1", TEST_OWNER, "worker-a", wall_clock_ms(), 2_000
    )
    assert token is not None
    scope = LeaseScope()
    scope.bind(token)
    lost: list[bool] = []

    def renew(current: LeaseToken, now_ms: int, ttl_ms: int) -> bool:
        return store.renew(
            current.kind,
            current.target_id,
            TEST_OWNER,
            current.holder,
            current.token,
            now_ms,
            ttl_ms,
        )

    heartbeat = LeaseHeartbeat(
        scope=scope,
        renew=renew,
        interval_seconds=0.01,
        ttl_ms=2_000,
        on_lost=lambda: lost.append(True),
    )

    before = int(
        connection.execute(
            "SELECT lease_expires_at FROM rounds WHERE round_id = 'r1'"
        ).fetchone()[0]
    )
    await asyncio.sleep(0.01)
    assert await heartbeat.beat_once() is True
    after = int(
        connection.execute(
            "SELECT lease_expires_at FROM rounds WHERE round_id = 'r1'"
        ).fetchone()[0]
    )
    assert after > before
    assert scope.lost is False
    assert lost == []

    connection2 = database.connect(db_path)
    try:
        SqliteLeaseStore(connection2).force_acquire(
            LeaseKind.ROUND, "r1", TEST_OWNER, "thief", wall_clock_ms(), 2_000
        )
    finally:
        connection2.close()

    assert await heartbeat.beat_once() is False
    assert scope.lost is True
    assert lost == [True]
    connection.close()


async def test_heartbeat_retries_when_the_database_is_locked(tmp_path) -> None:
    scope = LeaseScope()
    scope.bind(
        LeaseToken(
            kind=LeaseKind.ROUND,
            target_id="r1",
            user_id=str(TEST_OWNER),
            holder="worker-a",
            token=1,
            expires_at_ms=wall_clock_ms() + 5_000,
        )
    )
    delays: list[float] = []
    calls: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    def flaky_renew(current: LeaseToken, now_ms: int, ttl_ms: int) -> bool:
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return True

    heartbeat = LeaseHeartbeat(
        scope=scope,
        renew=flaky_renew,
        interval_seconds=0.01,
        ttl_ms=5_000,
        on_lost=lambda: None,
        sleep=fake_sleep,
    )

    assert await heartbeat.beat_once() is True
    assert len(calls) == 3
    assert delays == sorted(delays)
    assert all(delay > 0 for delay in delays)
    assert scope.lost is False


async def test_current_token_is_a_read_only_borrow(tmp_path) -> None:
    db_path = tmp_path / "pmstudio.db"
    connection = database.connect(db_path)
    connection.execute(
        "INSERT INTO rounds (round_id, project_id, owner_id, step, waiting_on, payload,"
        " created_at, updated_at) VALUES ('r1', 'p1', ?, 'started', 'none', '{}',"
        " '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
        (str(TEST_OWNER),),
    )
    store = SqliteLeaseStore(connection)
    acquired = store.acquire(
        LeaseKind.ROUND, "r1", TEST_OWNER, "worker-a", wall_clock_ms(), 2_000
    )
    assert acquired is not None

    borrowed = store.current_token(
        LeaseKind.ROUND, "r1", TEST_OWNER, now_ms=wall_clock_ms()
    )
    assert borrowed == acquired
    assert store.holds(acquired, now_ms=wall_clock_ms())

    assert store.release(
        LeaseKind.ROUND, "r1", TEST_OWNER, "worker-a", acquired.token
    )
    assert (
        store.current_token(LeaseKind.ROUND, "r1", TEST_OWNER, now_ms=wall_clock_ms())
        is None
    )
    assert current_lease_scope() is None
    connection.close()


def test_round_lease_lost_and_fencing_are_both_409() -> None:
    from pmstudio.api.errors import status_for

    assert status_for(RoundLeaseLost("r1")) == 409
    assert status_for(LeaseLost(kind="round", target_id="r1")) == 409


async def test_external_cancel_keeps_its_cancelled_error(tmp_path) -> None:
    """外部取消（放弃之外的）不被翻成 409；runner 原样抛 `CancelledError`。"""

    db_path = tmp_path / "pmstudio.db"
    gateway = GatedGateway(replies={"claims": CLAIMS_REPLY})
    app = _build(db_path, gateway)
    project_id = await _new_project(app)
    runner = app.state.services.runner

    gateway.block_purposes = {"claims"}
    running = asyncio.create_task(
        runner.run(
            RoundCommand(kind="start", project_id=project_id, user_input="想做个订单导出的功能")
        )
    )
    await asyncio.wait_for(gateway.entered.wait(), timeout=5)

    running.cancel()
    outcome = (await asyncio.gather(running, return_exceptions=True))[0]
    assert isinstance(outcome, asyncio.CancelledError)
    assert _lease_row(db_path, _only_round_id(db_path))["lease_owner"] is None
