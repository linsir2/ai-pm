"""复现：`packet_id` 唯一性在**生产路径**上撞车（自查发现的 Critical）。

`IntegrateRound` 的槽位第二维来自局部变量 `validate_left`，而它在每次进入整合时重置成 2。
一轮里整合者会跑多次（问卷 → 答完 → 第二趟席位 → 第二批整合，`02` §13 第 6/7/8 步），
于是每次都是 `a1` —— 同一个 `packet_id` 出现在两趟上。

后果比"没有 id"更坏：`use_cases.py` 那段注释写着"按 packet_id 一查即可"，
撞 id 之后查出来的是两趟混在一起的记录，看起来能查、结果是错的。
"""

import json

from pmstudio.application.events import PACKET_EMITTED
from pmstudio.application.use_cases import AnswerInput
from tests.support.builders import PROJECT
from tests.support.harness import Harness

SEAT = "seat.data_scale"


def _harness() -> Harness:
    return Harness(
        replies={
            "claims": json.dumps(
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
            ),
            "integration": [
                json.dumps(
                    {
                        "questions": [
                            {
                                "title": "导出的量级大概是多少？",
                                "why": "两套实现",
                                "consequence_if_skipped": "只能按 1 万行同步做",
                                "kind": "gap",
                                "target": {"zone": "body", "section_id": "s5"},
                                "evidence_refs": ["E1"],
                            }
                        ],
                        "list_items": [],
                        "proposals": [],
                        "dropped_opinions": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
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
                                    "feature_slug": "export",
                                    "field": "name",
                                },
                                "text": "订单导出",
                                "origin_type": "derived",
                                "evidence_refs": ["E1"],
                            }
                        ],
                        "dropped_opinions": [],
                    },
                    ensure_ascii=False,
                ),
            ],
            "seat_review": json.dumps(
                {
                    "basis": "读了 s5 与 E1",
                    "opinions": [
                        {
                            "local_id": "O1",
                            "form": "question",
                            "about": "导出的数据量级",
                            "claim": "必须先问用户",
                            "because": "两套实现",
                            "evidence_refs": ["E1"],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
        seat_replies={
            SEAT: json.dumps(
                {
                    "basis": "读了 s5 与 E1",
                    "opinions": [
                        {
                            "local_id": "O1",
                            "form": "question",
                            "about": "导出的数据量级",
                            "claim": "必须先问用户",
                            "because": "两套实现",
                            "evidence_refs": ["E1"],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        },
    )


def spy_on_the_agent(harness: Harness, method: str) -> list[str]:  # type: ignore[no-untyped-def]
    """在 agent 方法上挂一个记录器，看用例层到底传了哪些槽位。"""

    seen: list[str] = []
    agent = getattr(harness, method.split(".")[0])
    name = method.split(".")[1]
    original = getattr(agent, name)

    async def recorder(**kwargs):  # type: ignore[no-untyped-def]
        seen.append(str(kwargs.get("packet_slot")))
        return await original(**kwargs)

    setattr(agent, name, recorder)
    return seen


async def _first_batch(harness: Harness) -> str:  # type: ignore[no-untyped-def]
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=[SEAT])
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)
    return round_id


def ids(harness: Harness) -> list[str]:  # type: ignore[no-untyped-def]
    return [
        str(event.payload["packet_id"])
        for event in harness.events.published
        if event.name == PACKET_EMITTED
    ]


async def test_packet_ids_are_unique_across_a_whole_round() -> None:
    """一整轮里出现过的每一个 packet_id 都必须是不同的——包括问卷回路后的第二次整合。"""

    harness = _harness()
    harness.seed_project()
    slots = spy_on_the_agent(harness, "integrator.integrate")
    round_id = await _first_batch(harness)

    # 答完问卷 → 第二趟席位 → 第二批整合（`02` §13 第 8 步与第 6 步再来一次）
    first = harness.rounds.get(round_id).questionnaire
    await harness.submit_answers()(
        round_id=round_id,
        form_id=str(first.form_id),
        answers=tuple(
            AnswerInput(question_id=str(item.question_id), text="第一批的答案")
            for item in first.questions
        ),
        skipped=(),
    )
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    # 事件里只有席位包；整合者的 id 不进事件，所以直接查它收到的槽位。
    assert slots, "整合者一次都没被调用——这个用例测不到东西"
    assert len(set(slots)) == len(slots), f"整合者的槽位重复了：{slots}"
    # 跨轮次的两趟必须落在不同的 `i{整合次数}` 上（局部重试计数每次进入整合都会重置，
    # 拿它当槽位会让第二次整合拿到与第一次一样的 id —— 这条断言就是为了钉住那个坑）。
    assert len({slot.split("a")[0] for slot in slots}) > 1, (
        f"两趟整合落进了同一个槽位前缀：{slots}"
    )
