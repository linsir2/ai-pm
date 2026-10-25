"""三方结构化联网申请穿过用例层：申请真的挂到 `Round.web_requests` 上。

B1 只做了领域与兼容层；这里钉的是三条真报文（分析者 / 席位 / 整合者）各自
**从模型回包一路走到轮次聚合**，以及"每方 4 条时第 4 条 TOOL 留痕"。
"""

import json

from pmstudio.domain.round.round import DropSource
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
            }
        ],
        "seats": [{"seat_id": SEAT, "why": "导出要评估数据量与耗时"}],
    },
    ensure_ascii=False,
)

SEAT_REPLY = json.dumps(
    {
        "basis": "读了 s1–s13 与 E1",
        "opinions": [],
    },
    ensure_ascii=False,
)


def claims_reply(*queries: dict) -> str:
    payload = json.loads(CLAIMS_REPLY)
    payload["web_queries"] = list(queries)
    return json.dumps(payload, ensure_ascii=False)


def seat_reply(*requests: dict) -> str:
    payload = json.loads(SEAT_REPLY)
    payload["web_requests"] = list(requests)
    return json.dumps(payload, ensure_ascii=False)


def integration_reply(*requests: dict) -> str:
    """一道不阻塞装配的题 + 这轮提名的联网申请。"""

    return json.dumps(
        {
            "questions": [
                {
                    "kind": "gap",
                    "title": "单次导出最多多少行？",
                    "why": "量级决定同步还是异步",
                    "consequence_if_skipped": "按同步实现，超时风险由用户承担",
                    "target": {"zone": "body", "section_id": "s5"},
                    "type": "text",
                }
            ],
            "list_items": [],
            "proposals": [],
            "web_requests": list(requests),
        },
        ensure_ascii=False,
    )


def queries(count: int) -> list[dict]:
    return [
        {"query": f"第{index}条外部事实", "why": "文档与用户都给不了"}
        for index in range(1, count + 1)
    ]


async def started_round(harness: Harness):  # type: ignore[no-untyped-def]
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    return round_id


async def test_analyzer_query_lands_as_an_analyzer_web_request():
    harness = Harness(
        replies={
            "claims": claims_reply(
                {"query": "RBAC 主流做法", "why": "要查行业通行的权限模型"}
            )
        }
    )

    round_id = await started_round(harness)

    round_ = harness.rounds.get(round_id)
    assert len(round_.web_requests) == 1
    request = round_.web_requests[0]
    assert request.request_id == "w1"
    assert request.query == "RBAC 主流做法"
    assert request.why == "要查行业通行的权限模型"
    assert request.origin == "analyzer"
    assert request.seat_id is None
    assert request.pass_index is None
    assert request.state.value == "proposed"
    assert request.created_at == harness.clock.now()
    # 旧只读派生仍然成立（B1 的兼容面）
    assert [item.query for item in round_.web_queries] == ["RBAC 主流做法"]


async def test_analyzer_fourth_query_is_dropped_as_a_tool_trace():
    harness = Harness(replies={"claims": claims_reply(*queries(4))})

    round_id = await started_round(harness)

    round_ = harness.rounds.get(round_id)
    assert [item.query for item in round_.web_requests] == [
        "第1条外部事实",
        "第2条外部事实",
        "第3条外部事实",
    ]
    assert [(item.handle, item.source) for item in round_.dropped] == [
        ("检索词 第4条外部事实", DropSource.TOOL)
    ]


async def test_seat_requests_are_attached_with_seat_identity_and_the_fourth_is_dropped():
    harness = Harness(
        replies={"claims": CLAIMS_REPLY},
        seat_replies={SEAT: seat_reply(*queries(4))},
    )
    round_id = await started_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    attached = [
        (item.request_id, item.query, item.origin, item.seat_id, item.pass_index)
        for item in round_.web_requests
    ]
    assert attached == [
        ("w1", "第1条外部事实", "seat", SEAT, 1),
        ("w2", "第2条外部事实", "seat", SEAT, 1),
        ("w3", "第3条外部事实", "seat", SEAT, 1),
    ]
    drops = [
        (item.handle, item.seat_id, item.pass_index, item.source)
        for item in round_.dropped
    ]
    assert drops == [("联网申请 第4条外部事实", SEAT, 1, DropSource.TOOL)]
    assert harness.web_search.calls == []


async def test_integrator_request_is_attached_in_the_final_uow_and_not_executed():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": integration_reply(
                {"query": "ISO 8601 日期格式", "why": "要查导出文件的行业格式"}
            ),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    attached = [
        (item.request_id, item.query, item.origin, item.seat_id, item.pass_index)
        for item in round_.web_requests
    ]
    assert attached == [("w1", "ISO 8601 日期格式", "integrator", None, None)]
    assert round_.web_requests[0].state.value == "proposed"
    assert harness.web_search.calls == []


async def test_integrator_fourth_request_is_dropped_as_a_tool_trace():
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": integration_reply(*queries(4)),
        },
        seat_replies={SEAT: SEAT_REPLY},
    )
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [item.query for item in round_.web_requests] == [
        "第1条外部事实",
        "第2条外部事实",
        "第3条外部事实",
    ]
    drops = [
        (item.handle, item.seat_id, item.pass_index, item.source)
        for item in round_.dropped
    ]
    assert drops == [("联网申请 第4条外部事实", None, 1, DropSource.TOOL)]
    assert harness.web_search.calls == []


async def test_all_three_parties_share_one_request_id_space():
    """三方同一个编号序列：`next_web_request_id` 扫描整张表，不按方各起一套。"""

    harness = Harness(
        replies={
            "claims": claims_reply(
                {"query": "分析者要查的事实", "why": "文档与用户都给不了"}
            ),
            "integration": integration_reply(
                {"query": "整合者要查的事实", "why": "文档与用户都给不了"}
            ),
        },
        seat_replies={
            SEAT: seat_reply({"query": "席位要查的事实", "why": "文档与用户都给不了"})
        },
    )
    round_id = await started_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)

    await harness.integrate_round()(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    assert [(item.request_id, item.origin) for item in round_.web_requests] == [
        ("w1", "analyzer"),
        ("w2", "seat"),
        ("w3", "integrator"),
    ]
    # 旧键只看分析者，不被席位 / 整合者的申请污染
    assert [item.query for item in round_.web_queries] == ["分析者要查的事实"]
