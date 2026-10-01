"""复述卡上的检索词勾选（工具层 C-2b，P17b §5）。

三条口径：
1. **勾了才记账**：用户勾中的检索词落进 `Round.web_queries_chosen`；
2. **没配工具就别答应**：勾了但服务端没这个工具 → `ShapeError`（422），**一次网络调用都没有**；
3. 超上限（`tool.web_search.max_queries = 3`）也是"请求不对"，不是"领域不变量被破坏"。

T9 **不含任何网络调用**：真正去查在 T10，这份文件只钉数据通道与两道校验。
"""

import json

import pytest

from pmstudio.application.use_cases import SubmitOpening
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import RoundId
from tests.support.builders import PROJECT, SEAT
from tests.support.harness import Harness


def claims_reply(*queries: dict) -> str:
    """最小可用报文 + 这轮提名的检索词。"""

    return json.dumps(
        {
            "basis": "读了空文档与本轮输入",
            "claims": [
                {
                    "text": "要做一个订单导出功能",
                    "raw_span": "想做个订单导出的功能",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了导出"}],
            "web_queries": list(queries),
        },
        ensure_ascii=False,
    )


RBAC = {"query": "RBAC 权限模型的主流做法", "why": "要查行业通行的权限模型"}


async def opened_at_the_recap_card(harness: Harness) -> RoundId:
    """建轮 → 分析：停在复述卡上（`waiting_on == opening_card`）。"""

    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    return round_id


async def test_submitting_the_opening_records_what_the_user_checked():
    harness = Harness(replies={"claims": claims_reply(RBAC)})
    round_id = await opened_at_the_recap_card(harness)

    await harness.submit_opening()(
        round_id=round_id, seats=(SEAT,), web_queries=("RBAC 权限模型的主流做法",)
    )

    round_ = harness.rounds.get(round_id)
    assert [item.query for item in round_.web_queries] == ["RBAC 权限模型的主流做法"]
    assert round_.web_queries_chosen == ("RBAC 权限模型的主流做法",)


async def test_checking_nothing_leaves_the_chosen_list_empty():
    harness = Harness(replies={"claims": claims_reply(RBAC)})
    round_id = await opened_at_the_recap_card(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    assert harness.rounds.get(round_id).web_queries_chosen == ()
    assert harness.web_search.calls == []      # 没勾 → 一次都没调


async def test_checking_a_query_without_the_tool_is_a_shape_error():
    """没配密钥就是没这个工具：答应下来再假装搜过，是仓库里最反对的那种失败。"""

    harness = Harness(replies={"claims": claims_reply(RBAC)}, available_tool_keys=frozenset())
    round_id = await opened_at_the_recap_card(harness)

    with pytest.raises(ShapeError) as error:
        await harness.submit_opening()(
            round_id=round_id, seats=(SEAT,), web_queries=("RBAC 权限模型的主流做法",)
        )

    assert "联网" in error.value.message
    assert harness.web_search.calls == []
    assert harness.rounds.get(round_id).web_queries_chosen == ()


async def test_more_queries_than_the_tool_allows_is_a_shape_error():
    """上限来自 `web_search.toml` 的 `max_queries`，不是写死的常数。"""

    harness = Harness(replies={"claims": claims_reply(RBAC)})
    round_id = await opened_at_the_recap_card(harness)

    with pytest.raises(ShapeError) as error:
        await harness.submit_opening()(
            round_id=round_id,
            seats=(SEAT,),
            web_queries=("a", "b", "c", "d"),
        )

    assert "3" in error.value.message
    assert harness.web_search.calls == []


async def test_a_tool_that_is_available_but_not_wired_is_a_wiring_error_not_a_silent_skip():
    """目录说工具可用、组装点却没接上实现——这种装配错误必须当场响，不能悄悄跳过。"""

    harness = Harness(replies={"claims": claims_reply(RBAC)})
    round_id = await opened_at_the_recap_card(harness)
    use_case = SubmitOpening(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        catalog=harness.seats,
        tools=harness.tools,
        available_tool_keys=frozenset({"tavily_api_key"}),
        web_search=None,
        evidence=harness.evidences,
        evidence_writer=harness.evidences,
        materials=harness.materials,
        uow=harness.uow,
        clock=harness.clock,
        events=harness.events,
    )

    with pytest.raises(ShapeError) as error:
        await use_case(round_id=round_id, seats=(SEAT,), web_queries=("RBAC 权限模型的主流做法",))

    assert "组装点" in error.value.message
