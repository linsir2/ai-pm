"""版本端点：列表、diff、回退。回退写的是**新**版本，旧版本一个都不删（`04` D6）。"""

import pytest

from pmstudio.api.app import create_app
from tests.support.api_client import async_client
from tests.support.builders import PROJECT
from tests.support.harness import Harness


@pytest.fixture()
def app():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    return create_app(harness.api_services(model_name="m")), harness


async def test_list_versions_and_diff(app):
    application, _ = app

    async with async_client(application) as client:
        listed = await client.get(f"/api/projects/{PROJECT}/versions")
        diffed = await client.get(f"/api/projects/{PROJECT}/versions/1/diff?to=1")

    assert listed.status_code == 200
    body = listed.json()
    assert body["current"] == 1
    assert body["versions"][0]["trigger_label"] == "手工编辑"
    assert diffed.status_code == 200
    assert diffed.json()["groups"] == []


async def test_diff_without_to_is_rejected(app):
    application, _ = app

    async with async_client(application) as client:
        response = await client.get(f"/api/projects/{PROJECT}/versions/1/diff")

    assert response.status_code == 422  # FastAPI 的查询参数校验：`to` 是必填


async def test_diff_of_unknown_version_is_404(app):
    application, _ = app

    async with async_client(application) as client:
        response = await client.get(f"/api/projects/{PROJECT}/versions/1/diff?to=99")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_version"


async def test_rollback_returns_the_new_document(app):
    application, harness = app

    async with async_client(application) as client:
        response = await client.post(f"/api/projects/{PROJECT}/versions/1/rollback")

    assert response.status_code == 200
    body = response.json()
    assert body["version_no"] == 2
    assert body["document"]["version_no"] == 2
    assert len(harness.documents.get(PROJECT).versions) == 2


async def test_rollback_to_unknown_version_is_404(app):
    application, _ = app

    async with async_client(application) as client:
        response = await client.post(f"/api/projects/{PROJECT}/versions/99/rollback")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_version"
