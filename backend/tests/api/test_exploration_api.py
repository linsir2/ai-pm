"""探索端点（M8）：发起 / 列表 / 重试 / 放弃 / 收下 / 删掉的状态码与形状。"""

import json

from pmstudio.api.app import create_app
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ScopeKind,
)
from pmstudio.domain.shared.ids import ExplorationId
from tests.support.api_client import async_client
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness


def _reply() -> str:
    return json.dumps(
        {
            "basis": "读了 s5",
            "hypotheses": [
                {
                    "local_id": "H1",
                    "statement": "运营按客户名找单",
                    "grounding": "invented",
                    "based_on": [],
                    "falsification": "访谈没人这么找",
                    "evidence_needed": "两个访谈",
                }
            ],
            "blind_spots": [
                {"local_id": "B1", "statement": "归属未知", "next_step": "问数据组"}
            ],
        },
        ensure_ascii=False,
    )


def _app(*, filled: bool = True, replies: dict | None = None):  # type: ignore[no-untyped-def]
    harness = Harness(replies=replies or {"inquiry": _reply()}, seat_replies={})
    harness.seed_project()
    if filled:
        harness.documents.seed(document_with_feature())
    return create_app(harness.api_services(model_name="m")), harness


async def test_start_list_and_keep_flow() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "done"
        assert [item["candidate_id"] for item in body["candidates"]] == ["H1", "B1"]

        xid = body["exploration_id"]
        listed = await client.get(f"/api/projects/{PROJECT}/explorations")
        assert listed.status_code == 200
        assert [item["exploration_id"] for item in listed.json()] == [xid]

        kept = await client.post(
            f"/api/explorations/{xid}/candidates/H1/keep", json={"as": "material"}
        )
        assert kept.status_code == 200, kept.text
        candidate = kept.json()["candidates"][0]
        assert candidate["kept_as"] == "material"
        assert candidate["kept_ref"]


async def test_empty_document_is_refused_with_422() -> None:
    app, _ = _app(filled=False)
    async with async_client(app) as client:
        response = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
    assert response.status_code == 422
    assert response.json()["code"] == "shape_error"


async def test_unknown_entry_is_404() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        response = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "entry", "ref": "entry-nope"}},
        )
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_entry"


async def test_unknown_exploration_is_404() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        response = await client.post(
            "/api/explorations/x-9/candidates/H1/keep", json={"as": "seed"}
        )
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_exploration"


async def test_keep_after_dismiss_is_409() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        xid = started.json()["exploration_id"]
        dismissed = await client.post(f"/api/explorations/{xid}/candidates/B1/dismiss")
        assert dismissed.status_code == 200

        kept = await client.post(
            f"/api/explorations/{xid}/candidates/B1/keep", json={"as": "seed"}
        )
    assert kept.status_code == 409
    assert kept.json()["code"].startswith("invariant:")


async def test_retry_a_finished_run_is_409() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        xid = started.json()["exploration_id"]
        response = await client.post(f"/api/explorations/{xid}/retry")
    assert response.status_code == 409
    assert response.json()["code"].startswith("invariant:")


async def test_keep_with_a_bad_destination_is_422() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        xid = started.json()["exploration_id"]
        response = await client.post(
            f"/api/explorations/{xid}/candidates/H1/keep", json={"as": "trash"}
        )
    assert response.status_code == 422


async def test_abandon_a_running_stale_run() -> None:
    app, harness = _app()
    stale = Exploration.start(
        exploration_id=ExplorationId("x-stale"),
        project_id=PROJECT,
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=harness.clock.now(),
    )
    harness.explorations.seed(stale)

    async with async_client(app) as client:
        response = await client.post("/api/explorations/x-stale/abandon")

    assert response.status_code == 200
    assert response.json()["status"] == "abandoned"
