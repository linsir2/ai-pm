"""模板全链路（真 SQLite + 真容器 + HTTP；模型零调用——模板流本来就不调模型）。

闭环：seed 内置 → 自建模板 → 列表 → 建项目（light/自建）→ 工作区章节即模板 →
摘要带模板 → 非 standard 开轮 409 → 重启（同库再 build）后自定义与快照都在。
"""

from tests.e2e.test_closed_loop import (
    CLAIMS_REPLY,
    DRAFT_REPLY,
    INPUT,
    OPPENING,
    QUESTION_REPLY,
    SEAT_REPLY,
    start_round,
)
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


async def test_light_project_runs_a_full_loop_over_http(make_app):
    """light 跑完整轮：输入 → 开场卡 → 席位 → 问卷 → 草案 → 确认 → 文档多一版。

    这是"引擎消费者全部迁完"的验收（`02` §11.0.69 删门前置条件）：
    同一个闭环用例（`test_closed_loop.py` 的那套夹具）在 light 上原样跑一遍——
    **它的报文里有一条指向 s10**（模板里没有的章），用来证明去处校验是"丢那一条并留痕"
    而不是炸整轮。
    """

    app, _ = make_app(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": [QUESTION_REPLY, DRAFT_REPLY],
        },
        seat_replies={"seat.data_scale": SEAT_REPLY, "seat.permissions": SEAT_REPLY},
    )
    light_ids = {"s1", "s2", "s4", "s5", "s11", "s13"}

    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "轻", "template_id": "light"})
        ).json()["project_id"]

        started = await start_round(client, project_id, user_input=INPUT)
        assert started["waiting_on"] == "opening_card"

        opened = await client.post(f"/api/rounds/{started['round_id']}/opening", json=OPPENING)
        assert opened.status_code == 200, opened.text
        opened_body = opened.json()

        # 出不出问卷由内容决定（light 的报文与 standard 的夹具不完全一样）：
        # 出了就答掉，没出就直接是草案卡——两条路都必须走到同一块屏幕。
        if opened_body["waiting_on"] == "questionnaire":
            answered = await client.post(
                f"/api/rounds/{started['round_id']}/answers",
                json={
                    "form_id": opened_body["form_id"],
                    "answers": [
                        {
                            "question_id": question["question_id"],
                            "selected": ["b"],
                            "text": "",
                        }
                        for question in opened_body["questions"][:1]
                    ],
                    "skipped": [
                        question["question_id"] for question in opened_body["questions"][1:]
                    ],
                },
            )
            assert answered.status_code == 200, answered.text
            draft = answered.json()
        else:
            draft = opened_body
        assert draft["waiting_on"] == "draft_card"

        # 每条提案的去处都在 light 的六章里（或偏好区，它本来就独立于章节）
        targets = {
            proposal["target_label"].split("/")[0].split(".")[0]
            for proposal in draft["draft"]["proposals"]
        }
        assert targets <= light_ids | {"preference"}, targets

        accepted = [
            proposal["proposal_id"]
            for proposal in draft["draft"]["proposals"]
            if proposal["origin_type"] != "proposed"
        ]
        confirmed = await client.post(
            f"/api/rounds/{started['round_id']}/confirm", json={"accepted": accepted}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["version_no"] == 2

        workspace = (await client.get(f"/api/projects/{project_id}/workspace")).json()
        assert [section["section_id"] for section in workspace["document"]["sections"]] == [
            "s1", "s2", "s4", "s5", "s11", "s13",
        ]
        # 就绪度按 light 的章节算：模板外的章一条都不报
        assert {item["section_id"] for item in workspace["readiness"]} <= light_ids
