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

        kept = await client.post(
            f"/api/discussions/{did}/cards/C1/keep", json={"as": "material"}
        )
        assert kept.status_code == 200, kept.text
        card = next(item for item in kept.json()["cards"] if item["card_id"] == "C1")
        assert card["kept_ref"]
        assert (card["state"], card["kept_as"]) == ("kept", "material")


async def test_card_keep_seed_and_dismiss_over_http() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.challenger"},
        )
        did = started.json()["thread_id"]
        await client.post(f"/api/discussions/{did}/say", json={"text": "我想做导出"})
        await client.post(f"/api/discussions/{did}/cards")

        seeded = await client.post(
            f"/api/discussions/{did}/cards/C1/keep", json={"as": "seed"}
        )
        assert seeded.status_code == 200, seeded.text
        card = seeded.json()["cards"][0]
        assert (card["state"], card["kept_as"], card["kept_ref"]) == ("kept", "seed", None)

        # 重复收下同去向：幂等 200
        again = await client.post(
            f"/api/discussions/{did}/cards/C1/keep", json={"as": "seed"}
        )
        assert again.status_code == 200
        # 换去向：409（invariant:T5）
        other = await client.post(
            f"/api/discussions/{did}/cards/C1/keep", json={"as": "material"}
        )
        assert other.status_code == 409
        assert other.json()["code"] == "invariant:T5"

        dismissed = await client.post(f"/api/discussions/{did}/cards/C1/dismiss")
        assert dismissed.status_code == 409  # 已收下的不能忽略
        assert dismissed.json()["code"] == "invariant:T5"


async def test_dismiss_card_over_http_is_idempotent_and_unknown_cards_are_422() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.challenger"},
        )
        did = started.json()["thread_id"]
        await client.post(f"/api/discussions/{did}/say", json={"text": "我想做导出"})
        await client.post(f"/api/discussions/{did}/cards")

        first = await client.post(f"/api/discussions/{did}/cards/C1/dismiss")
        assert first.status_code == 200, first.text
        card = first.json()["cards"][0]
        assert (card["state"], card["kept_as"], card["kept_ref"]) == (
            "dismissed",
            None,
            None,
        )
        second = await client.post(f"/api/discussions/{did}/cards/C1/dismiss")
        assert second.status_code == 200  # 幂等

        unknown = await client.post(f"/api/discussions/{did}/cards/CX/dismiss")
        assert unknown.status_code == 422

        missing = await client.post("/api/discussions/d-nope/cards/C1/dismiss")
        assert missing.status_code == 404
        assert missing.json()["code"] == "unknown_discussion"

        bad_body = await client.post(
            f"/api/discussions/{did}/cards/C1/keep", json={"as": "gift"}
        )
        assert bad_body.status_code == 422  # 去向只能是 seed/material（pydantic 拦）


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
