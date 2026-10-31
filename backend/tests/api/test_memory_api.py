"""记忆的 HTTP 契约（M6b）：确认带 `accepted_memory`、删除走 `DELETE .../memory/{id}`。

口径：`docs/plans/2026-10-03-M6-跨轮记忆本体.md` §6.4 / §9（P1、P2）。
老客户端（不传 `accepted_memory`）行为**逐字节不变**——这是本文件第一条用例。
"""

import json

import pytest

from pmstudio.api.app import create_app
from pmstudio.domain.memory.memory import ProjectMemory
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from tests.support.api_client import async_client
from tests.support.builders import PROJECT, SEAT
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
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
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
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "同类导出的常见上限",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)

DRAFT_REPLY = json.dumps(
    {
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "entry_id": None,
                "target": {
                    "zone": "body",
                    "section_id": "s5",
                    "feature_slug": "order-export",
                    "field": "exceptions",
                },
                "text": "无导出权限的用户不显示导出入口",
                "origin_type": "derived",
                "evidence_refs": ["E1", "E2"],
                "rationale": "订单含客户信息",
            }
        ],
        "dropped_opinions": [{"handle": f"{SEAT}:O1", "reason": "已落到上面的提案里"}],
    },
    ensure_ascii=False,
)

CURATOR_REPLY = json.dumps(
    {
        "changes": [
            {
                "op": "add",
                "kind": "decision",
                "text": "导出保留期是 30 天",
                "refs": ["E1"],
                "reason": "用户这一轮定下来的口径",
            }
        ]
    },
    ensure_ascii=False,
)


async def harness_at_draft() -> tuple[Harness, str]:  # type: ignore[no-untyped-def]
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            "memory_curation": CURATOR_REPLY,
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    if FeatureSlug("order-export") not in round_.authorized_new_features:
        round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
        harness.rounds.save(round_)
    await harness.integrate_round()(round_id=round_id)
    return harness, str(round_id)


def seed_memory(harness: Harness) -> None:
    """直接塞一条**已确认**的记忆（跳过 Curator 与确认：这一条测的是读/删两个端点）。"""

    memory = ProjectMemory.empty(PROJECT).apply(
        (
            MemoryChange(
                op=MemoryOp.ADD,
                kind=MemoryKind.DECISION,
                text="导出保留期是 30 天",
                refs=("E1",),
            ),
        ),
        round_id="r0",
        known_refs=frozenset({"E1"}),
        now=harness.clock.now(),
        ids=harness.ids,
    )
    harness.memory.seed(memory)


@pytest.fixture()
def app():
    harness = Harness(replies={}, seat_replies={})
    harness.seed_project()
    return create_app(harness.api_services(model_name="m"))


async def test_workspace_exposes_the_confirmed_memory():
    harness, _round_id = await harness_at_draft()
    seed_memory(harness)
    app = create_app(harness.api_services(model_name="m"))

    async with async_client(app) as client:
        response = await client.get(f"/api/projects/{PROJECT}/workspace")

    assert response.status_code == 200
    assert [
        (item["kind"], item["text"], item["refs"]) for item in response.json()["memory"]
    ] == [("decision", "导出保留期是 30 天", ["E1"])]


async def test_confirm_accepts_accepted_memory_and_returns_the_round():
    harness, round_id = await harness_at_draft()
    app = create_app(harness.api_services(model_name="m"))
    round_ = harness.rounds.get(round_id)
    accepted = [str(item.proposal_id) for item in round_.draft.proposals]

    async with async_client(app) as client:
        response = await client.post(
            f"/api/rounds/{round_id}/confirm",
            json={"draft_id": str(round_.draft.draft_id),
                  "accepted": accepted, "accepted_memory": ["mem#1"]},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["version_no"] == 2
    assert body["round"]["step"] == "done"
    assert [item.text for item in harness.memory.get(PROJECT).entries] == [
        "导出保留期是 30 天"
    ]


async def test_confirm_without_accepted_memory_writes_no_memory():
    """老客户端（不带这个字段）行为不变：文档照写，记忆一条都不动。"""

    harness, round_id = await harness_at_draft()
    app = create_app(harness.api_services(model_name="m"))
    round_ = harness.rounds.get(round_id)
    accepted = [str(item.proposal_id) for item in round_.draft.proposals]

    async with async_client(app) as client:
        response = await client.post(
            f"/api/rounds/{round_id}/confirm",
            json={"draft_id": str(round_.draft.draft_id), "accepted": accepted}
        )

    assert response.status_code == 200
    assert harness.memory.get(PROJECT).entries == ()


async def test_forget_memory_deletes_one_entry():
    harness, _round_id = await harness_at_draft()
    seed_memory(harness)
    app = create_app(harness.api_services(model_name="m"))
    stored = harness.memory.get(PROJECT)

    async with async_client(app) as client:
        response = await client.delete(
            f"/api/projects/{PROJECT}/memory/{stored.entries[0].memory_id}"
        )

    assert response.status_code == 204
    assert harness.memory.get(PROJECT).entries == ()


async def test_forgetting_an_unknown_entry_is_404(app):
    async with async_client(app) as client:
        response = await client.delete(f"/api/projects/{PROJECT}/memory/mem-404")

    assert response.status_code == 404
