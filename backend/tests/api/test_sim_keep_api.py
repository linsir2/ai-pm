"""模拟收下的端点：经 `RoundRunner.short_action` 回包保留 `KeepSimResult`，重复不双写。"""

from pmstudio.api.app import create_app
from tests.application.test_sim_use_cases import (
    CLAIMS_REPLY,
    DRAFT_REPLY,
    SEAT_REPLY,
    _round_on_draft_card,
    _sim_reply,
)
from tests.support.api_client import async_client
from tests.support.builders import SEAT
from tests.support.harness import Harness


async def test_keep_sim_endpoint_returns_result_and_is_idempotent() -> None:
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "sim_inquiry": _sim_reply(),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await _round_on_draft_card(harness)
    await harness.run_sim_inquiry()(round_id=round_id)
    app = create_app(harness.api_services(model_name="scripted"))

    async with async_client(app) as client:
        first = await client.post(
            f"/api/rounds/{round_id}/sim-inquiry/keep", json={"chosen": ["H1"]}
        )
        second = await client.post(
            f"/api/rounds/{round_id}/sim-inquiry/keep", json={"chosen": ["H1"]}
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["material_id"] == second.json()["material_id"]
    assert harness.materials.count_for_project(
        harness.rounds.get(round_id).project_id
    ) == 1
