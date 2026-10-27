"""PostgreSQL SQLSTATE → HTTP，以及 `/api/ready`。

SQLSTATE 映射是纯函数，另有两条真库/真 handler 的集成路径：
- 锁等待（55P03）→ 503 + Retry-After（真 `LOCK TABLE`）；
- 53100 → 507，且**不带** Retry-After（构造对应类型的 psycopg 错误，走真 handler）。
"""

from __future__ import annotations

import httpx
import pytest
from psycopg.errors import (
    AdminShutdown,
    ConnectionFailure,
    DeadlockDetected,
    DiskFull,
    InFailedSqlTransaction,
    LockNotAvailable,
    QueryCanceled,
    SerializationFailure,
)

from pmstudio.api.app import create_app
from pmstudio.api.errors import error_for_sqlstate, status_for
from pmstudio.harness.errors import DatabaseBusy, DatabaseUnavailable, StorageFull
from pmstudio.infrastructure.postgres.database import PgConnection, connect
from tests.support.harness import Harness
from tests.support.postgres import PgTestDatabase, load_environment, prepare, truncate_data_tables


def _services():
    return Harness(replies={}, seat_replies={}).api_services(model_name="m")


@pytest.mark.parametrize(
    "error",
    [
        LockNotAvailable("lock"),
        SerializationFailure("serialization"),
        DeadlockDetected("deadlock"),
        QueryCanceled("timeout"),
    ],
)
def test_retryable_states_map_to_503_with_retry_after(error) -> None:
    mapped = error_for_sqlstate(error)
    assert isinstance(mapped, DatabaseBusy)
    assert status_for(mapped) == 503
    assert mapped.retry_after > 0


@pytest.mark.parametrize(
    "error",
    [
        ConnectionFailure("connection failure"),
        AdminShutdown("terminated"),
        InFailedSqlTransaction("aborted"),
    ],
)
def test_connection_and_aborted_states_map_to_503(error) -> None:
    mapped = error_for_sqlstate(error)
    assert isinstance(mapped, DatabaseUnavailable)
    assert status_for(mapped) == 503


def test_disk_full_maps_to_507_without_retry_after() -> None:
    mapped = error_for_sqlstate(DiskFull("disk full"))
    assert isinstance(mapped, StorageFull)
    assert status_for(mapped) == 507
    assert mapped.retry_after is None


def test_unknown_sqlstate_is_not_guessed_from_text() -> None:
    class Weird(Exception):
        sqlstate = "XX000"

    assert error_for_sqlstate(Weird("database is locked; disk full")) is None


async def test_ready_probe_success_and_failure() -> None:
    app = create_app(_services(), readiness=lambda: True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ready") as client:
        ok = await client.get("/api/ready")
        assert ok.status_code == 200
        assert ok.json() == {"status": "ready"}

    def boom() -> None:
        raise RuntimeError("db down")

    broken = create_app(_services(), readiness=boom)
    transport = httpx.ASGITransport(app=broken)
    async with httpx.AsyncClient(transport=transport, base_url="http://ready") as client:
        failed = await client.get("/api/ready")
        assert failed.status_code == 503
        assert failed.json()["status"] == "not_ready"


async def test_ready_without_a_probe_is_503_not_healthy() -> None:
    app = create_app(_services())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ready") as client:
        response = await client.get("/api/ready")
    assert response.status_code == 503


async def test_lock_timeout_sqlstate_returns_503_with_retry_after() -> None:
    database: PgTestDatabase = load_environment()
    prepare(database)
    truncate_data_tables(database)
    with connect(database.direct_url) as setup:
        setup.execute(
            f'CREATE TABLE IF NOT EXISTS "{database.schema}".lock_probe (value text)'
        )
    blocker = connect(database.direct_url)
    wrapper = PgConnection(database.direct_url)
    app = create_app(_services())

    @app.get("/probe-lock")
    async def probe_lock():  # type: ignore[no-untyped-def]
        wrapper.execute("SET lock_timeout = '150ms'")
        wrapper.execute(
            f'INSERT INTO "{database.schema}".lock_probe (value) VALUES (%s)', ("x",)
        )
        return {"ok": True}

    try:
        blocker.execute("BEGIN")
        blocker.execute(
            f'LOCK TABLE "{database.schema}".lock_probe IN ACCESS EXCLUSIVE MODE'
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://lock") as client:
            response = await client.get("/probe-lock")
        assert response.status_code == 503
        assert response.json()["code"] == "database_busy"
        assert response.headers.get("Retry-After") == "3"
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
        wrapper.close()
        with connect(database.direct_url) as cleanup:
            cleanup.execute(f'DROP TABLE IF EXISTS "{database.schema}".lock_probe')


async def test_disk_full_handler_returns_507_without_retry_after() -> None:
    app = create_app(_services())

    @app.get("/probe-disk")
    async def probe_disk():  # type: ignore[no-untyped-def]
        raise DiskFull("could not extend file: No space left on device")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://disk") as client:
        response = await client.get("/probe-disk")
    assert response.status_code == 507
    assert response.json()["code"] == "storage_full"
    assert "Retry-After" not in response.headers
