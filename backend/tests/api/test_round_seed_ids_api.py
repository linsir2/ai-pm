"""起轮带调研种子（M8.1 回归）：`seed_ids` 必须穿过 HTTP 边界落到 Round 上。

背景：`StartRoundRequest` 一度没有 `seed_ids` 字段，前端发的种子 id
被 pydantic 静默丢掉——`Round.seeds` 经 HTTP 永远是空的，
`_resolve_seeds` 的"人话拒绝"在真链路上从没跑过。
这条测试钉死这条边界：给种子 → 轮上必须有回链；给错种子 → 人话拒绝。
"""

import json

from pmstudio.api.app import create_app
from pmstudio.domain.shared.ids import RoundId
from tests.support.api_client import async_client
from tests.support.builders import PROJECT, document_with_feature
from tests.support.harness import Harness

INQUIRY_REPLY = json.dumps(
    {
        "basis": "读了 s5 的功能描述",
        "hypotheses": [
            {
                "local_id": "H1",
                "statement": "运营做日报时按客户名找单",
                "grounding": "invented",
                "based_on": [],
                "falsification": "访谈没人这么找",
                "evidence_needed": "两个运营的访谈",
            }
        ],
        "blind_spots": [
            {"local_id": "B1", "statement": "归属未知", "next_step": "去问数据组"}
        ],
    },
    ensure_ascii=False,
)

CLAIMS_REPLY = json.dumps(
    {
        "basis": "读了文档与本轮输入",
        "claims": [
            {
                "text": "按调研的假设去验证",
                "raw_span": "调研说运营按客户名找单",
                "label": "need",
                "zone": "body",
                "section_id": "s10",
            }
        ],
        "seats": [{"seat_id": "seat.data_scale", "why": "与量级有关"}],
    },
    ensure_ascii=False,
)

SAY_REPLY = json.dumps(
    {"text": "我来挑个刺：先把范围想清楚。", "based_on": []}, ensure_ascii=False
)

CARDS_REPLY = json.dumps(
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


def _app() -> Harness:
    harness = Harness(
        replies={
            "inquiry": INQUIRY_REPLY,
            "claims": CLAIMS_REPLY,
            "discussion": SAY_REPLY,
            "discussion_cards": CARDS_REPLY,
        },
        seat_replies={},
    )
    harness.seed_project()
    harness.documents.seed(document_with_feature())
    return harness


async def _seeded_exploration(client) -> str:  # type: ignore[no-untyped-def]
    started = await client.post(
        f"/api/projects/{PROJECT}/explorations",
        json={"kind": "inquiry", "scope": {"kind": "document"}},
    )
    assert started.status_code == 200, started.text
    xid = started.json()["exploration_id"]
    kept = await client.post(
        f"/api/explorations/{xid}/candidates/H1/keep", json={"as": "seed"}
    )
    assert kept.status_code == 200, kept.text
    return xid


async def test_start_round_carries_seed_ids() -> None:
    harness = _app()
    app = create_app(harness.api_services(model_name="m"))
    async with async_client(app) as client:
        xid = await _seeded_exploration(client)
        response = await client.post(
            f"/api/projects/{PROJECT}/rounds",
            json={
                "input": "调研说运营按客户名找单，先记下来",
                "material_ids": [],
                "seed_ids": [f"exploration:{xid}#H1"],
            },
        )
        assert response.status_code == 200, response.text
        round_id = response.json()["round_id"]

    round_ = harness.rounds.get(RoundId(round_id))
    assert [(ref.origin, ref.ref_id, ref.item_id) for ref in round_.seeds] == [
        ("exploration", xid, "H1")
    ]


async def test_start_round_carries_discussion_card_seed_ids() -> None:
    """讨论卡种子也要穿过 HTTP 边界（M12）：收下为 seed 的卡 → 起轮 → Round 上有回链。"""

    harness = _app()
    app = create_app(harness.api_services(model_name="m"))
    async with async_client(app) as client:
        started = await client.post(
            f"/api/projects/{PROJECT}/discussions",
            json={"topic": "导出优化", "role_id": "dlg.challenger"},
        )
        assert started.status_code == 200, started.text
        did = started.json()["thread_id"]
        said = await client.post(
            f"/api/discussions/{did}/say", json={"text": "我想做导出"}
        )
        assert said.status_code == 200, said.text
        cards = await client.post(f"/api/discussions/{did}/cards")
        assert cards.status_code == 200, cards.text
        card_id = cards.json()["cards"][0]["card_id"]
        kept = await client.post(
            f"/api/discussions/{did}/cards/{card_id}/keep", json={"as": "seed"}
        )
        assert kept.status_code == 200, kept.text

        response = await client.post(
            f"/api/projects/{PROJECT}/rounds",
            json={
                "input": "调研说运营按客户名找单，先记下来",
                "material_ids": [],
                "seed_ids": [f"discussion:{did}#{card_id}"],
            },
        )
        assert response.status_code == 200, response.text
        round_id = response.json()["round_id"]

    round_ = harness.rounds.get(RoundId(round_id))
    assert [(ref.origin, ref.ref_id, ref.item_id) for ref in round_.seeds] == [
        ("discussion", did, card_id)
    ]


async def test_start_round_rejects_unknown_seed() -> None:
    harness = _app()
    app = create_app(harness.api_services(model_name="m"))
    async with async_client(app) as client:
        response = await client.post(
            f"/api/projects/{PROJECT}/rounds",
            json={
                "input": "带一条不存在的种子",
                "material_ids": [],
                "seed_ids": ["exploration:x-nope#H1"],
            },
        )
    assert response.status_code == 404
    assert response.json()["code"] == "unknown_exploration"


async def test_start_round_rejects_legacy_seed_format_with_human_message() -> None:
    """老格式（没有来源前缀）现在给 422 人话——格式换了就得说清换成了什么。"""

    harness = _app()
    app = create_app(harness.api_services(model_name="m"))
    async with async_client(app) as client:
        response = await client.post(
            f"/api/projects/{PROJECT}/rounds",
            json={
                "input": "带一条老格式的种子",
                "material_ids": [],
                "seed_ids": ["x-1#H1"],
            },
        )
    assert response.status_code == 422
    assert "来源:id#条目id" in response.json()["message"]
