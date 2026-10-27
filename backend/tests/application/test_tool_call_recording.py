"""工具留样的**写侧**行为（可观测性 v2）：成功也记、失败带分类、炸了也留痕、旁路不搞死轮。

用例全部走**真 `_search` 路径**（`SubmitOpening._search`）+ **真 SQLite 留样器**；
唯一的替身是联网检索（`RecordingWebSearch`，与既有 `test_web_search_round.py` 同款）
——真 `TavilySearchGateway` 会出网，本仓明令不许拿它跑测试。
"""

import json

import pytest

from pmstudio.domain.round.round import DropSource
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.ids import RoundId
from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.model import failure
from pmstudio.infrastructure.sqlite.tool_calls import SqliteToolCallRecorder
from tests.support.builders import PROJECT, SEAT, make_web_packet
from tests.support.doubles import RecordingWebSearch
from tests.support.harness import Harness
from tests.support.postgres import open_direct

QUERY = "RBAC 权限模型的主流做法"


def claims_reply() -> str:
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
            "web_queries": [{"query": QUERY, "why": "要查行业通行做法"}],
        },
        ensure_ascii=False,
    )


SEAT_REPLY = json.dumps({"basis": "读了检索结果", "opinions": []}, ensure_ascii=False)


def web_harness(*packets) -> Harness:  # type: ignore[no-untyped-def]
    return Harness(
        replies={"claims": claims_reply()},
        seat_replies={SEAT: SEAT_REPLY},
        web_search=RecordingWebSearch(returns=packets),
    )


async def recap_round(harness: Harness) -> RoundId:
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="权限想按行业通行做法做")
    await start.analyze(round_id=round_id)
    return round_id


def _rows(connection, round_id: RoundId):  # type: ignore[no-untyped-def]
    return connection.execute(
        "SELECT tool_id, arguments, result_note, duration_ms, ok, error, error_kind"
        " FROM tool_calls WHERE round_id = %s ORDER BY id",
        (str(round_id),),
    ).fetchall()


async def test_a_successful_search_is_recorded_with_its_duration():
    """I：成功的那一趟也记（只记失败答不出「这个工具慢不慢」），耗时是逐条量的。"""

    connection = open_direct()
    harness = web_harness(make_web_packet(url="https://a"))
    harness.tool_recorder = SqliteToolCallRecorder(connection)
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    rows = _rows(connection, round_id)
    assert len(rows) == 1
    assert rows[0]["tool_id"] == "web_search"
    # 两个参数都要可追溯（schema 注释写明了形状）：只记 query 答不出
    # 「这次只回 1 条是 limit=1 还是上游只给了 1 条」。
    recorded = rows[0]["arguments"]
    assert recorded["query"] == "RBAC 权限模型的主流做法"
    assert recorded["limit"] > 0
    assert rows[0]["result_note"] == "1 条"
    assert rows[0]["ok"] is True
    assert rows[0]["error"] is None
    assert rows[0]["error_kind"] is None
    assert rows[0]["duration_ms"] >= 0


async def test_a_failed_search_is_recorded_with_a_classified_error():
    """I：工具失败的留样带**分类**（复用 `failure.classify`，不新造分类器）。"""

    connection = open_direct()
    harness = web_harness()
    harness.tool_recorder = SqliteToolCallRecorder(connection)
    error = ToolCallFailed(
        "联网搜索失败：HTTP 504", tool_id="tool.web_search", status=504
    )
    harness.web_search.failure = error
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    rows = _rows(connection, round_id)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == error.message
    assert rows[0]["error_kind"] == failure.classify(error).value


async def test_a_non_tool_exception_is_recorded_before_it_raises():
    """I（评审 #2）：非工具异常**先记一笔再 raise**——这次调用确实炸了，不留痕就是盲区。"""

    connection = open_direct()
    harness = web_harness()
    harness.tool_recorder = SqliteToolCallRecorder(connection)
    harness.web_search.failure = ValueError("不是工具错")
    round_id = await recap_round(harness)

    with pytest.raises(ValueError):
        await harness.submit_opening()(
            round_id=round_id, seats=(SEAT,), web_queries=(QUERY,)
        )

    rows = _rows(connection, round_id)
    assert len(rows) == 1
    assert rows[0]["ok"] is False
    assert rows[0]["error"] == "不是工具错"


async def test_recording_never_breaks_the_round():
    """I：表被 DROP / 只读时这一轮照常（旁路语义不许变），且 `Round.dropped` 一字不变。"""

    connection = open_direct()
    harness = web_harness()
    harness.tool_recorder = SqliteToolCallRecorder(connection)
    harness.web_search.failure = ToolCallFailed(
        "联网搜索失败：HTTP 504", tool_id="tool.web_search", status=504
    )
    round_id = await recap_round(harness)

    # 连接断了：留样写不进去，但这一轮必须照常走完
    connection.close()
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.SEATS_RUNNING  # 没被判死
    # `Round.dropped` 的工具留痕**一字不变**（失败那条仍在）
    tool_drops = [item for item in round_.dropped if item.source is DropSource.TOOL]
    assert len(tool_drops) == 1
    assert QUERY in tool_drops[0].handle
    assert "504" in tool_drops[0].reason


async def test_no_recorder_means_no_writes():
    """R：留样关（recorder=None）时**零写入**——这是生产的默认状态。"""

    connection = open_direct()
    harness = web_harness(make_web_packet(url="https://a"))
    harness.tool_recorder = None  # 默认
    round_id = await recap_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), web_queries=(QUERY,))

    assert _rows(connection, round_id) == []
    assert (
        connection.execute("SELECT COUNT(*) AS n FROM tool_calls").fetchone()["n"] == 0
    )
