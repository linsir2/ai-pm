"""API 路由测试。"""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pmstudio.harness.fake import FakeHarness
from web_api.app import create_app


@pytest.fixture
def client():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    app = create_app(db_path, harness=FakeHarness())
    with TestClient(app) as c:
        yield c


class TestProjects:
    def test_create_project(self, client):
        resp = client.post("/projects", json={"name": "测试项目"})
        assert resp.status_code == 200
        assert "project_id" in resp.json()

    def test_create_project_with_template(self, client):
        resp = client.post(
            "/projects",
            json={"name": "测试项目", "template_id": "reg_tpl_initial"},
        )
        assert resp.status_code == 200


class TestRounds:
    def test_start_round(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        resp = client.post(
            "/rounds",
            json={"project_id": proj["project_id"], "user_input": "细化功能清单"},
        )
        assert resp.status_code == 200
        assert "round_id" in resp.json()

    def test_start_round_with_scope(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        resp = client.post(
            "/rounds",
            json={
                "project_id": proj["project_id"],
                "user_input": "细化功能清单",
                "selected_fields": ["功能清单", "目标"],
            },
        )
        assert resp.status_code == 200

    def test_start_round_no_project_returns_400(self, client):
        resp = client.post(
            "/rounds",
            json={"project_id": "nonexistent", "user_input": "细化功能清单"},
        )
        assert resp.status_code == 400

    def test_get_round(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        rnd = client.post(
            "/rounds",
            json={"project_id": proj["project_id"], "user_input": "细化功能清单"},
        ).json()
        resp = client.get(f"/rounds/{rnd['round_id']}")
        assert resp.status_code == 200
        assert resp.json()["round_id"] == rnd["round_id"]

    def test_get_round_not_found(self, client):
        resp = client.get("/rounds/nonexistent")
        assert resp.status_code == 400


class TestSubmitCards:
    def test_submit_understanding_card(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        rnd = client.post(
            "/rounds",
            json={"project_id": proj["project_id"], "user_input": "细化功能清单"},
        ).json()
        grp = client.get(f"/rounds/{rnd['round_id']}").json()
        card_id = grp["card_group"]["cards"][0]["card_id"]
        resp = client.post(
            f"/card-groups/{grp['card_group']['group_id']}/submit",
            json={
                "answers": [
                    {"card_id": card_id, "verdict": "confirm", "status": "answered"}
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["cards"][0]["kind"] == "fill"

    def test_submit_fill_card(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        rnd = client.post(
            "/rounds",
            json={"project_id": proj["project_id"], "user_input": "细化功能清单"},
        ).json()
        grp = client.get(f"/rounds/{rnd['round_id']}").json()
        card_id = grp["card_group"]["cards"][0]["card_id"]
        resp1 = client.post(
            f"/card-groups/{grp['card_group']['group_id']}/submit",
            json={
                "answers": [
                    {"card_id": card_id, "verdict": "confirm", "status": "answered"}
                ]
            },
        )
        fill_group = resp1.json()
        fill_card_id = fill_group["cards"][0]["card_id"]
        proposals = fill_group["cards"][0]["proposals"]
        resp2 = client.post(
            f"/card-groups/{fill_group['group_id']}/submit",
            json={
                "answers": [
                    {
                        "card_id": fill_card_id,
                        "verdict": "confirm",
                        "status": "answered",
                        "proposal_states": {p["proposal_id"]: "kept" for p in proposals},
                    }
                ]
            },
        )
        assert resp2.status_code == 200

    def test_submit_no_group_returns_400(self, client):
        resp = client.post(
            "/card-groups/nonexistent/submit",
            json={"answers": []},
        )
        assert resp.status_code == 400


class TestDocuments:
    def test_get_document(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        doc_id = proj.get("doc_id", "")
        if not doc_id:
            pytest.skip("create_project 不返回 doc_id")
        resp = client.get(f"/documents/{doc_id}")
        assert resp.status_code == 200

    def test_get_document_not_found(self, client):
        resp = client.get("/documents/nonexistent")
        assert resp.status_code == 400

    def test_get_document_versions(self, client):
        proj = client.post("/projects", json={"name": "测试项目"}).json()
        doc_id = proj.get("doc_id", "")
        if not doc_id:
            pytest.skip("create_project 不返回 doc_id")
        resp = client.get(f"/documents/{doc_id}/versions")
        assert resp.status_code == 200
        assert "versions" in resp.json()


class TestErrors:
    def test_contract_violation_returns_400(self, client):
        resp = client.post(
            "/rounds",
            json={"project_id": "nonexistent", "user_input": "test"},
        )
        assert resp.status_code == 400
        assert "detail" in resp.json()

    def test_invalid_json_returns_422(self, client):
        resp = client.post("/projects", json={"invalid_field": "test"})
        assert resp.status_code == 422
