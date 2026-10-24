import json

from pmstudio.api.app import ApiServices, create_app
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.shared.ids import EntryId, EvidenceId, ProjectId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.application.test_materials import make_material
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


def make_client(*, fetcher=None):  # type: ignore[no-untyped-def]
    harness = Harness(
        replies={"claims": CLAIMS_REPLY, "integration": QUESTION_REPLY},
        seat_replies={"seat.data_scale": SEAT_REPLY},
        fetcher=fetcher,
    )
    return create_app(_services(harness)), harness


#: 抓网页那两个用例里的页面字节（真 `HttpxWebFetcher` + 真 HTTP 语义，不联网）
FETCHED_PAGE = (
    "<html><head><title>导出权限（竞品）</title></head><body>"
    "<p>竞品把导出放在管理员菜单里。</p></body></html>"
).encode()


def real_fetcher(*, status: int = 200, content_type: str = "text/html", content: bytes = None):  # type: ignore[assignment]
    """**真实现** + `MockTransport`：走真护栏、真 bs4、真切块，只是不出网。"""

    import ipaddress

    import httpx

    from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={"content-type": content_type},
            content=FETCHED_PAGE if content is None else content,
        )

    def resolve(host: str) -> tuple[str, ...]:
        """字面 IP 照实解析（内网那几个就该被判出来）；域名给一个固定公网 IP（不出网）。"""

        if host == "localhost":
            return ("127.0.0.1",)
        try:
            return (str(ipaddress.ip_address(host)),)
        except ValueError:
            return ("93.184.216.34",)

    return HttpxWebFetcher(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        resolve=resolve,
    )


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


async def test_fetch_endpoint_pulls_a_page_and_lists_it():
    """按网址抓材料（`P27`）：走**真抓取器**（MockTransport 不出网）、真解析、真落库。"""

    app, _ = make_client(fetcher=real_fetcher())
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/attachments/fetch",
            json={"url": "https://example.com/spec"},
        )
        listed = await client.get(f"/api/projects/{project_id}/attachments")

    assert response.status_code == 201
    body = response.json()
    assert body["origin"] == "web_fetch"
    assert body["source_ref"] == "https://example.com/spec"
    assert body["title"] == "导出权限（竞品）"
    assert body["chunk_count"] == 1
    assert "chunks" not in body                  # 摘要投影：不回块正文
    assert [item["title"] for item in listed.json()] == ["导出权限（竞品）"]


async def test_fetch_endpoint_refuses_a_private_address_with_422():
    """内网地址：**422 + 一句人话**，而且一次请求都不发（护栏在用例之前）。"""

    app, _ = make_client(fetcher=real_fetcher())
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/attachments/fetch",
            json={"url": "http://127.0.0.1:8000/secret"},
        )

    assert response.status_code == 422
    assert response.json()["code"] == "fetch_refused"
    assert "内网" in response.json()["message"]


async def test_fetch_endpoint_reports_a_site_failure_as_502():
    """站点那边出问题（404 / 超时）是 **502**：与"你给的地址不行"分开。"""

    app, _ = make_client(fetcher=real_fetcher(status=404))
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/attachments/fetch",
            json={"url": "https://example.com/gone"},
        )

    assert response.status_code == 502
    assert response.json()["code"] == "tool_call_failed"


async def test_fetch_endpoint_rejects_an_empty_url():
    app, _ = make_client(fetcher=real_fetcher())
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        response = await client.post(
            f"/api/projects/{project_id}/attachments/fetch", json={"url": ""}
        )

    assert response.status_code == 422        # pydantic 的 min_length 就挡了


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


