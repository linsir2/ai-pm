import os

import pytest

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import AppSettings
from tests.support.api_client import async_client
from tests.support.postgres import open_direct


def _settings(**overrides) -> AppSettings:  # type: ignore[no-untyped-def]
    return AppSettings(
        database_url=os.environ["PMSTUDIO_DATABASE_URL"],
        model="openai/gpt-4o-mini",
        api_key="sk-test",
        api_base=None,
        **overrides,
    )


async def test_container_wires_everything_and_serves_health():
    from pmstudio.infrastructure.postgres.repositories import SqliteTemplateRepository
    from pmstudio.infrastructure.templates.loader import seed_builtin_templates

    with open_direct() as connection:
        seed_builtin_templates(SqliteTemplateRepository(connection))
        from pmstudio.infrastructure.postgres.users import SqliteUserRepository
        from pmstudio.infrastructure.security import hash_password

        users = SqliteUserRepository(connection)
        user_id = users.register(
            username="root", password_hash=hash_password("root-s3cret"), role="admin"
        )
        users.set_status(user_id, "active")
    settings = _settings()
    container = build_container(settings)

    async with async_client(container.app) as client:
        health = await client.get("/api/health")
        login = await client.post(
            "/api/auth/login", json={"username": "root", "password": "root-s3cret"}
        )
        assert login.status_code == 200
        client.cookies = login.cookies
        created = await client.post("/api/projects", json={"title": "订单中台"})

    assert health.json() == {
            "status": "ok",
            "model": "openai/gpt-4o-mini",
        }
    assert created.status_code == 201


def test_container_has_no_fake_model_switch():
    """生产容器里不许有"用假模型 / 无密钥降级"的开关。"""

    container = build_container(_settings())
    with pytest.raises(AttributeError):
        _ = container.use_fake_model  # type: ignore[attr-defined]


def test_container_warms_the_chinese_tokenizer(monkeypatch):
    """R（检索 P1）：组装点预热中文分词——jieba 首次 cut ~0.6s，别落在首个请求里。"""

    import pmstudio.bootstrap.container as container_module

    calls: list[int] = []
    monkeypatch.setattr(container_module, "warm_tokenizer", lambda: calls.append(1))

    build_container(_settings())

    assert calls == [1], "组装没有预热分词器"


def test_search_project_uses_keyword_retrieval_only():
    """P1.1：顶栏搜索走**纯 FTS**，不再碰 embedding/融合。

    这是**接线守卫**（不是业务断言）：`SearchProject` 只依赖 `MaterialRetriever` 协议，
    换实现应该在装配点一行完成；这里直接看装配进去的实例类型。
    """

    from pmstudio.application.use_cases import SearchProject
    from pmstudio.infrastructure.postgres.materials import SqliteFtsRetriever

    container = build_container(_settings())

    search = container.app.state.services.search
    assert isinstance(search, SearchProject)
    assert isinstance(search._materials, SqliteFtsRetriever)
