"""真 PgBouncer（transaction mode）smoke：连接、池、SET LOCAL、prepared。

这些用例需要真 PgBouncer 与 init 建好的角色/auth_file：

    PMSTUDIO_TEST_PGBOUNCER_URL=postgresql://pmstudio_app:…@127.0.0.1:6432/pmstudio
    PMSTUDIO_TEST_PGBOUNCER_ADMIN_URL=postgresql://pmstudio_owner:…@127.0.0.1:6432/pgbouncer

缺任一个就 skip（**不拿直连假装通过**）；CI 里 skip 视为失败，由 T8 的 job 保证。
"""

from __future__ import annotations

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from pmstudio.infrastructure.postgres.database import connect

APP_URL = os.environ.get("PMSTUDIO_TEST_PGBOUNCER_URL", "").strip()
ADMIN_URL = os.environ.get("PMSTUDIO_TEST_PGBOUNCER_ADMIN_URL", "").strip()


@pytest.fixture(scope="module")
def pgbouncer_urls() -> tuple[str, str]:
    if not APP_URL or not ADMIN_URL:
        pytest.skip(
            "缺 PMSTUDIO_TEST_PGBOUNCER_URL / PMSTUDIO_TEST_PGBOUNCER_ADMIN_URL"
        )
    return APP_URL, ADMIN_URL


def test_connects_through_transaction_pool(pgbouncer_urls) -> None:
    app_url, admin_url = pgbouncer_urls
    with connect(app_url) as connection:
        assert connection.prepare_threshold is None
        assert connection.execute("SELECT 1 AS ok").fetchone() == {"ok": 1}
        assert connection.execute("SHOW timezone").fetchone() == {"TimeZone": "UTC"}
    # admin console 不支持 psycopg 的向量注册探测；运维入口用裸连接。
    admin = psycopg.connect(
        admin_url, autocommit=True, row_factory=dict_row, prepare_threshold=None
    )
    try:
        pools = admin.execute("SHOW POOLS").fetchall()
        assert pools, "SHOW POOLS 没有返回行——admin console 没连上"
        assert any(str(row.get("pool_mode")) == "transaction" for row in pools)
        config = {
            str(row["key"]): str(row["value"])
            for row in admin.execute("SHOW CONFIG").fetchall()
        }
        assert config.get("pool_mode") == "transaction"
        assert config.get("max_prepared_statements") == "0"
        assert config.get("admin_users") == "pmstudio_owner"
    finally:
        admin.close()


def test_set_local_does_not_leak_between_transactions(pgbouncer_urls) -> None:
    app_url, _ = pgbouncer_urls
    with connect(app_url) as connection:
        baseline = connection.execute("SHOW application_name").fetchone()
        assert baseline is not None
        connection.execute("BEGIN")
        connection.execute("SET LOCAL application_name = 'pmstudio-leak-probe'")
        assert connection.execute("SHOW application_name").fetchone() == {
            "application_name": "pmstudio-leak-probe"
        }
        connection.execute("COMMIT")
        after = connection.execute("SHOW application_name").fetchone()
        assert after == baseline, f"SET LOCAL 漏到了下一个事务：{after}"


def test_prepared_statements_are_disabled(pgbouncer_urls) -> None:
    app_url, _ = pgbouncer_urls
    with connect(app_url) as connection:
        for _ in range(3):
            connection.execute("SELECT %s::int AS value", (1,)).fetchone()
        prepared = connection.execute(
            "SELECT count(*) AS n FROM pg_prepared_statements"
        ).fetchone()
        assert prepared == {"n": 0}, (
            "transaction mode 下不能有服务端预编译语句"
            "（prepare_threshold 必须是 None）"
        )
