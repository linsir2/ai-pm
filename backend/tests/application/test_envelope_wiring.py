"""信封的**消费者**与**唯一性**——不是形状声明，是真在链路上。

`02-编排与数据.md §4.4` 说信封是"智能体之间唯一的通信形状"。如果它只是躺在 Outcome 上
没人读，那它就是装饰；这一条守住两件事：

1. **有消费者**：`packet.emitted` 事件带 `packet_id` / `run_id` / `kind` / `produced_by`——
   "这一包是谁产出的、依据是什么、后来哪些条没进卡片"从"翻 5 处日志"变成"按 id 一查"。
2. **唯一**：同一席位在同一轮里跑多趟、或重试、或整合者重跑，`packet_id` 都不重复。
   身份由**协调者**分配（用例层知道"第几趟第几次"，agent 只知道自己是谁产出的）。
"""

import json

from pmstudio.application.events import PACKET_EMITTED
from tests.support.builders import PROJECT
from tests.support.harness import Harness

SEAT = "seat.data_scale"

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
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": "O1",
                "form": "judgment",
                "about": "导出的量级",
                "claim": "必须问用户",
                "because": "1 万行与 10 万行是两套实现",
                "evidence_refs": ["E1"],
            }
        ],
    },
    ensure_ascii=False,
)

OVER_CAP_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 全文与 E1",
        "opinions": [
            {
                "local_id": f"O{index}",
                "form": "judgment",
                "about": f"第 {index} 件事",
                "claim": "……",
                "because": "……",
                "evidence_refs": ["E1"],
            }
            for index in range(1, 15)
        ],
    },
    ensure_ascii=False,
)


def a_harness(seat_reply: str = SEAT_REPLY) -> Harness:
    harness = Harness(
        replies={"claims": CLAIMS_REPLY},
        seat_replies={SEAT: seat_reply},
    )
    harness.seed_project()
    return harness


async def round_at_seat_round(harness: Harness) -> str:  # type: ignore[no-untyped-def]
    """建轮 → 落主张 → 应用开场卡，停在"正在跑席位轮"。"""

    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=[SEAT])
    return round_id


def packets(harness: Harness) -> list[dict]:  # type: ignore[no-untyped-def]
    return [event.payload for event in harness.events.published if event.name == PACKET_EMITTED]


async def test_packet_emitted_carries_the_envelope_identity() -> None:
    """事件是信封的第一个真实消费者：讨论流按 packet_id 回查这一趟。"""

    harness = a_harness()
    round_id = await round_at_seat_round(harness)
    await harness.run_seat_round()(round_id=round_id)

    sent = packets(harness)
    assert sent, "这一趟没发 packet.emitted——信封就没有消费者"
    assert sent[0]["packet_id"] == f"pkt-{round_id}-seat_review-{SEAT}-p1a1"
    assert sent[0]["run_id"] == round_id
    assert sent[0]["kind"] == "seat_review"
    assert sent[0]["produced_by"] == SEAT


async def test_the_envelope_basis_is_the_only_source_of_the_basis_sent_to_the_ui() -> None:
    """`basis` 只有一处存储（信封）；事件里那一份是从信封取的，不是另算的。"""

    harness = a_harness()
    round_id = await round_at_seat_round(harness)
    await harness.run_seat_round()(round_id=round_id)

    assert packets(harness)[0]["basis"] == "读了 s1–s13 全文与 E1"


async def test_a_retry_of_the_same_seat_gets_a_different_packet_id() -> None:
    """同一趟里重装一次（`validation_error` 非空）必须换 id——否则两次回包无法区分。"""

    harness = a_harness()
    round_id = await round_at_seat_round(harness)
    bad_then_good = [
        json.dumps({"basis": "读了 s5", "opinions": [{"local_id": "O1"}]}, ensure_ascii=False),
        SEAT_REPLY,
    ]
    harness.gateway._seat_replies = {SEAT: list(bad_then_good)}  # noqa: SLF001
    await harness.run_seat_round()(round_id=round_id)

    sent = packets(harness)
    assert len(sent) == 1, "重装后应该只交回一包（失败的第一次不产包）"
    assert sent[0]["packet_id"] == f"pkt-{round_id}-seat_review-{SEAT}-p1a2", (
        "重试那一趟的 id 必须带 a2 —— 否则与第一次的 id 撞车"
    )


async def test_overflow_still_leaves_a_trace_and_the_envelope_still_carries_it() -> None:
    """超限的留痕（`Round.dropped`）与信封同源：同一个 `overflow` 报告既进留痕也留在信封上。

    **这一条是"闭环"的真正断言**：留痕行的 `packet_id` 必须等于同一趟 `packet.emitted`
    事件里的那个 id。有了它，"为什么这条没进卡片"从"翻 5 处日志"变成"按 id 一查"。
    """

    harness = a_harness(OVER_CAP_REPLY)
    round_id = await round_at_seat_round(harness)
    await harness.run_seat_round()(round_id=round_id)

    view = harness.rounds.get(round_id)
    assert view.dropped, "超限的 2 条没有留痕——那违反『一条都不许默默丢掉』"
    sent = packets(harness)
    assert sent, "这一趟没发 packet.emitted——信封就没有消费者"

    # 留痕行必须能指回它来自哪一包（§4.4 信封的 packet_id）
    assert all(item.packet_id for item in view.dropped), (
        "席位来源的留痕没有 packet_id——它来自模型回包，必须有"
    )
    assert {item.packet_id for item in view.dropped} == {
        event.payload["packet_id"] for event in harness.events.published
        if event.name == PACKET_EMITTED
    }, "留痕的 packet_id 与事件的 packet_id 对不上——两个锚点必须相等"