async def test_reparse_attachment_over_http():
    """重解析：真上传一份 → 让后端拿原始字节重读一遍（同一份文本，验的是这条路通）。"""

    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        saved = (
            await client.post(
                f"/api/projects/{project_id}/attachments",
                files={"file": ("需求.md", "# 目标\n导出".encode(), "text/markdown")},
            )
        ).json()
        reparsed = await client.post(
            f"/api/projects/{project_id}/attachments/{saved['material_id']}/reparse"
        )
        missing = await client.post(
            f"/api/projects/{project_id}/attachments/m-不存在/reparse"
        )

    assert reparsed.status_code == 200
    assert [item["material_id"] for item in reparsed.json()] == [saved["material_id"]]
    assert reparsed.json()[0]["stale"] is False
    assert missing.status_code == 404
    assert missing.json()["code"] == "unknown_material"


async def test_delete_attachment_over_http():
    """删一份没被用过的材料 → 200 + 刷新后的列表；再删一次 → 404。"""

    app, _ = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        saved = (
            await client.post(
                f"/api/projects/{project_id}/attachments",
                files={"file": ("需求.md", "# 目标\n导出".encode(), "text/markdown")},
            )
        ).json()
        deleted = await client.delete(
            f"/api/projects/{project_id}/attachments/{saved['material_id']}"
        )
        again = await client.delete(
            f"/api/projects/{project_id}/attachments/{saved['material_id']}"
        )

    assert deleted.status_code == 200
    assert deleted.json() == []
    assert again.status_code == 404
    assert again.json()["code"] == "unknown_material"


async def test_deleting_a_used_material_is_a_409_with_the_numbers():
    """被引用过的材料：409 + 消息里列出编号（编号发出即冻结，`P19`）。"""

    app, harness = make_client()
    async with async_client(app) as client:
        project_id = (
            await client.post("/api/projects", json={"title": "订单中台"})
        ).json()["project_id"]
        await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"}
        )
        saved = (
            await client.post(
                f"/api/projects/{project_id}/attachments",
                files={"file": ("需求.md", "# 目标\n导出".encode(), "text/markdown")},
            )
        ).json()

    from pmstudio.domain.round.round import MaterialRef, Round
    from pmstudio.domain.round.step import Step, WaitingOn
    from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId

    round_ = Round(
        round_id=RoundId("r-used"),
        project_id=ProjectId(project_id),
        user_input="按这份写",
        created_at=harness.clock.now(),
        step=Step.DONE,
        waiting_on=WaitingOn.NONE,
    )
    round_.add_materials(
        (
            MaterialRef(
                material_id=saved["material_id"],
                chunk_id=f"{saved['material_id']}#c00",
                eid=EvidenceId("E4"),
            ),
        )
    )
    harness.rounds.save(round_)

    async with async_client(app) as client:
        refused = await client.delete(
            f"/api/projects/{project_id}/attachments/{saved['material_id']}"
        )

    assert refused.status_code == 409
    assert refused.json()["code"] == "material_frozen"
    assert "E4" in refused.json()["message"]


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
        "max_sim_review_personas": 3,
        "max_sim_review_features": 2,
        "max_discussion_turns": 30,
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


async def test_attach_material_endpoint_pins_and_records_without_an_eid():
    """P1.3：搜索框「带进本轮」的 HTTP 形状与幂等语义。"""

    harness = Harness(replies={"claims": CLAIMS_REPLY})
    project_id = harness.seed_project()
    harness.materials.seed(make_material("权限：管理员才能导出", "审计：导出要留痕"))
    app = create_app(_services(harness))
    async with async_client(app) as client:
        created = await client.post(
            f"/api/projects/{project_id}/rounds", json={"input": "想做个订单导出"}
        )
        round_id = created.json()["round_id"]
        response = await client.post(
            f"/api/rounds/{round_id}/materials",
            json={"material_id": "m1", "chunk_ids": ["m1#c01"]},
        )
        bad = await client.post(
            f"/api/rounds/{round_id}/materials",
            json={"material_id": "m1", "chunk_ids": ["m1#c99"]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pinned_materials"] == ["m1"]
    assert body["manual_chunks"] == [{"material_id": "m1", "chunk_id": "m1#c01"}]
    assert body["materials"] == [], "还没投影，不许提前发 E 号"
    assert bad.status_code == 422
