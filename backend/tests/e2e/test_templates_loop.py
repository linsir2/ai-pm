"""模板全链路（真 PG + 真容器 + HTTP；模型零调用——模板流本来就不调模型）。

闭环：seed 内置 → 自建模板 → 列表 → 建项目（light/自建）→ 工作区章节即模板 →
摘要带模板 → 非 standard 开轮 409 → 重启（同库再 build）后自定义与快照都在。
"""

import json

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

    app, _ = build_app(tmp_path, replies={}, first_admin=True)
    async with async_client(app) as client:
        custom_id = (
            await client.post("/api/templates", json={"title": "自建", "sections": TRIPLE})
        ).json()["template_id"]
        project_id = (
            await client.post(
                "/api/projects", json={"title": "自建项目", "template_id": custom_id}
            )
        ).json()["project_id"]

    app2, _ = build_app(tmp_path, replies={}, first_admin=True)
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
            f"/api/rounds/{started['round_id']}/confirm",
            json={"draft_id": draft["draft"]["draft_id"], "accepted": accepted}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["version_no"] == 2

        workspace = (await client.get(f"/api/projects/{project_id}/workspace")).json()
        assert [section["section_id"] for section in workspace["document"]["sections"]] == [
            "s1", "s2", "s4", "s5", "s11", "s13",
        ]
        # 就绪度按 light 的章节算：模板外的章一条都不报
        assert {item["section_id"] for item in workspace["readiness"]} <= light_ids


async def test_updating_a_template_leaves_every_existing_project_alone(make_app):
    """快照不变式（`02` §11.0.70）：项目行里存的是模板**全文 JSON 快照**，不是 id 引用。

    所以改模板对已建项目零影响——这是"改模板不危险"的根据，不是运气。
    """

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        project = (
            await client.post(
                "/api/projects", json={"title": "老项目", "template_id": "light"}
            )
        ).json()
        before = (await client.get(f"/api/projects/{project['project_id']}/workspace")).json()

        template_id = (
            await client.post(
                "/api/templates",
                json={"title": "改我", "sections": [
                    {"id": "s1", "title": "改名后的背景", "purpose": "摆设", "role": "plain"},
                    {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
                    {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                    {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
                ]},
            )
        ).json()["template_id"]
        renamed = await client.put(
            f"/api/templates/{template_id}",
            json={"title": "改完了", "sections": [
                {"id": "s2", "title": "全新的章", "purpose": "摆设", "role": "plain"},
                {"id": "s5", "title": "功能", "purpose": "摆设", "role": "feature"},
                {"id": "s11", "title": "未决", "purpose": "摆设", "role": "open_questions"},
                {"id": "s13", "title": "版本", "purpose": "摆设", "role": "version_history"},
            ]},
        )

        after = (await client.get(f"/api/projects/{project['project_id']}/workspace")).json()

    assert renamed.status_code == 200
    assert before["document"]["sections"] == after["document"]["sections"]
    assert [item["section_id"] for item in after["document"]["sections"]] == [
        "s1", "s2", "s4", "s5", "s11", "s13",
    ]


async def test_deleting_a_template_leaves_every_existing_project_openable(make_app):
    """删模板不删项目（快照模型）：项目照常打开，只是那个 id 不能再建新项目。"""

    app, _ = make_app(replies={})
    async with async_client(app) as client:
        template_id = (
            await client.post(
                "/api/templates", json={"title": "用一下", "sections": TRIPLE}
            )
        ).json()["template_id"]
        project = (
            await client.post(
                "/api/projects", json={"title": "用它建的", "template_id": template_id}
            )
        ).json()

        assert (await client.delete(f"/api/templates/{template_id}")).status_code == 204

        workspace = await client.get(f"/api/projects/{project['project_id']}/workspace")
        listed = await client.get("/api/projects")
        rebuild = await client.post(
            "/api/projects", json={"title": "再建一个", "template_id": template_id}
        )

    assert workspace.status_code == 200
    assert [item["section_id"] for item in workspace.json()["document"]["sections"]] == [
        "s5", "s11", "s13",
    ]
    assert [item["project_id"] for item in listed.json()] == [project["project_id"]]
    assert rebuild.status_code == 404


TWO_CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文（空文档）与本轮输入",
        "claims": [
            {
                "text": "社区食堂现在靠手工登记订餐",
                "raw_span": "社区食堂现在靠手工登记订餐",
                "label": "fact",
                "zone": "body",
                "section_id": "s1",
            },
            {
                "text": "下个月 15 号必须上线",
                "raw_span": "下个月 15 号必须上线",
                "label": "constraint",
                "zone": "body",
                "section_id": "s1",
            },
        ],
        "seats": [
            {"seat_id": "seat.data_scale", "why": "你说到了单量"},
        ],
    },
    ensure_ascii=False,
)

DRAFT_CITING_ONLY_E1_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "entry_id": None,
                "target": {"zone": "body", "section_id": "s2"},
                "text": "把手工登记换成线上订餐",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "手工登记是现状",
            },
        ],
    },
    ensure_ascii=False,
)


async def test_an_uncited_claim_lands_at_its_own_target_in_the_draft(make_app):
    """E6 现场的复刻（`04-未决.md` §N1）：整合者只引用了 E1，E2 没被引用——
    草案里必须多一条 USER 提案（去处是它自己的 s1，默认勾选），而不是静默消失。"""

    from tests.e2e.test_closed_loop import SEAT_REPLY

    app, _ = make_app(
        replies={
            "claims": TWO_CLAIMS_REPLY,
            "integration": [DRAFT_CITING_ONLY_E1_REPLY],
        },
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "食堂"})
        ).json()["project_id"]
        started = await start_round(
            client,
            project_id,
            user_input="社区食堂现在靠手工登记订餐，下个月 15 号必须上线。",
        )
        assert started["waiting_on"] == "opening_card"

        opened = await client.post(
            f"/api/rounds/{started['round_id']}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )
        assert opened.status_code == 200, opened.text
        draft = opened.json()
        assert draft["waiting_on"] == "draft_card", draft.get("waiting_on")

        landed = [
            proposal
            for proposal in draft["draft"]["proposals"]
            if proposal["origin_type"] == "user"
            and proposal["text"] == "下个月 15 号必须上线"
        ]
        assert len(landed) == 1, [
            (item["origin_type"], item["text"], item["target_label"])
            for item in draft["draft"]["proposals"]
        ]
        assert landed[0]["target_label"] == "s1"

        accepted = [
            proposal["proposal_id"]
            for proposal in draft["draft"]["proposals"]
            if proposal["origin_type"] != "proposed"
        ]
        assert landed[0]["proposal_id"] in accepted  # USER 默认勾选：不勾等于静默丢
        confirmed = await client.post(
            f"/api/rounds/{started['round_id']}/confirm",
            json={"draft_id": draft["draft"]["draft_id"], "accepted": accepted}
        )
        assert confirmed.status_code == 200, confirmed.text

        workspace = (await client.get(f"/api/projects/{project_id}/workspace")).json()
        s1 = next(
            section
            for section in workspace["document"]["sections"]
            if section["section_id"] == "s1"
        )
        assert any(
            "下个月 15 号必须上线" in entry["text"]
            for block in s1["blocks"]
            for entry in block["entries"]
        ), s1
