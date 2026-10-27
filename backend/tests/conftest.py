"""全局测试夹具：真 PostgreSQL + 默认 owner 上下文。

T8 起**全量测试直连 PG**：一个 test database、一个 test schema，pytest 串行。
- session 一次：建 schema、装 pgvector、跑迁移、种 vector profile；
- 每个用例前：只 `TRUNCATE` 显式数据表（保留 `schema_migrations` /
  `vector_index_meta`），truncate 后重新 upsert profile；内置模板由需要它的
  用例自己 seed（backend 不 seed，生产由 init --finalize 负责）；
- 两个测试 URL 双写成生产变量，子进程（uvicorn / CLI）直接继承；
- 缺 URL 或连不上直接 `pytest.exit`，不回落 SQLite。
"""

import os

import pytest
from scripts.migrate import apply_migrations

from pmstudio.application.lease import clear_lease_scope
from pmstudio.domain.shared.ids import UserId
from pmstudio.infrastructure.postgres import owner_scope
from tests.support.postgres import (
    install_environment,
    load_environment,
    prepare,
    seed_defaults,
    truncate_data_tables,
)

#: 测试默认 owner。真值不重要，只要跨调用稳定——写入盖章与读取过滤用同一个值。
TEST_OWNER = UserId("u-owner-scope-test")


@pytest.fixture(scope="session", autouse=True)
def pg_test_database():  # type: ignore[no-untyped-def]
    """一次建库/迁移；URL 写进环境供子进程继承。缺 URL 在 load_environment 退出。"""

    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.exit("测试夹具是单 schema + 串行 truncate：禁止 xdist", returncode=2)
    database = load_environment()
    prepare(database)
    apply_migrations(database.direct_url)
    seed_defaults(database)
    install_environment(database)
    return database


@pytest.fixture(autouse=True)
def clean_pg_database(pg_test_database):  # type: ignore[no-untyped-def]
    """每个用例前清空显式数据表；不清迁移账本与向量口径。"""

    truncate_data_tables(pg_test_database)
    seed_defaults(pg_test_database)
    yield


@pytest.fixture(autouse=True)
def default_owner_scope():  # type: ignore[no-untyped-def]
    token = owner_scope.set_owner_scope(TEST_OWNER)
    yield TEST_OWNER
    owner_scope.clear_owner_scope(token)


@pytest.fixture(autouse=True)
def default_lease_scope_is_clear():  # type: ignore[no-untyped-def]
    """每个用例结束后清空租约 contextvar——测试补的租约不跨用例泄漏。"""

    yield
    clear_lease_scope()
