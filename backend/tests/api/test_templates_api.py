"""模板库 API：列模板 / 建模板 / 取模板 / 建项目时选模板。"""

from pmstudio.api.app import create_app
from tests.support.api_client import async_client
from tests.support.harness import Harness


def make_client():  # type: ignore[no-untyped-def]
    harness = Harness(replies={})
    return create_app(harness.api_services(model_name="openai/gpt-4o-mini")), harness


def triple_sections():
    return [
        {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
        {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
        {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
    ]


async def test_list_templates_returns_seeded_builtins():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/templates")

    assert response.status_code == 200
    ids = [item["template_id"] for item in response.json()]
    assert "standard" in ids and "light" in ids


async def test_create_template_then_create_project_with_it():
    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post(
            "/api/templates", json={"title": "自建", "sections": triple_sections()}
        )
        assert created.status_code == 201
        template_id = created.json()["template_id"]
        # id 服务端盖（调用方不许自选）：测试缝是顺序号、生产是 uuid，只断前缀族。
        assert template_id.startswith("tpl")

        got = await client.get(f"/api/templates/{template_id}")
        assert got.status_code == 200
        assert len(got.json()["sections"]) == 3

        project = await client.post(
            "/api/projects", json={"title": "轻项目", "template_id": template_id}
        )
        assert project.status_code == 201
        workspace = await client.get(
            f"/api/projects/{project.json()['project_id']}/workspace"
        )
        # 切片 1 只证明"建得出来 + 读得回来"：块数断言在应用层
        # （`Document.view()` 仍读全局 SECTIONS，渲染迁移是切片 2）。
        assert workspace.status_code == 200
        assert workspace.json()["project"]["title"] == "轻项目"


async def test_create_project_without_template_id_still_builds_13():
    """老客户端只传 title：缺省 standard，行为与今天一致。"""

    app, _ = make_client()
    async with async_client(app) as client:
        project = await client.post("/api/projects", json={"title": "标准项目"})
        workspace = await client.get(
            f"/api/projects/{project.json()['project_id']}/workspace"
        )

    assert project.status_code == 201
    assert len(workspace.json()["document"]["sections"]) == 13


async def test_create_project_with_unknown_template_is_404():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.post(
            "/api/projects", json={"title": "坏项目", "template_id": "nope"}
        )

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_template"


async def test_create_template_with_bad_anchors_is_409():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.post(
            "/api/templates",
            json={
                "title": "坏",
                "sections": [
                    {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"}
                ],
            },
        )

    assert response.status_code == 409


async def test_get_unknown_template_is_404():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/templates/nope")

    assert response.status_code == 404


async def test_schema_templates_carry_slots():
    """`/schema` 的模板投影带槽：前端类型声明了 slots，后端就得真给（契约对拍）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/schema")

    assert response.status_code == 200
    templates = {item["template_id"]: item for item in response.json()["templates"]}
    assert set(templates) >= {"standard", "light"}
    s8 = next(
        item for item in templates["standard"]["sections"] if item["section_id"] == "s8"
    )
    assert [slot["name"] for slot in s8["slots"]] == [
        "performance",
        "availability",
        "security",
        "compatibility",
        "observability",
        "cost",
    ]


async def test_create_template_carries_criteria_declarations():
    """API 建的模板带判据声明，读回来一字不差（声明漏传 = 静默没判据）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post(
            "/api/templates",
            json={
                "title": "带判据",
                "sections": [
                    {
                        "id": "bg",
                        "title": "背景",
                        "purpose": "摆设",
                        "min_entries": 2,
                        "coverage": 3,
                    },
                    {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
                    {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                    {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
                ],
            },
        )

    assert created.status_code == 201
    section = created.json()["sections"][0]
    assert section["min_entries"] == {"count": 2, "kind": None}
    assert section["coverage"] == 3


async def test_create_template_rejects_criteria_on_anchor_section():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.post(
            "/api/templates",
            json={
                "title": "越界",
                "sections": [
                    {
                        "id": "s5",
                        "title": "功能",
                        "purpose": "摆设",
                        "role": "feature",
                        "min_entries": 2,
                    },
                    {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                    {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
                ],
            },
        )

    assert response.status_code == 409


async def test_schema_exposes_the_role_and_kind_enumerations():
    """`steps`/`decision`/`backbone`/`data` 四个 role 没有任何内置模板在用，
    前端从现有模板数据永远拿不到它们 → 模板编辑器的下拉只能由后端给
    （`02` §11.0.70；与讨论角色、人设同一个通道）。"""

    async with async_client(make_client()[0]) as client:
        schema = (await client.get("/api/schema")).json()

    assert len(schema["template_roles"]) == len(set(schema["template_roles"]))
    assert {"feature", "open_questions", "version_history", "steps",
            "decision", "backbone", "data", "plain"} <= set(schema["template_roles"])
    assert len(schema["block_kinds"]) == len(set(schema["block_kinds"]))
    assert {"acceptance", "steps", "decision", "entity", "interface"} <= set(schema["block_kinds"])


async def test_updating_a_custom_template_returns_the_new_definition():
    async with async_client(make_client()[0]) as client:
        created = (
            await client.post(
                "/api/templates", json={"title": "自建", "sections": triple_sections()}
            )
        ).json()

        updated = await client.put(
            f"/api/templates/{created['template_id']}",
            json={
                "title": "改名了",
                "sections": triple_sections() + [
                    {"id": "s1", "title": "背景", "purpose": "摆设", "role": "plain"}
                ],
            },
        )

    assert updated.status_code == 200
    assert updated.json()["title"] == "改名了"
    assert [item["section_id"] for item in updated.json()["sections"]] == [
        "s5", "s11", "s13", "s1",
    ]


async def test_updating_a_builtin_template_is_refused():
    async with async_client(make_client()[0]) as client:
        response = await client.put(
            "/api/templates/standard", json={"title": "偷改", "sections": triple_sections()}
        )

    assert response.status_code == 409
    assert response.json()["code"] == "template_builtin_protected"


async def test_deleting_a_custom_template_takes_it_out_and_then_it_is_a_404():
    async with async_client(make_client()[0]) as client:
        created = (
            await client.post(
                "/api/templates", json={"title": "自建", "sections": triple_sections()}
            )
        ).json()
        template_id = created["template_id"]

        deleted = await client.delete(f"/api/templates/{template_id}")
        again = await client.get(f"/api/templates/{template_id}")

    assert deleted.status_code == 204
    assert again.status_code == 404


async def test_deleting_a_builtin_template_is_refused():
    async with async_client(make_client()[0]) as client:
        response = await client.delete("/api/templates/light")

    assert response.status_code == 409
    assert response.json()["code"] == "template_builtin_protected"


async def test_deleting_a_template_that_is_not_there_is_a_404():
    async with async_client(make_client()[0]) as client:
        response = await client.delete("/api/templates/tpl-nope")

    assert response.status_code == 404


async def test_a_template_with_duplicate_slot_names_is_rejected():
    """重名槽位会让 `template.slot(section_id, name)` 静默返回第一个，寻址错乱——
    界面保证唯一只是体验，后端得兜住（`02` §11.0.70）。"""

    sections = [
        {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature",
         "slots": [
             {"name": "goal", "title": "甲", "kind": "text"},
             {"name": "goal", "title": "乙", "kind": "text"},
         ]},
        {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
        {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
    ]
    async with async_client(make_client()[0]) as client:
        response = await client.post(
            "/api/templates", json={"title": "重名", "sections": sections}
        )

    # 坏定义是 `BadTemplateDefinition`（InvariantViolation）→ 409，与既有锚点校验同一个出口
    assert response.status_code == 409
