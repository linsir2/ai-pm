"""`GetRoundCalls`（可观测性 v2）：这一轮外部调用轨迹的**组装**（读侧用例）。

真合并 SQL 在 `tests/infrastructure/test_pg_simple_repositories.py`（那里是**真 PostgreSQL**）；
这里只验「拿到条目后怎么组装成 `RoundCallsView`」与「trace_id 是不是这一轮」。
"""

from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime

from pmstudio.application.dto import CallEntry, RoundCallsView
from tests.support.builders import PROJECT, SEAT
from tests.support.doubles import RecordingCallLog
from tests.support.harness import Harness

CLAIMS_REPLY = json.dumps(
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
    },
    ensure_ascii=False,
)


def _entry(
    kind: str,
    name: str,
    *,
    ok: bool = True,
    duration_ms: int = 1,
    error_kind: str | None = None,
    at: datetime = datetime(2026, 10, 23, 10, 0, tzinfo=UTC),
) -> CallEntry:
    return CallEntry(
        kind=kind,
        name=name,
        duration_ms=duration_ms,
        ok=ok,
        error_kind=error_kind,
        at=at,
    )


def test_the_round_calls_view_carries_both_sides():
    """守卫：两个 DTO 的字段集钉死——加字段必须是一次自觉的改动。"""

    assert {f.name for f in fields(RoundCallsView)} == {
        "round_id",
        "calls",
        "model_calls",
        "model_failed",
        "tool_calls",
        "tool_failed",
    }
    assert {f.name for f in fields(CallEntry)} == {
        "kind",
        "name",
        "duration_ms",
        "ok",
        "error_kind",
        "at",
    }


async def test_get_round_calls_counts_both_sides_from_the_entries():
    harness = Harness(replies={"claims": CLAIMS_REPLY}, seat_replies={})
    harness.call_log = RecordingCallLog(
        returns=(
            _entry("model", "claims"),
            _entry("model", "integration", ok=False),
            _entry("tool", "web_search"),
            _entry("tool", "web_search", ok=False, error_kind="timeout"),
        )
    )
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="权限想按行业通行做法做")
    await start.analyze(round_id=round_id)

    view = await harness.get_round_calls()(round_id=round_id)

    assert view.round_id == str(round_id)
    assert (view.model_calls, view.model_failed, view.tool_calls, view.tool_failed) == (
        2,
        1,
        2,
        1,
    )
    assert [entry.kind for entry in view.calls] == ["model", "model", "tool", "tool"]


async def test_get_round_calls_queries_by_the_round_id_as_the_trace():
    """留样的关联键是 `round_id`（存的是 trace id）——读侧按它取，不能按项目取。"""

    harness = Harness(replies={"claims": CLAIMS_REPLY}, seat_replies={})
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="权限想按行业通行做法做")
    await start.analyze(round_id=round_id)

    await harness.get_round_calls()(round_id=round_id)

    assert harness.call_log.calls == [str(round_id)]
