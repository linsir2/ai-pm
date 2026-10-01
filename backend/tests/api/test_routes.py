import json

from pmstudio.api.app import ApiServices, create_app
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.shared.ids import EntryId, EvidenceId, ProjectId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.api_client import async_client
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了空文档",
        "claims": [
            {
                "text": "要做一个订单导出功能",
                "raw_span": "想做个订单导出的功能",
                "label": "need",
                "zone": "body",
                "section_id": "s5",
                "feature_slug": "order-export",
            }
        ],
        "seats": [
            {"seat_id": "seat.data_scale", "why": "导出要评估数据量与耗时"},
        ],
    },
    ensure_ascii=False,
)

#: 同一份报文 + 一条检索词提名（工具层 C-2b：复述卡上的联网那一栏）
CLAIMS_REPLY_WITH_QUERY = json.dumps(
    {
        **json.loads(CLAIMS_REPLY),
        "web_queries": [{"query": "RBAC 权限模型", "why": "要查行业通行做法"}],
    },
    ensure_ascii=False,
)

QUESTION_REPLY = json.dumps(
    {
        "questions": [
            {
                "kind": "gap",
                "title": "单次导出的最大行数是多少？",
                "why": "量级决定同步还是异步",
                "consequence_if_skipped": "按同步实现",
                "target": {"zone": "body", "section_id": "s8"},
                "type": "single",
                "options": [
                    {"option_id": "a", "text": "少于 1 万"},
                    {"option_id": "b", "text": "1 万–10 万"},
                ],
                "evidence_refs": ["E1"],
                "asked_by": ["seat.data_scale"],
            }
        ],
        "list_items": [],
        "proposals": [],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "异常与边界",
                "claim": "无导出权限的用户不显示导出入口",
                "because": "订单含客户信息",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)


def _services(harness: Harness) -> ApiServices:
    return harness.api_services(model_name="openai/gpt-4o-mini")


def make_client():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": QUESTION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )
    return create_app(_services(harness)), harness


async def test_health_reports_model():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "openai/gpt-4o-mini"}


