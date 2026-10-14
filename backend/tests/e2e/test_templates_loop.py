"""模板全链路（真 SQLite + 真容器 + HTTP；模型零调用——模板流本来就不调模型）。

闭环：seed 内置 → 自建模板 → 列表 → 建项目（light/自建）→ 工作区章节即模板 →
摘要带模板 → 非 standard 开轮 409 → 重启（同库再 build）后自定义与快照都在。
"""

from tests.support.api_client import async_client

TRIPLE = [
    {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
    {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
    {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
]


async def test_template_lifecycle_over_http(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        listed = (await client.get("/api/templates")).json()
        assert [item["template_id"] for item in listed] == ["standard", "light"]

        created = await client.post(
            "/api/templates", json={"title": "自建", "sections": TRIPLE}
        )
        assert created.status_code == 201
        custom_id = created.json()["template_id"]

        project = await client.post(
            "/api/projects", json={"title": "自建项目", "template_id": custom_id}
        )
        assert project.status_code == 201
        project_id = project.json()["project_id"]

        workspace = (
            await client.get(f"/api/projects/{project_id}/workspace")
        ).json()
        assert [s["section_id"] for s in workspace["document"]["sections"]] == [
            "s5",
            "s11",
            "s13",
        ]

        projects = (await client.get("/api/projects")).json()
        mine = next(item for item in projects if item["project_id"] == project_id)
        assert mine["template_title"] == "自建"


async def test_light_project_round_is_rejected_over_http(make_app):
    app, _ = make_app(replies={})
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "轻", "template_id": "light"})
        ).json()["project_id"]

        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "写点东西"}
        )

        assert started.status_code == 409
        assert started.json()["code"] == "template_rounds_unsupported"


async def test_reboot_keeps_customs_and_snapshots(make_app, tmp_path):
    """换进程接着跑：自定义模板与项目快照都在，内置重 seed 不炸。"""

    from tests.e2e.conftest import build_app

    app, _ = build_app(tmp_path, replies={})
    async with async_client(app) as client:
        custom_id = (
            await client.post("/api/templates", json={"title": "自建", "sections": TRIPLE})
        ).json()["template_id"]
        project_id = (
            await client.post(
                "/api/projects", json={"title": "自建项目", "template_id": custom_id}
            )
        ).json()["project_id"]

    app2, _ = build_app(tmp_path, replies={})
    async with async_client(app2) as client:
        ids = [item["template_id"] for item in (await client.get("/api/templates")).json()]
        assert custom_id in ids and "standard" in ids and "light" in ids
        workspace = (
            await client.get(f"/api/projects/{project_id}/workspace")
        ).json()
        assert [s["section_id"] for s in workspace["document"]["sections"]] == [
            "s5",
            "s11",
            "s13",
        ]
