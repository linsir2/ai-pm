"""联网执行层（B3）：批准才联网、一条结果一个编号、跨步复用。

这里钉六件事：

1. opening 只执行**已批准**的申请，并在同一个 UoW 里记 done/failed；
2. 校验不过（未知申请 / 没工具 / 坏席位 / 坏 claim 编辑）时**一次网络调用都没有**，
   轮次还停在复述卡上；
3. `allow_web` 只自动批准 seat / integrator，且受每轮执行名额限制；
4. 同一条结果再次出现时只复用已覆盖块的编号，新块不发号（TOOL 留痕）；
5. 跨步累计不重复发号、web 预算仍共用一本账；
6. 真实 SQLite 上跑通 opening → seat → integrate 的多步执行。
"""

import json

import pytest

from pmstudio.application.dto import MaterialChunk, ParsedMaterial
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.round.round import DropSource
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.round.web import WebRequest, WebRequestState
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.harness.tools.registry import ToolDefinition
from tests.support.builders import PROJECT, SEAT, make_web_packet
from tests.support.doubles import RecordingWebSearch
from tests.support.harness import Harness

QUERY_A = "RBAC 权限模型的主流做法"
QUERY_B = "多租户隔离怎么做"
QUERY_C = "导出文件用什么编码"

SEAT_REPLY = json.dumps({"basis": "读了检索结果", "opinions": []}, ensure_ascii=False)

INTEGRATION_DRAFT = json.dumps(
    {
        "basis": "读了主张、材料与席位意见",
        "questions": [],
        "list_items": [],
        "proposals": [
            {
                "op": "add",
                "target": {"zone": "body", "section_id": "s1"},
                "text": "订单导出用于把订单批量导出成表格。",
                "origin_type": "derived",
                "evidence_refs": ["E1"],
                "rationale": "来自用户说的导出需求",
            }
        ],
        "dropped_opinions": [],
    },
    ensure_ascii=False,
)

MEMORY_REPLY = json.dumps({"changes": []}, ensure_ascii=False)


def claims_reply(*queries: dict) -> str:
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


def seat_reply(*requests: dict) -> str:
    payload = json.loads(SEAT_REPLY)
    payload["web_requests"] = list(requests)
    return json.dumps(payload, ensure_ascii=False)


def integration_reply(*requests: dict) -> str:
    payload = json.loads(INTEGRATION_DRAFT)
    payload["web_requests"] = list(requests)
    return json.dumps(payload, ensure_ascii=False)


async def analyzed_round(harness: Harness) -> object:
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    return round_id


def request_ids(harness: Harness, round_id) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    return tuple(item.request_id for item in harness.rounds.get(round_id).web_requests)


def only_request(harness: Harness, round_id) -> WebRequest:  # type: ignore[no-untyped-def]
    (item,) = harness.rounds.get(round_id).web_requests
    return item


def external_evidences(harness: Harness):
    return [
        item
        for item in harness.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.EXTERNAL
    ]


async def test_opening_executes_only_approved_requests_and_marks_them_done():
    harness = Harness(
        replies={"claims": claims_reply({"query": QUERY_A, "why": "要查行业做法"})},
        web_search=RecordingWebSearch(
            returns=(make_web_packet(url="https://a"), make_web_packet(url="https://b"))
        ),
    )
    round_id = await analyzed_round(harness)
    (request_id,) = request_ids(harness, round_id)

    await harness.submit_opening()(
        round_id=round_id, seats=(SEAT,), approved_request_ids=(request_id,)
    )

    assert harness.web_search.calls == [(QUERY_A, 5)]
    round_ = harness.rounds.get(round_id)
    request = only_request(harness, round_id)
    assert request.state is WebRequestState.DONE
    assert request.result_note == "搜到 2 条结果"
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00", "m2#c00"]
    assert len(external_evidences(harness)) == 2
    assert round_.web_selection == (("m1", "m1#c00"), ("m2", "m2#c00"))
    assert round_.step is Step.SEATS_RUNNING


async def test_an_unapproved_request_is_not_executed():
    harness = Harness(
        replies={"claims": claims_reply({"query": QUERY_A, "why": "要查行业做法"})},
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))

    assert harness.web_search.calls == []
    request = only_request(harness, round_id)
    assert request.state is WebRequestState.PROPOSED
    assert harness.rounds.get(round_id).materials == ()
    assert harness.rounds.get(round_id).web_selection is None


async def test_an_unknown_approved_id_fails_before_any_network_call():
    harness = Harness(
        replies={"claims": claims_reply({"query": QUERY_A, "why": "要查行业做法"})},
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)

    with pytest.raises(ShapeError):
        await harness.submit_opening()(
            round_id=round_id, seats=(SEAT,), approved_request_ids=("w999",)
        )

    assert harness.web_search.calls == []
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.CLAIMS_READY
    assert round_.waiting_on is WaitingOn.OPENING
    assert only_request(harness, round_id).state is WebRequestState.PROPOSED