async def test_upload_endpoint_accepts_multipart_and_lists_summaries():
    """上传材料走**真 multipart**：解析、落库、列表都走真实现（不假实现）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/attachments",
            files={"file": ("需求.md", "# 目标\n导出要有权限".encode(), "text/markdown")},
        )
        listed = await client.get(f"/api/projects/{project_id}/attachments")

    assert response.status_code == 201
    body = response.json()
    assert body["parser_id"] == "local-text"
    assert body["chunk_count"] == 1
    assert body["stale"] is False
    assert "chunks" not in body                      # 摘要投影：不回块正文
    assert [item["title"] for item in listed.json()] == ["需求.md"]


async def test_upload_of_an_unsupported_type_is_422_with_a_code():
    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/attachments",
            files={"file": ("scan.jpg", b"\x89PNG", "image/jpeg")},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "unsupported_material"


async def test_search_endpoint_returns_hits_and_an_empty_query_returns_nothing():
    """F6：搜得到东西；**空查询回空**（绝不"不填就是全量"）。"""

    app, harness = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        await client.post(f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"})
        answer = await client.get(f"/api/projects/{project_id}/search", params={"q": "订单导出"})
        blank = await client.get(f"/api/projects/{project_id}/search")

    assert answer.status_code == 200
    body = answer.json()
    assert body["query"] == "订单导出"
    assert any(hit["kind"] == "evidence" for hit in body["hits"])
    assert blank.json()["hits"] == []


async def test_search_endpoint_includes_material_hits():
    """材料也是搜得到的东西（`P18` C-2c-1）：命中带块 id（点得动的落点）与锚点。"""

    from pmstudio.application.dto import MaterialHit

    harness = Harness(replies={}, seat_replies={})
    harness.material_retriever.returns = (
        MaterialHit(
            material_id="m1",
            chunk_id="m1#c03",
            score=3.0,
            excerpt="…结论：导出要走异步队列…",
            anchor="第 3 页 §2.1",
            source_ref="attachment:诊断报告.pdf",
            title="诊断报告.pdf",
        ),
    )
    app = create_app(_services(harness))
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        found = await client.get(
            f"/api/projects/{project_id}/search", params={"q": "异步队列"}
        )

    assert found.status_code == 200
    hits = found.json()["hits"]
    assert [hit["kind"] for hit in hits] == ["material"]
    assert hits[0]["ref"] == "m1#c03"
    assert hits[0]["where"] == "材料 · 第 3 页 §2.1"


async def test_list_rounds_returns_the_rounds_of_this_project():
    """闭环区顶上那个"这个项目走过哪几轮"由它喂（F4）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        await client.post(f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"})
        response = await client.get(f"/api/projects/{project_id}/rounds")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["step"] == "claims_ready"
    assert body[0]["claims_count"] == 1
    assert body[0]["user_input"].startswith("想做个订单导出")


async def test_manual_add_with_a_target_writes_the_slot_that_did_not_exist():
    """手改也能给完整 target（`{section_id, field}`）：块还不存在就由 domain 建（F8-①）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/edits",
            json={
                "op": "add",
                "target": {"zone": "body", "section_id": "s8", "field": "performance"},
                "text": "导出 5 万条 60 秒内完成",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["written_block_ids"] == ["s8.performance"]
    assert body["version_no"] == 2


async def test_schema_endpoint_returns_the_13_sections_and_the_limits():
    """界面上那些"前端自己抄了一份"的常量与说明，回后端这一份（`04-未决.md` F2）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/schema")

    assert response.status_code == 200
    body = response.json()
    assert len(body["sections"]) == 13
    assert body["sections"][0]["purpose"]
    assert len(body["feature_fields"]) == 12
    assert body["limits"] == {
        "max_seat_rounds": 3,
        "max_questionnaires": 2,
        "max_open_items_per_batch": 3,
    }


async def test_create_project_then_workspace():
    app, _ = make_client()
    async with async_client(app) as client:
        created = await client.post("/api/projects", json={"title": "订单中台"})
        project_id = created.json()["project_id"]
        workspace = await client.get(f"/api/projects/{project_id}/workspace")
        projects = await client.get("/api/projects")

    assert created.status_code == 201
    assert created.json()["title"] == "订单中台"

    assert workspace.status_code == 200
    body = workspace.json()
    assert body["project"]["title"] == "订单中台"
    assert [section["section_id"] for section in body["document"]["sections"]] == [
        f"s{index}" for index in range(1, 14)
    ]
    assert body["round"] is None
    assert projects.json()[0]["project_id"] == project_id


async def test_full_loop_over_http():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        started = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
        )
        round_id = started.json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={"seats": ["seat.data_scale"], "claim_edits": []},
        )

    assert started.status_code == 200
    assert started.json()["waiting_on"] == "opening_card"
    assert opened.status_code == 200
    assert opened.json()["waiting_on"] == "questionnaire"
    assert opened.json()["form_id"] == "f1"


async def test_opening_records_the_queries_the_user_checked():
    """复述卡勾一条检索词 → 后端记下来，并且读回来的投影里带着"你勾了它"。"""

    harness = Harness(
        replies={"claims": CLAIMS_REPLY_WITH_QUERY, "integration": QUESTION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
    )
    app = create_app(_services(harness))
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={
                "seats": ["seat.data_scale"],
                "claim_edits": [],
                "web_queries": ["RBAC 权限模型"],
            },
        )
        read_back = await client.get(f"/api/rounds/{round_id}")

    assert opened.status_code == 200
    assert read_back.status_code == 200
    assert [
        (item["query"], item["why"], item["chosen"])
        for item in read_back.json()["web_queries"]
    ] == [("RBAC 权限模型", "要查行业通行做法", True)]
    assert read_back.json()["web_search_available"] is True


async def test_opening_with_an_unknown_seat_is_a_422_not_a_500():
    """席位必须在注册表里：给错 id 是"请求不对"（422），不该拖到执行时才炸成 500。"""

    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        response = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={"seats": ["seat.data_scale", "seat.nope"], "claim_edits": []},
        )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "shape_error"
    assert body["details"]["seat_ids"] == ["seat.nope"]


async def test_opening_accepts_claim_edits():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        opened = await client.post(
            f"/api/rounds/{round_id}/opening",
            json={
                "seats": ["seat.data_scale"],
                "claim_edits": [
                    {
                        "claim_id": "c1",
                        "label": "goal",
                        "target": {"zone": "body", "section_id": "s2"},
                    }
                ],
            },
        )

    assert opened.status_code == 200
    assert opened.json()["claims"][0]["label"] == "goal"
    assert opened.json()["claims"][0]["target_label"] == "s2"


