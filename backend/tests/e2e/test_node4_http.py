import pytest

from pmstudio.domain.shared.ids import EntryId
from pmstudio.domain.shared.writing import WriteOp
from tests.e2e.test_node3_confirm_http import RID, proposal, write
from tests.e2e.test_node3_confirm_http import scenario as scenario
from tests.support.api_client import async_client
from tests.support.builders import NOW


@pytest.mark.parametrize("base", [2, None, 99])
async def test_real_http_and_pg_projection_uses_history_and_preserves_rows(scenario, base):
    scenario.apply(write(entry="old#01", text="真实旧文"))
    scenario.apply(write(entry="old#01", text="后来的正文", op=WriteOp.REPLACE))
    scenario.card(proposal(entry="old#01", base=base))
    rows = scenario.rows()
    async with async_client(scenario.app) as client:
        response = await client.get(f"/api/rounds/{RID}")
        assert response.status_code == 200, response.text
        card = response.json()["draft"]["proposals"][0]
        assert card["base_version_no"] == base
        assert card["before"] == (
            {"entry_id": "old#01", "text": "真实旧文", "refs": ["E1"]} if base == 2 else None
        )
        assert card["requires_review"] is True
        assert ("proposal_stale" if base == 2 else "baseline_missing") in card["warning"]
        workspace = await client.get(f"/api/projects/{scenario.project}/workspace")
        assert workspace.status_code == 200, workspace.text
        assert workspace.json()["round"]["draft"]["proposals"][0] == card
    assert scenario.rows() == rows


async def test_http_confirm_done_card_keeps_old_before_after_further_pg_write(scenario):
    scenario.apply(write(entry="old#01", text="真实旧文"))
    scenario.card(proposal(entry="old#01", text="用户确认的新文"))
    response = await scenario.confirm()
    assert response.status_code == 200, response.text
    card = response.json()["round"]["draft"]["proposals"][0]
    assert card["before"]["text"] == "真实旧文"
    assert not card["requires_review"]
    document = scenario.documents.get(scenario.project)
    document.apply((write(entry="old#01", text="下一轮又改", op=WriteOp.REPLACE),),
                   now=NOW, summary="测试后续版本")
    scenario.documents.save(document)
    async with async_client(scenario.app) as client:
        done = (await client.get(f"/api/rounds/{RID}")).json()
    assert done["step"] == "done" and done["waiting_on"] == "none"
    assert done["draft"]["proposals"][0]["before"]["text"] == "真实旧文"
    assert not done["draft"]["proposals"][0]["requires_review"]
    assert document.locate_entry(EntryId("old#01")).entry.text == "下一轮又改"
