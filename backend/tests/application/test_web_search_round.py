"""联网检索真的跑起来之后：信息包、块级出处、注入提示词、失败留痕（`P17b` §6）。

四条口径：
1. **没勾不联网**（记录替身 `calls == []`）；
2. 每条结果一个信息包 + 一条 `external` 出处，`external_ref` 就是 URL；
3. **编号 == 带进提示词的块**（选块与渲染共用同一把尺子，`§2.1`）；
4. 失败 / 没搜到 / 预算挤掉的那几条 → `Round.dropped(source=tool)`，**不判死整轮**。
"""

import json
import re

from pmstudio.application.errors import ToolCallFailed
from pmstudio.application.use_cases import external_material_text
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.round.round import DropSource
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.ids import RoundId
from tests.support.builders import PROJECT, SEAT, make_web_packet
from tests.support.doubles import RecordingWebSearch
from tests.support.harness import Harness

QUERY = "RBAC 权限模型的主流做法"


def claims_reply(*queries: dict) -> str:
    return json.dumps(
        {
            "basis": "读了空文档与本轮输入",
            "claims": [
                {
                    "text": "权限想按行业通行做法做",
                    "raw_span": "权限想按行业通行做法做",
                    "label": "need",
                    "zone": "body",
                    "section_id": "s5",
                }
            ],
            "seats": [{"seat_id": SEAT, "why": "你说到了权限"}],
            "web_queries": list(queries),
        },
        ensure_ascii=False,
    )


SEAT_REPLY = json.dumps({"basis": "读了检索结果", "opinions": []}, ensure_ascii=False)


def web_harness(*packets) -> Harness:  # type: ignore[no-untyped-def]
    return Harness(
        replies={
            "claims": claims_reply({"query": QUERY, "why": "要查行业通行做法"}),
        },
        seat_replies={SEAT: SEAT_REPLY},
        web_search=RecordingWebSearch(returns=packets),
    )


async def recap_round(harness: Harness) -> RoundId:
    """建轮 → 分析：停在复述卡上。"""

    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="权限想按行业通行做法做")
    await start.analyze(round_id=round_id)
    return round_id


async def test_no_query_selected_means_no_network_call_at_all():
    """作者定的口径：没勾 = 一次网络调用都没有。"""

    harness = web_harness(make_web_packet())
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    assert harness.web_search.calls == []
    assert harness.rounds.get(round_id).materials == ()
    assert harness.rounds.get(round_id).web_queries_chosen == ()


async def test_chosen_queries_become_material_packets_and_chunk_evidence():
    harness = web_harness(
        make_web_packet(url="https://a"),
        make_web_packet(url="https://b"),
        make_web_packet(url="https://c"),
    )
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    # 条数上限来自 `web_search.toml` 的 `max_results`，不是写死的
    assert harness.web_search.calls == [(QUERY, 5)]
    externals = [
        item
        for item in harness.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.EXTERNAL
    ]
    assert [item.source.external_ref for item in externals] == [
        "https://a",
        "https://b",
        "https://c",
    ]
    round_ = harness.rounds.get(round_id)
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00", "m2#c00", "m3#c00"]
    assert [str(ref.eid) for ref in round_.materials] == [str(item.eid) for item in externals]


async def test_a_failing_tool_leaves_a_trace_and_the_round_goes_on():
    harness = web_harness()
    harness.web_search.failure = ToolCallFailed(
        "联网搜索失败：HTTP 504", tool_id="tool.web_search", status=504
    )
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.SEATS_RUNNING          # 不判死
    assert round_.materials == ()                     # 也没编出材料
    tool_drops = [item for item in round_.dropped if item.source is DropSource.TOOL]
    assert len(tool_drops) == 1
    assert QUERY in tool_drops[0].handle
    assert "检索失败" in tool_drops[0].reason
    assert "504" in tool_drops[0].reason


async def test_a_query_that_returns_nothing_is_traced_too():
    """搜到了 0 条也是"办了但没结果"——用户该知道，不该静默。"""

    harness = web_harness()
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    round_ = harness.rounds.get(round_id)
    assert round_.materials == ()
    tool_drops = [item for item in round_.dropped if item.source is DropSource.TOOL]
    assert len(tool_drops) == 1
    assert "没有搜到" in tool_drops[0].reason


async def test_the_seat_prompt_carries_the_results_with_their_numbers():
    harness = web_harness(make_web_packet(url="https://a", title="权限模型综述"))
    round_id = await recap_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    await harness.run_seat_round()(round_id=round_id)

    slots = [item for item in harness.prompts.calls if item[0] == "seat_review"][-1][1]
    text = slots["external_material"]
    assert "联网检索结果" in text
    assert "https://a" in text
    assert "权限模型综述" in text
    assert "不是用户说的话" in text
    externals = [
        item
        for item in harness.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.EXTERNAL
    ]
    assert f"[{externals[0].eid}]" in text


async def test_numbered_chunks_are_exactly_the_rendered_ones():
    """不变量（§2.1）：`Round.materials` 里的编号集合 == 渲染文本里的编号集合。"""

    harness = web_harness(
        *[
            make_web_packet(url=f"https://example.com/{index}", text="字" * 220)
            for index in range(6)
        ]
    )
    round_id = await recap_round(harness)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    round_ = harness.rounds.get(round_id)
    assert round_.materials, "这个预算下应该带得进几条，否则这条不变量测了个空"
    rendered = external_material_text(round_, store=harness.materials)

    assert set(re.findall(r"\[(E\d+)\]", rendered)) == {
        str(ref.eid) for ref in round_.materials
    }
    assert "只带了前" not in rendered        # 选进来的每一条都被渲染出来了


async def test_results_that_do_not_fit_the_budget_are_traced_to_the_user():
    harness = web_harness(
        *[
            make_web_packet(url=f"https://example.com/{index}", text="字" * 300)
            for index in range(8)
        ]
    )
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    round_ = harness.rounds.get(round_id)
    assert 0 < len(round_.materials) < 8
    tool_drops = [item for item in round_.dropped if item.source is DropSource.TOOL]
    assert len(tool_drops) == 1
    assert "没带进去" in tool_drops[0].reason
    assert "材料表" in tool_drops[0].reason


async def test_the_same_url_twice_is_one_material():
    """两条查询返回同一个 URL：只入库一次、只发一个号（去重键是 URL）。"""

    harness = web_harness(make_web_packet(url="https://a"))
    round_id = await recap_round(harness)

    await harness.submit_opening()(
        round_id=round_id, seats=(SEAT,), web_queries=(QUERY, "RBAC 权限模型 怎么落地")
    )

    assert len(harness.web_search.calls) == 2
    round_ = harness.rounds.get(round_id)
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00"]
    externals = [
        item
        for item in harness.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.EXTERNAL
    ]
    assert len(externals) == 1
