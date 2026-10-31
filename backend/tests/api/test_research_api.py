"""研究端点（P3b-1）：`POST /projects/{id}/research` 的状态码与形状。

与 M8 的 `/explorations` 分开：研究不要求文档非空，失败也是 200（status=failed）。
"""

import json

from pmstudio.api.app import create_app
from pmstudio.application.ports import FetchedPage
from tests.support.api_client import async_client
from tests.support.builders import PROJECT
from tests.support.doubles import (
    LoopScriptedGateway,
    RecordingWebFetch,
    RecordingWebSearch,
    ToolStep,
)
from tests.support.harness import Harness


def _page():
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    text = "同类产品默认 60 秒上限。"
    return ParsedMaterial(
        origin="upload",
        title="导出上限白皮书",
        source_ref="https://example.com/a",
        media_type="text/html",
        bytes_len=len(text.encode()),
        chunks=(
            MaterialChunk(
                chunk_id="https://example.com/a#c00",
                ordinal=0,
                text=text,
                heading_path=(),
                anchor="正文",
            ),
        ),
        parser_id="fake",
        parser_version="1",
    )


def _app(*, synthesis: str | None = None, returns=None):  # type: ignore[no-untyped-def]
    gateway = LoopScriptedGateway(
        loop=(
            ToolStep(calls=(("web_search", {"query": "导出上限"}),)),
            ToolStep(text="够了"),
        ),
        structured={
            # 这一条用例还要开一轮（挂来源卡要有个轮次可挂）：分析者照最小脚本回
            "claims": json.dumps(
                {
                    "basis": "读了本轮输入",
                    "claims": [
                        {
                            "text": "想做个批量导出",
                            "raw_span": "想做个导出",
                            "label": "need",
                            "zone": "body",
                            "section_id": "s5",
                        }
                    ],
                    "seats": [{"seat_id": "seat.data_scale", "why": "与量级有关"}],
                },
                ensure_ascii=False,
            ),
            "research": synthesis
            or json.dumps(
                {
                    "answer": "同类产品多把批量导出上限设在 60 秒；超大文件需要分片。",
                    "answer_refs": ["S1"],
                    "findings": [
                        {"text": "主流产品 60 秒上限", "refs": ["S1"]}
                    ],
                    "open_questions": ["要不要断点续传？"],
                },
                ensure_ascii=False,
            )
        },
    )
    harness = Harness(
        replies={},
        seat_replies={},
        gateway=gateway,
        web_search=RecordingWebSearch(
            returns=returns if returns is not None else (_page(),)
        ),
        fetcher=RecordingWebFetch(
            returns=(
                FetchedPage(
                    content=(
                        "<html><body><p>同类产品默认 60 秒上限。</p></body></html>"
                    ).encode(),
                    media_type="text/html",
                    final_url="https://example.com/a",
                    title="导出上限白皮书",
                ),
            )
        ),
    )
    harness.seed_project()
    return create_app(harness.api_services(model_name="m")), harness


async def test_research_returns_the_final_run_with_sources_and_findings() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        response = await client.post(
            f"/api/projects/{PROJECT}/research", json={"topic": "批量导出的性能上限"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["kind"] == "web_research"
        assert body["status"] == "done"
        assert body["scope_kind"] == "topic"
        assert body["candidates"] == []
        assert body["research"]["sources"][0]["url"] == "https://example.com/a"
        assert body["research"]["sources"][0]["eid"] == "E1"
        assert body["research"]["answer"].startswith("同类产品多把批量导出上限")
        assert body["research"]["answer_refs"] == ["E1"]
        assert body["research"]["findings"][0]["refs"] == ["E1"]
        assert body["research"]["open_questions"] == ["要不要断点续传？"]


async def test_empty_topic_is_422_and_too_long_topic_is_422() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        empty = await client.post(f"/api/projects/{PROJECT}/research", json={"topic": "   "})
        assert empty.status_code == 422, empty.text

        long = await client.post(
            f"/api/projects/{PROJECT}/research", json={"topic": "字" * 501}
        )
        assert long.status_code == 422, long.text


async def test_unknown_project_is_404() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        response = await client.post(
            "/api/projects/p-nope/research", json={"topic": "随便"}
        )
        assert response.status_code == 404, response.text


async def test_zero_sources_is_200_with_failed_status() -> None:
    app, _ = _app(returns=tuple())
    async with async_client(app) as client:
        response = await client.post(
            f"/api/projects/{PROJECT}/research", json={"topic": "查不到的主题"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "failed"
        assert "没有取到任何来源" in body["failure_reason"]
        assert body["research"] is None


async def test_research_rows_show_up_in_the_exploration_list_with_their_kind() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        await client.post(f"/api/projects/{PROJECT}/research", json={"topic": "导出上限"})
        listed = await client.get(f"/api/projects/{PROJECT}/explorations")

        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert [row["kind"] for row in rows] == ["web_research"]
        assert rows[0]["research"]["sources"][0]["title"] == "导出上限白皮书"


async def test_attach_research_sources_endpoint_and_its_guards() -> None:
    app, harness = _app()
    async with async_client(app) as client:
        research = await client.post(
            f"/api/projects/{PROJECT}/research", json={"topic": "导出上限"}
        )
        xid = research.json()["exploration_id"]
        eid = research.json()["research"]["sources"][0]["eid"]

        started = await client.post(
            f"/api/projects/{PROJECT}/rounds", json={"input": "想做个导出"}
        )
        assert started.status_code == 200, started.text
        round_id = started.json()["round_id"]

        attached = await client.post(
            f"/api/rounds/{round_id}/research-sources",
            json={"exploration_id": xid, "eids": [eid]},
        )
        assert attached.status_code == 200, attached.text
        assert attached.json()["research_sources"][0]["eid"] == eid

        again = await client.post(
            f"/api/rounds/{round_id}/research-sources",
            json={"exploration_id": xid, "eids": [eid]},
        )
        assert again.status_code == 200
        assert len(again.json()["research_sources"]) == 1

        bad = await client.post(
            f"/api/rounds/{round_id}/research-sources",
            json={"exploration_id": xid, "eids": ["E999"]},
        )
        assert bad.status_code == 422, bad.text

        missing = await client.post(
            f"/api/rounds/{round_id}/research-sources",
            json={"exploration_id": "x-nope", "eids": [eid]},
        )
        assert missing.status_code == 404, missing.text