async def test_an_unknown_seat_fails_before_any_network_call():
    harness = Harness(
        replies={"claims": claims_reply({"query": QUERY_A, "why": "要查行业做法"})},
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)
    (request_id,) = request_ids(harness, round_id)

    with pytest.raises(ShapeError):
        await harness.submit_opening()(
            round_id=round_id,
            seats=("seat.not_in_catalog",),
            approved_request_ids=(request_id,),
        )

    assert harness.web_search.calls == []
    assert harness.rounds.get(round_id).step is Step.CLAIMS_READY


async def test_allow_web_without_a_tool_is_refused_before_any_network_call():
    harness = Harness(
        replies={"claims": claims_reply({"query": QUERY_A, "why": "要查行业做法"})},
        available_tool_keys=frozenset(),
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)

    with pytest.raises(ShapeError) as error:
        await harness.submit_opening()(round_id=round_id, seats=(SEAT,), allow_web=True)

    assert "联网工具" in error.value.message
    assert harness.web_search.calls == []
    assert harness.rounds.get(round_id).step is Step.CLAIMS_READY


async def test_allow_web_auto_approves_and_executes_seat_requests_before_integration():
    harness = Harness(
        replies={"claims": claims_reply(), "integration": INTEGRATION_DRAFT},
        seat_replies={
            SEAT: seat_reply({"query": QUERY_B, "why": "席位要查多租户隔离"})
        },
        web_search=RecordingWebSearch(returns=(make_web_packet(url="https://tenant"),)),
    )
    round_id = await analyzed_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), allow_web=True)
    await harness.run_seat_round()(round_id=round_id)

    assert harness.web_search.calls == []
    assert only_request(harness, round_id).state is WebRequestState.PROPOSED

    await harness.integrate_round()(round_id=round_id)

    assert harness.web_search.calls == [(QUERY_B, 5)]
    request = only_request(harness, round_id)
    assert request.state is WebRequestState.DONE
    assert harness.rounds.get(round_id).web_selection == (("m1", "m1#c00"),)
    integration_slots = [
        item[1] for item in harness.prompts.calls if item[0] == "integrator"
    ][-1]
    assert "https://tenant" in integration_slots["external_material"]


async def test_allow_web_false_leaves_seat_requests_unexecuted():
    harness = Harness(
        replies={"claims": claims_reply(), "integration": INTEGRATION_DRAFT},
        seat_replies={
            SEAT: seat_reply({"query": QUERY_B, "why": "席位要查多租户隔离"})
        },
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    assert harness.web_search.calls == []
    assert only_request(harness, round_id).state is WebRequestState.PROPOSED


async def test_an_integrator_request_is_not_executed_in_the_same_integration():
    harness = Harness(
        replies={
            "claims": claims_reply(),
            "integration": integration_reply(
                {"query": QUERY_C, "why": "整合者要查导出编码"}
            ),
        },
        seat_replies={SEAT: SEAT_REPLY},
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), allow_web=True)
    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    assert harness.web_search.calls == []
    request = only_request(harness, round_id)
    assert request.origin == "integrator"
    assert request.state is WebRequestState.PROPOSED

    # 下一步（这里直接调执行点）才轮到它。
    await harness.web_research().execute_approved(round_id=round_id)

    assert harness.web_search.calls == [(QUERY_C, 5)]
    assert only_request(harness, round_id).state is WebRequestState.DONE


