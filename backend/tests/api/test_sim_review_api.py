"""模拟评测 API（M11-T6）：端点形状与错误映射。

路由是薄封装（同 `/dig`、`/sim-inquiry`）：真正的动作在 `RunSimReview` 与图的路由里。
这里只证明"端点通、请求体到得了用例、错误码对"。
"""


from pmstudio.api.app import create_app
from tests.application.test_sim_review_use_cases import (  # noqa: F401
    PERSONAS,
    _all_good,
    _harness,
    _round_on_draft_card,
)
from tests.support.api_client import async_client


async def test_sim_review_endpoint_runs_and_returns_round_view() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    app = create_app(harness.api_services(model_name="scripted"))

    async with async_client(app) as client:
        response = await client.post(
            f"/api/rounds/{round_id}/sim-review",
            json={"persona_ids": list(PERSONAS), "feature_slugs": ["order-export"]},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["round_id"] == round_id
    assert "round.updated" in harness.events.names
    assert harness.rounds.get(round_id).sim_review_runs == 1


async def test_sim_review_endpoint_rejects_unknown_persona() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    app = create_app(harness.api_services(model_name="scripted"))

    async with async_client(app) as client:
        response = await client.post(
            f"/api/rounds/{round_id}/sim-review",
            json={"persona_ids": ["persona.nobody"], "feature_slugs": ["order-export"]},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "shape_error"


async def test_sim_review_endpoint_rejects_when_not_on_draft_card() -> None:
    harness = _harness({}, _all_good())
    from tests.support.builders import PROJECT

    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    app = create_app(harness.api_services(model_name="scripted"))

    async with async_client(app) as client:
        response = await client.post(
            f"/api/rounds/{round_id}/sim-review",
            json={"persona_ids": list(PERSONAS), "feature_slugs": ["order-export"]},
        )

    assert response.status_code == 409


async def test_sim_review_second_run_returns_409() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    app = create_app(harness.api_services(model_name="scripted"))

    async with async_client(app) as client:
        first = await client.post(
            f"/api/rounds/{round_id}/sim-review",
            json={"persona_ids": list(PERSONAS), "feature_slugs": ["order-export"]},
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/rounds/{round_id}/sim-review",
            json={"persona_ids": list(PERSONAS), "feature_slugs": ["order-export"]},
        )

    assert second.status_code == 409
    assert second.json()["code"] == "invariant:R9"