async def test_confirm_response_shape():
    """没走到草案卡时确认要被状态机挡住（409），这是"AI 不能自己写"的直接体现。"""

    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        response = await client.post(
            f"/api/rounds/{round_id}/confirm", json={"accepted": ["p01"]}
        )

    assert response.status_code == 409
    assert response.json()["code"].startswith("invariant:")


async def test_unknown_round_is_404():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/rounds/r404")
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_round"


async def test_manual_edit_over_http_bumps_the_version_and_returns_the_document():
    """手改一条：返回新版本号 + 改完的文档，界面一次往返就能刷新。"""

    app, harness = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]

        # 先给文档铺一条可改的内容（走完整闭环太慢，这不是被测对象）
        document = harness.documents.get(ProjectId(project_id))
        document.apply(
            [
                WriteInstruction(
                    instruction_id="seed",
                    entry_id=EntryId("en-1"),
                    op=WriteOp.ADD,
                    target=Target.body("s2"),
                    text="把想法整理成能交付的文档",
                    refs=(EvidenceId("E1"),),
                    kind=BlockKind.TEXT,
                )
            ],
            now=harness.clock.now(),
            summary="铺一条",
        )
        harness.documents.seed(document)
        before = harness.documents.get(ProjectId(project_id))

        response = await client.post(
            f"/api/projects/{project_id}/edits",
            json={"op": "replace", "entry_id": "en-1", "text": "把想法整理成一份能交付的文档"},
        )
        bad = await client.post(
            f"/api/projects/{project_id}/edits",
            json={"op": "replace", "entry_id": "nope", "text": "x"},
        )
        missing = await client.post(
            f"/api/projects/{project_id}/edits", json={"op": "add", "text": "x"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["version_no"] == before.current_version.version_no + 1
    assert body["written_block_ids"]

    blocks = [block for section in body["document"]["sections"] for block in section["blocks"]]
    entries = [entry for block in blocks for entry in block["entries"]]
    assert [entry["text"] for entry in entries] == ["把想法整理成一份能交付的文档"]
    assert len(entries[0]["refs"]) == 2  # 原来的出处 + 手改那一条
    # 有内容的那一块被标记成"人改过"（s8 的空槽是骨架，不在判定里）
    assert [block["manually_edited"] for block in blocks if block["entries"]] == [True]

    assert bad.status_code == 422
    assert missing.status_code == 422


async def test_bad_body_is_422():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.post("/api/projects", json={"title": ""})
    assert response.status_code == 422


async def test_model_failure_is_502():
    app, harness = make_client()
    harness.gateway._replies.clear()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
        )

    assert response.status_code == 502
    assert response.json()["code"] == "model_call_failed"


async def test_readiness_endpoint_reports_counts_and_items():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.get(f"/api/projects/{project_id}/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["blockers"] >= 1
    assert body["warns"] >= 0
    assert {"section_id", "code", "title", "detail", "severity"} == set(body["items"][0])
    assert body["blockers"] == sum(1 for item in body["items"] if item["severity"] == "blocker")


async def test_export_markdown_returns_a_downloadable_file():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.get(f"/api/projects/{project_id}/export/markdown")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert 'filename="deliverable-v1.md"' in disposition
    assert "filename*=UTF-8''" in disposition  # 中文项目名要用 RFC 5987 双写
    assert response.text.startswith("# 产品文档 · 订单中台（v1）")
    assert "## 13 版本历史" in response.text
    assert "附录 A · 出处" in response.text


async def test_export_markdown_unknown_project_is_404():
    app, _ = make_client()
    async with async_client(app) as client:
        response = await client.get("/api/projects/p-nope/export/markdown")

    assert response.status_code == 404


def test_round_busy_maps_to_409():
    """同一个 round 上已经有一个动作在跑：是"冲突"（409），不是服务器炸了（500）。"""

    from pmstudio.api.errors import status_for
    from pmstudio.application.errors import RoundBusy

    assert status_for(RoundBusy(round_id="r1")) == 409


async def test_round_view_exposes_discussion_stream_data():
    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        round_id = (
            await client.post(
                f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出的功能"}
            )
        ).json()["round_id"]
        body = (await client.get(f"/api/rounds/{round_id}")).json()

    assert body["claims"][0]["eid"] == "E1"
    assert body["claims"][0]["label"] == "need"
    assert body["seat_packets"][0]["status"] == "pending"
    assert body["nominated_seats"][0]["seat_id"] == "seat.data_scale"
