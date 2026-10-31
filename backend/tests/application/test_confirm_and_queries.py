import json

import pytest

from pmstudio.application.events import DOC_CHANGED, ROUND_UPDATED
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.errors import IllegalStepTransition
from pmstudio.domain.shared.ids import FeatureSlug
from pmstudio.harness.errors import ModelCallFailed
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
                "feature_slug": "order-export",  # 用户点名 → 授权
            }
        ],
        "seats": [
            {"seat_id": SEAT, "why": "导出要评估数据量与耗时"},
        ],
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
                "evidence_refs": ["E1"],
                "rationale": "订单含客户信息",
            }
        ],
    },
    ensure_ascii=False,
)


def confirmable_harness() -> Harness:
    return Harness(
        replies={"claims": CLAIMS_REPLY, "integration": DRAFT_REPLY},
        seat_replies={SEAT: SEAT_REPLY},
    )


async def round_awaiting_confirm(harness: Harness):
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)
    return round_id


async def test_confirm_draft_writes_document_and_bumps_version():
    harness = confirmable_harness()
    round_id = await round_awaiting_confirm(harness)

    result = await harness.confirm_draft()(
        round_id=round_id, draft_id=str(harness.rounds.get(round_id).draft.draft_id),
        accepted=("p01",),
    )

    assert result.version.version_no == 2  # v1 是建项目时的空骨架
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.DONE
    assert round_.confirmed_version_no == 2
    document = harness.documents.get(PROJECT)
    assert document.feature(FeatureSlug("order-export")) is not None
    assert document.field_block(FeatureSlug("order-export"), "exceptions") is not None
    assert DOC_CHANGED in harness.events.names
    assert harness.events.names[-2:] == [DOC_CHANGED, ROUND_UPDATED]


async def test_confirm_draft_bad_second_confirm_is_rejected_and_writes_nothing():
    harness = confirmable_harness()
    round_id = await round_awaiting_confirm(harness)
    draft_id = str(harness.rounds.get(round_id).draft.draft_id)
    await harness.confirm_draft()(round_id=round_id, draft_id=draft_id, accepted=("p01",))
    versions_before = len(harness.documents.get(PROJECT).versions)

    with pytest.raises(IllegalStepTransition):
        await harness.confirm_draft()(round_id=round_id, draft_id=draft_id, accepted=("p01",))

    assert len(harness.documents.get(PROJECT).versions) == versions_before


async def test_retry_round_resumes_from_failed_step():
    harness = Harness(replies={})  # 没有 claims 脚本 → 第一次就失败
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    with pytest.raises(ModelCallFailed):
        await start.analyze(round_id=round_id)
    assert harness.rounds.get(round_id).step is Step.FAILED

    await harness.retry_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.STARTED  # 恢复到失败发生的那一步
    assert round_.failure is None
    assert harness.uow.rollbacks == 0


async def test_retry_round_on_a_healthy_round_is_a_noop():
    """"继续"这个动作对没失败的轮是无害的——关页面/进程崩了以后轮停在 seats_running，
    它不是 failed，但同样要能接着跑（§3.7）。"""

    harness = confirmable_harness()
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    before = harness.rounds.get(round_id)

    await harness.retry_round()(round_id=round_id)

    after = harness.rounds.get(round_id)
    assert after.step is before.step
    assert after.waiting_on is before.waiting_on


async def test_get_workspace_returns_document_and_latest_round():
    harness = confirmable_harness()
    round_id = await round_awaiting_confirm(harness)
    harness.index.set_latest_round("p1", round_id)  # 真实现由 SQL 查最新一轮

    view = await harness.get_workspace()(project_id=PROJECT)

    assert view.project.title == "订单中台"
    assert view.project.version_no == 1
    assert view.document.version_no == 1
    assert view.round is not None
    assert view.round.round_id == str(round_id)
    assert view.round.waiting_on == "draft_card"
    assert view.round.draft is not None


async def test_get_workspace_without_round_returns_none():
    harness = confirmable_harness()
    harness.seed_project()
    view = await harness.get_workspace()(project_id=PROJECT)
    assert view.round is None


async def test_list_projects_returns_summaries():
    harness = confirmable_harness()
    harness.seed_project()
    summaries = await harness.list_projects()()
    assert [item.project_id for item in summaries] == ["p1"]
    assert summaries[0].title == "订单中台"
