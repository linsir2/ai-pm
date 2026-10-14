"""讨论端点（M9-T5）：开聊 / 列表 / 追问 / 总结 / 收卡 / 关闭的状态码与形状。"""

import json

from pmstudio.api.app import create_app
from tests.support.api_client import async_client
from tests.support.builders import PROJECT, document_with_feature
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
            json={"topic": "导出优化", "role_ids": ["dlg.challenger"]},
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
            json={"topic": "导出优化", "role_ids": ["dlg.challenger"]},
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
            json={"topic": "导出优化", "role_ids": ["dlg.challenger"]},
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


async def test_start_discussion_with_finding_exploration_over_http() -> None:
    """「先跑一轮调研」（M13）穿过 HTTP：先起一趟探索，再带它的 id 开聊 → findings 快照。"""

    inquiry = json.dumps(
        {
            "basis": "读了文档",
            "hypotheses": [
                {
                    "local_id": "H1",
                    "statement": "运营按客户名找单",
                    "grounding": "invented",
                    "based_on": [],
                    "falsification": "访谈没人这么找",
                    "evidence_needed": "两个访谈",
                }
            ],
            "blind_spots": [
                {"local_id": "B1", "statement": "归属未知", "next_step": "问数据组"}
            ],
        },
        ensure_ascii=False,
    )
    harness = Harness(
        replies={"inquiry": inquiry, "discussion": _say_reply()}, seat_replies={}
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    app = create_app(harness.api_services(model_name="m"))
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/explorations",
            json={"kind": "inquiry", "scope": {"kind": "document"}},
        )
        assert started.status_code == 200, started.text
        xid = started.json()["exploration_id"]

        opened = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={
                "topic": "带底稿聊导出",
                "role_ids": ["dlg.challenger"],
                "finding_exploration_id": xid,
            },
        )
        assert opened.status_code == 200, opened.text
        assert opened.json()["findings_count"] == 2

        missing = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={
                "topic": "坏底稿",
                "role_ids": ["dlg.challenger"],
                "finding_exploration_id": "x-nope",
            },
        )
        assert missing.status_code == 404
        assert missing.json()["code"] == "unknown_exploration"


async def test_say_to_closed_thread_is_409() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_ids": ["dlg.challenger"]},
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
            json={"topic": "导出优化", "role_ids": ["dlg.nope"]},
        )
        assert started.status_code == 404
        assert started.json()["code"] == "unknown_role"


async def test_empty_say_text_is_422() -> None:
    app, _ = _app()
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_ids": ["dlg.challenger"]},
        )
        did = started.json()["thread_id"]
        response = await client.post(f"/api/discussions/{did}/say", json={"text": ""})
    assert response.status_code == 422


def _panel_say_reply(text: str) -> str:
    return json.dumps({"text": text, "based_on": []}, ensure_ascii=False)


def _synthesis_reply() -> str:
    return json.dumps(
        {"agreed": ["都认要写"], "disagreed": [], "open": ["给谁看"]},
        ensure_ascii=False,
    )


async def test_run_panel_round_join_and_abort_over_http() -> None:
    app, _ = _app(
        replies={
            "discussion": [_panel_say_reply("A 先说"), _panel_say_reply("B 再说")],
            "discussion_synthesis": [_synthesis_reply()],
        }
    )
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "日报要不要日报", "role_ids": ["dlg.challenger", "dlg.user_voice"]},
        )
        assert started.status_code == 200, started.text
        did = started.json()["thread_id"]
        assert [m["role_id"] for m in started.json()["participants"]] == [
            "dlg.challenger",
            "dlg.user_voice",
        ]

        ran = await client.post(f"/api/discussions/{did}/run-round")
        assert ran.status_code == 200, ran.text
        body = ran.json()
        assert sorted(t["author"] for t in body["turns"]) == [
            "dlg.challenger",
            "dlg.user_voice",
        ]
        assert len(body["summaries"]) == 1
        assert body["summaries"][0]["agreed"] == ["都认要写"]

        joined = await client.post(
            f"/api/discussions/{did}/join", json={"role_id": "dlg.analogist"}
        )
        assert joined.status_code == 200, joined.text
        members = {m["role_id"]: m["joined_round"] for m in joined.json()["participants"]}
        assert members == {"dlg.challenger": 1, "dlg.user_voice": 1, "dlg.analogist": 2}

        aborted = await client.post(f"/api/discussions/{did}/abort-run")
        assert aborted.status_code == 200, aborted.text
        assert aborted.json() == {"aborted": False}  # 没在跑幂等成功
