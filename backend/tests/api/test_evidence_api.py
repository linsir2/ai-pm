"""出处查询端点：界面跨轮跳转靠它（`04` F5）。"""

import pytest

from pmstudio.api.app import create_app
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.shared.ids import RoundId
from tests.support.api_client import async_client
from tests.support.builders import NOW, PROJECT
from tests.support.harness import Harness


@pytest.fixture()
def app():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    ledger = harness.evidences.get(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    harness.evidences.save(ledger)
    return create_app(harness.api_services(model_name="m"))


async def test_evidence_lookup_endpoint(app):
    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/evidence/E1")

    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["kind_label"], body["round_id"]) == (
        "user_input",
        "用户输入",
        "r1",
    )
    assert body["raw_span"] == "想做个订单导出的功能"
    assert body["used_by"] == []
    assert [item["eid"] for item in body["chain"]] == ["E1"]


async def test_unknown_evidence_is_404(app):
    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/evidence/E404")

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_evidence"


async def test_external_evidence_exposes_where_it_came_from():
    """材料的出处要能说清"来自哪份材料的哪一块 / 哪个 URL"（工具层 C-2 的证据链）。"""

    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    ledger = harness.evidences.get(PROJECT)
    ledger.record(
        kind=EvidenceKind.EXTERNAL,
        text="目标：导出要有权限",
        source=EvidenceSource(external_ref="attachment:需求.md#c00"),
        now=NOW,
    )
    harness.evidences.save(ledger)

    async with async_client(create_app(harness.api_services(model_name="m"))) as client:
        response = await client.get(f"/api/projects/{PROJECT}/evidence/E1")

    body = response.json()
    assert (body["kind"], body["kind_label"]) == ("external", "外部材料")
    assert body["external_ref"] == "attachment:需求.md#c00"
