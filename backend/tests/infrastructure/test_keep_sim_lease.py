"""KeepSim 走 `RoundRunner.short_action`：真 PostgreSQL 上不再因 fencing 500。"""

import os
from datetime import UTC, datetime

from pmstudio.bootstrap.container import build_container
from pmstudio.bootstrap.settings import AppSettings
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.ids import ProjectId, RoundId
from pmstudio.infrastructure.sqlite.repositories import SqliteRoundRepository
from tests.support.doubles import ScriptedModelGateway
from tests.support.postgres import open_direct

CLAIMS_REPLY = '{"basis": "读了空文档", "claims": [], "seats": []}'
HYPOTHESIS = {
    "local_id": "H1",
    "slice_id": "S1",
    "statement": "运营宁愿等一分钟也不愿分两次导",
    "grounding": "document",
    "based_on": [],
    "falsification": "观察运营主动分批导出",
    "evidence_needed": "跟两场月底结账",
}


def _app():  # type: ignore[no-untyped-def]
    settings = AppSettings(
        database_url=os.environ["PMSTUDIO_DATABASE_URL"],
        model="scripted",
        api_key="test-key",
        api_base=None,
    )
    gateway = ScriptedModelGateway(replies={"claims": CLAIMS_REPLY})
    from pmstudio.infrastructure.postgres.repositories import SqliteTemplateRepository
    from pmstudio.infrastructure.templates.loader import seed_builtin_templates

    with open_direct() as connection:
        seed_builtin_templates(SqliteTemplateRepository(connection))
    return build_container(settings, gateway=gateway).app


async def test_keep_sim_runs_under_the_round_lease_and_does_not_double_write() -> None:
    app = _app()
    services = app.state.services
    summary = await services.create_project(title="模拟收下")
    project_id = summary.project_id
    round_id = RoundId("r-keep")
    round_ = Round(
        round_id=round_id,
        project_id=ProjectId(project_id),
        user_input="模拟",
        created_at=datetime.now(UTC),
        step=Step.AWAITING_CONFIRM,
        waiting_on=WaitingOn.DRAFT,
        sim_slices=({"local_id": "S1", "situation": "月底对账"},),
        sim_hypotheses=(HYPOTHESIS,),
    )

    connection = open_direct()
    try:
        SqliteRoundRepository(connection).save(round_)
    finally:
        connection.close()

    async def action():  # type: ignore[no-untyped-def]
        return await services.keep_sim_inquiry(round_id=round_id, chosen=("H1",))

    first = await services.runner.short_action(round_id, action)
    second = await services.runner.short_action(round_id, action)

    assert first.material_id == second.material_id
    assert first.kept == ("H1",)
    materials = await services.list_materials(project_id=ProjectId(project_id))
    assert len(materials) == 1
    connection = open_direct()
    try:
        stored = SqliteRoundRepository(connection).get(round_id)
    finally:
        connection.close()
    assert stored.sim_inquiry_chosen == ("H1",)
