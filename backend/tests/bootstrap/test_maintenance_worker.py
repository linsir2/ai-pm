"""独立的材料维护 job：直连 PostgreSQL，try-lock 忙就跳过。"""

from __future__ import annotations

import pytest
from scripts import maintenance_worker
from scripts.migrate import apply_migrations

from pmstudio.infrastructure.postgres.database import connect
from tests.support.postgres import isolated_schema, load_environment, prepare


@pytest.fixture()
def direct_url() -> str:
    database = isolated_schema(load_environment(), "t7_maintenance")
    prepare(database)
    apply_migrations(database.direct_url)
    return database.direct_url


def test_maintenance_worker_skips_when_another_run_holds_the_lock(
    direct_url: str, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("PMSTUDIO_DIRECT_DATABASE_URL", direct_url)
    holder = connect(direct_url)
    holder.execute(
        "SELECT pg_advisory_lock(%s)", (maintenance_worker.MAINTENANCE_LOCK_KEY,)
    )
    try:
        assert maintenance_worker.run() == 0
        assert "跳过" in capsys.readouterr().out
    finally:
        holder.execute(
            "SELECT pg_advisory_unlock(%s)", (maintenance_worker.MAINTENANCE_LOCK_KEY,)
        )
        holder.close()


def test_maintenance_worker_runs_on_an_empty_database(
    direct_url: str, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("PMSTUDIO_DIRECT_DATABASE_URL", direct_url)
    assert maintenance_worker.run() == 0
    assert "重切 0 份" in capsys.readouterr().out
