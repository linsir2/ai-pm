import pytest

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import Settings
from tests.support.api_client import async_client


async def test_container_wires_everything_and_serves_health(tmp_path):
    settings = Settings(
        db_path=tmp_path / "pmstudio.db",
        model="openai/gpt-4o-mini",
        api_key="sk-test",
        api_base=None,
        # Task 6 起业务端点要身份：这个"装配齐全"用例种一个首管理员，登录后再建项目。
        first_admin_username="root",
        first_admin_password="root-s3cret",
    )

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


def test_container_creates_the_database_file(tmp_path):
    settings = Settings(
        db_path=tmp_path / "nested" / "pmstudio.db", model="m", api_key="k", api_base=None
    )
    build_container(settings)
    assert (tmp_path / "nested" / "pmstudio.db").exists()


def test_container_has_no_fake_model_switch(tmp_path):
    """生产容器里不许有"用假模型 / 无密钥降级"的开关。"""

    settings = Settings(db_path=tmp_path / "p.db", model="m", api_key="k", api_base=None)
    container = build_container(settings)
    with pytest.raises(AttributeError):
        _ = container.use_fake_model  # type: ignore[attr-defined]


def test_container_warms_the_chinese_tokenizer(tmp_path, monkeypatch):
    """R（检索 P1）：组装点预热中文分词——jieba 首次 cut ~0.6s，别落在首个请求里。"""

    import pmstudio.bootstrap.container as container_module

    calls: list[int] = []
    monkeypatch.setattr(container_module, "warm_tokenizer", lambda: calls.append(1))

    build_container(
        Settings(db_path=tmp_path / "pm.db", model="m", api_key="k", api_base=None)
    )

    assert calls == [1], "组装没有预热分词器"
