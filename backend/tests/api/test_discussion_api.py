"""讨论端点（M9-T5）：开聊 / 列表 / 追问 / 总结 / 收卡 / 关闭的状态码与形状。"""

import json

from pmstudio.api.app import create_app
from tests.support.api_client import async_client
from tests.support.builders import PROJECT
from tests.support.harness import Harness

SAY_TEXT = "先把导出范围收窄到本店本月，再谈全量。"


def _say_reply() -> str:
    return json.dumps({"text": SAY_TEXT, "based_on": []}, ensure_ascii=False)


def _cards_reply_1() -> str:
    return json.dumps(
        {
            "cards": [
                {
                    "statement": "给导出加一个按客户名找单的快捷入口",
                    "grounding": "invented",
                    "based_on": [],
                }
            ]
        },
        ensure_ascii=False,
    )


def _app(*, replies: dict | None = None):  # type: ignore[no-untyped-def]
    harness = Harness(
        replies=replies or {"discussion": _say_reply(), "discussion_cards": _cards_reply_1()},
        seat_replies={},
    )
    harness.seed_project()
    return create_app(harness.api_services(model_name="m")), harness


async def test_start_say_cards_keep_flow_over_http() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.challenger"},
        )
        assert started.status_code == 200, started.text
        body = started.json()
        assert body["status"] == "open"
        assert body["role_id"] == "dlg.challenger"
        assert body["turns"] == []
        assert body["cards"] == []

        did = body["thread_id"]
        listed = await client.get(f"/api/projects/{PROJECT}/discussions")
        assert listed.status_code == 200
        assert [item["thread_id"] for item in listed.json()] == [did]

        said = await client.post(f"/api/discussions/{did}/say", json={"text": "我想做导出"})
        assert said.status_code == 200, said.text
        turns = said.json()["turns"]
        assert len(turns) == 2
        assert turns[0]["role"] == "user" and turns[0]["text"] == "我想做导出"
        assert turns[1]["role"] == "assistant" and turns[1]["text"] == SAY_TEXT

        assembled = await client.post(f"/api/discussions/{did}/cards")
        assert assembled.status_code == 200, assembled.text
        cards = assembled.json()["cards"]
        assert [card["card_id"] for card in cards] == ["C1"]

        kept = await client.post(f"/api/discussions/{did}/cards/C1/keep")
        assert kept.status_code == 200, kept.text
        card = next(item for item in kept.json()["cards"] if item["card_id"] == "C1")
        assert card["kept_ref"]


async def test_say_to_closed_thread_is_409() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.challenger"},
        )
        did = started.json()["thread_id"]
        closed = await client.post(f"/api/discussions/{did}/close")
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"

        said = await client.post(f"/api/discussions/{did}/say", json={"text": "还能聊吗"})
    assert said.status_code == 409
    assert said.json()["code"].startswith("invariant:")


async def test_unknown_thread_and_role_are_404() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        said = await client.post("/api/discussions/d-nope/say", json={"text": "你好"})
        assert said.status_code == 404
        assert said.json()["code"] == "unknown_discussion"

        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.nope"},
        )
        assert started.status_code == 404
        assert started.json()["code"] == "unknown_role"


async def test_empty_say_text_is_422() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.challenger"},
        )
        did = started.json()["thread_id"]
        response = await client.post(f"/api/discussions/{did}/say", json={"text": ""})
    assert response.status_code == 422