async def test_allow_web_stops_at_three_execution_slots_across_origins():
    harness = Harness(
        replies={
            "claims": claims_reply(
                {"query": QUERY_A, "why": "分析者要查行业做法"},
                {"query": "第二个分析者申请", "why": "分析者要查第二件事"},
            ),
            "integration": INTEGRATION_DRAFT,
        },
        seat_replies={
            SEAT: seat_reply(
                {"query": QUERY_B, "why": "席位要查多租户隔离"},
                {"query": QUERY_C, "why": "席位要查导出编码"},
            )
        },
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    round_id = await analyzed_round(harness)
    analyzer_ids = request_ids(harness, round_id)

    await harness.submit_opening()(
        round_id=round_id, seats=(SEAT,), allow_web=True, approved_request_ids=analyzer_ids
    )
    assert len(harness.web_search.calls) == 2

    await harness.run_seat_round()(round_id=round_id)
    await harness.integrate_round()(round_id=round_id)

    # 两个 analyzer 已用掉两个名额：席位申请只能再自动批准一条。
    assert len(harness.web_search.calls) == 3
    states = {item.query: item.state for item in harness.rounds.get(round_id).web_requests}
    assert states[QUERY_B] is WebRequestState.DONE
    assert states[QUERY_C] is WebRequestState.PROPOSED


def long_web_packet() -> ParsedMaterial:
    """一条结果切成三块，每块都大到第一趟只装得下第一块。"""

    chunks = tuple(
        MaterialChunk(
            chunk_id=f"tavily#c{index:02d}",
            ordinal=index,
            text="字" * 1200 + f"（第 {index} 段）",
            heading_path=(),
            anchor=f"网页正文 · 第 {index + 1} 段",
        )
        for index in range(3)
    )
    return ParsedMaterial(
        origin="web_search",
        title="长结果",
        source_ref="https://long",
        media_type="text/html",
        bytes_len=5000,
        chunks=chunks,
        parser_id="tavily",
        parser_version="v1",
    )


async def test_uncovered_chunks_of_an_existing_result_never_get_a_new_number():
    harness = Harness(
        replies={"claims": claims_reply({"query": QUERY_A, "why": "要查行业做法"})},
        web_search=RecordingWebSearch(
            returns_by_query={
                QUERY_A: (long_web_packet(),),
                QUERY_B: (long_web_packet(),),
            }
        ),
    )
    round_id = await analyzed_round(harness)
    (first_id,) = request_ids(harness, round_id)
    await harness.submit_opening()(
        round_id=round_id, seats=(SEAT,), approved_request_ids=(first_id,)
    )

    before = harness.rounds.get(round_id)
    assert [ref.chunk_id for ref in before.materials] == ["m1#c00"]
    (eid,) = {str(ref.eid) for ref in before.materials}
    assert len(external_evidences(harness)) == 1

    # 第二步：同一个 URL 再次被搜到（同一份材料，后两块还没发过号）。
    round_ = harness.rounds.get(round_id)
    round_.attach_web_requests(
        (
            WebRequest(
                request_id=round_.next_web_request_id(),
                query=QUERY_B,
                why="第二步要再查一次同一个来源",
                origin="seat",
                seat_id=SEAT,
                pass_index=1,
                state=WebRequestState.APPROVED,
            ),
        )
    )
    harness.rounds.save(round_)

    await harness.web_research().execute_approved(round_id=round_id)

    after = harness.rounds.get(round_id)
    assert [ref.chunk_id for ref in after.materials] == ["m1#c00"]
    assert {str(ref.eid) for ref in after.materials} == {eid}
    assert after.web_selection == (("m1", "m1#c00"),)
    assert len(external_evidences(harness)) == 1
    drops = [item for item in after.dropped if item.source is DropSource.TOOL]
    assert any("新出现" in item.reason or "新块" in item.reason for item in drops), drops


async def test_web_budget_is_shared_across_steps():
    from pmstudio.application.context import WEB_BUDGET
    from pmstudio.application.use_cases import external_material_text

    many = tuple(
        make_web_packet(url=f"https://step/{index}", text="字" * 700)
        for index in range(4)
    )
    harness = Harness(
        replies={
            "claims": claims_reply(
                {"query": QUERY_A, "why": "第一步"},
                {"query": QUERY_B, "why": "第二步"},
            )
        },
        web_search=RecordingWebSearch(returns=many),
    )
    round_id = await analyzed_round(harness)

    await harness.submit_opening()(round_id=round_id, seats=(SEAT,), approved_request_ids=("w1",))
    await harness.web_research().execute_approved(round_id=round_id)
    # 第二步手动批准第二条 analyzer 申请（模拟逐条批准）。
    round_ = harness.rounds.get(round_id)
    round_.decide_web_request("w2", approve=True)
    harness.rounds.save(round_)
    await harness.web_research().execute_approved(round_id=round_id)

    round_ = harness.rounds.get(round_id)
    rendered = external_material_text(round_, store=harness.materials)
    assert len(rendered) <= WEB_BUDGET
    assert len({str(ref.eid) for ref in round_.materials}) == len(
        {ref.material_id for ref in round_.materials}
    ), "一条结果只能有一个编号"


async def test_keep_is_still_reachable_from_submit_opening():
    """兼容面：老测试直接调 `SubmitOpening._keep`。"""

    harness = Harness(replies={})
    harness.seed_project()
    packet = harness.submit_opening()._keep(
        PROJECT, make_web_packet(url="https://keep", text="keep 正文")
    )

    assert packet.material_id == "m1"
    assert harness.materials.get(packet.material_id).parsed.source_ref == "https://keep"


async def test_the_effective_execution_limit_is_the_smaller_of_tool_and_round_caps():
    """工具只声明 1 条时，即使分析者提了 2 条、用户批准了 2 条，也不能执行 2 条。"""

    class _OneAtATime:
        def all(self) -> tuple[ToolDefinition, ...]:
            return (
                ToolDefinition(
                    tool_id="tool.web_search",
                    title="联网搜索",
                    kind="web_search",
                    requires=("tavily_api_key",),
                    max_queries=1,
                    max_results=5,
                    network_access=True,
                ),
            )

        def available(self, *, has):  # type: ignore[no-untyped-def]
            return self.all()

    harness = Harness(
        replies={
            "claims": claims_reply(
                {"query": QUERY_A, "why": "第一条"},
                {"query": QUERY_B, "why": "第二条"},
            )
        },
        web_search=RecordingWebSearch(returns=(make_web_packet(),)),
    )
    harness.tools = _OneAtATime()
    round_id = await analyzed_round(harness)
    ids = request_ids(harness, round_id)

    with pytest.raises(ShapeError) as error:
        await harness.submit_opening()(
            round_id=round_id, seats=(SEAT,), approved_request_ids=ids
        )

    assert "最多执行 1 条" in error.value.message
    assert harness.web_search.calls == []
