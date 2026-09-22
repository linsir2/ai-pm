import json

import pytest

from pmstudio.application.errors import ModelCallFailed
from pmstudio.application.events import PACKET_EMITTED
from pmstudio.application.use_cases import ClaimEdit
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.target import Target
from tests.support.builders import PROJECT, SEAT, make_seat
from tests.support.harness import Harness

SECOND_SEAT = "seat.permissions"

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的性能上限",
                "claim": "单次导出应在 60 秒内完成",
                "because": "量级在 1 万级，同步实现可满足",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)


def claims_reply() -> str:
    return json.dumps(
        {
            "basis": "读了空文档",
            "claims": [
                {
                    "text": "要做一个订单导出功能",
                    "raw_span": "想做个订单导出的功能",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
        },
        ensure_ascii=False,
    )


async def started_round(harness: Harness):
    """建轮 + 落主张，停在开场卡（用真正的 StartRound，不手搓状态）。"""

    use_case = harness.start_round()
    round_id = await use_case.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await use_case.analyze(round_id=round_id)
    return round_id


async def test_submit_opening_applies_claim_edits_and_starts_the_seat_round():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    round_id = await started_round(harness)

    await harness.submit_opening()(
        round_id=round_id,
        seats=(SEAT,),
        claim_edits=(ClaimEdit(claim_id="c1", label="goal", target=Target.body("s2")),),
    )

    round_ = harness.rounds.get(round_id)
    assert round_.claims[0].label.value == "goal"
    assert round_.claims[0].target == Target.body("s2")
    assert round_.seats == (SEAT,)
    assert round_.step is Step.SEATS_RUNNING
    assert round_.seat_round_count == 1


async def test_submit_opening_bad_unknown_claim_id_is_rejected():
    harness = Harness(replies={"claims": claims_reply()})
    harness.seed_project()
    round_id = await started_round(harness)

    with pytest.raises(ShapeError):
        await harness.submit_opening()(
            round_id=round_id,
            seats=(SEAT,),
            claim_edits=(ClaimEdit(claim_id="c9", label="need", target=Target.body("s5")),),
        )


async def test_run_seat_round_attests_and_numbers_judgments_only():
    harness = Harness(
        replies={"claims": claims_reply()}, seat_replies={SEAT: SEAT_REPLY}
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert len(round_.current_reviews) == 1
    ledger = harness.evidences.get(PROJECT)
    judgments = [
        evidence for evidence in ledger.evidences() if evidence.source.seat_id == SEAT
    ]
    assert len(judgments) == 1  # 判断占编号；疑问不占
    assert judgments[0].eid == EvidenceId("E2")  # E1 是那条主张，判断从 E2 起
    # 建轮 / 落主张 / 收开场卡 / 席位交回落库 / 汇合点编号（读取不开事务）
    assert harness.uow.commits == 5


async def test_run_seat_round_emits_one_packet_event_per_seat():
    harness = Harness(replies={"claims": claims_reply()}, seat_replies={SEAT: SEAT_REPLY})
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    await harness.run_seat_round()(round_id=round_id)

    packets = [event for event in harness.events.published if event.name == PACKET_EMITTED]
    assert len(packets) == 1
    assert packets[0].payload["seat_id"] == SEAT
    assert packets[0].payload["opinions"][0]["claim"] == "单次导出应在 60 秒内完成"
    assert packets[0].payload["opinions"][0]["eids"] == ["E1"]  # 依据编号（输入侧）


async def test_run_seat_round_bad_one_seat_failure_fails_the_round_but_keeps_what_came_back():
    """失败必须是失败（整轮 failed、文档零写入），但**已交回的意见不丢**（计划 §3.11）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY},  # 只给一个席位配脚本
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))

    with pytest.raises(ModelCallFailed):
        await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.FAILED
    assert SECOND_SEAT in round_.failure.reason
    assert len(round_.seat_reviews) == 1  # A 交回了，留着
    assert round_.pending_seats == (SECOND_SEAT,)
    # 汇合点还没到 → 判断还没占编号，账本里只有那条主张
    assert harness.evidences.get(PROJECT).evidences()[-1].eid == EvidenceId("E1")


async def test_run_seat_round_only_refills_seats_that_have_not_reported():
    """断点续传：A 已交回、B 崩了 → 重进只叫 B，A 不重跑、编号也不重复（§3.11）。"""

    harness = Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY},  # 只给 A 配脚本 → B 会失败
        seats=(make_seat(), make_seat(SECOND_SEAT, title="权限与角色")),
    )
    harness.seed_project()
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT, SECOND_SEAT))
    with pytest.raises(ModelCallFailed):
        await harness.run_seat_round()(round_id=round_id)

    assert harness.rounds.get(round_id).pending_seats == (SECOND_SEAT,)

    await harness.retry_round()(round_id=round_id)  # 失败 → resume，回到 seats_running
    harness.gateway._seat_replies[SECOND_SEAT] = [SEAT_REPLY]  # 补上 B 的脚本
    await harness.run_seat_round()(round_id=round_id)

    seat_requests = [
        request.metadata["seat_id"]
        for request in harness.gateway.requests
        if request.purpose == "seat_review"
    ]
    assert seat_requests == [SEAT, SECOND_SEAT, SECOND_SEAT]  # A 只被叫过一次
    judgment_eids = [
        str(evidence.eid)
        for evidence in harness.evidences.get(PROJECT).evidences()
        if evidence.source.seat_id is not None
    ]
    assert len(judgment_eids) == len(set(judgment_eids))  # 编号没被重复占用
