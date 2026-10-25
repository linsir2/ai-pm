"""统一联网申请（P3a B1）：状态机、每方名额、归一化去重与旧数据迁移。

口径：
1. 申请只是提名，批准与执行是两步；状态只能沿着 proposed → approved → done/failed 走，
   或 proposed → rejected。
2. 每方（analyzer/seat/integrator）最多 3 条提案；去重键是 (origin, seat_id, 归一化 query)。
3. 旧 `web_queries`/`web_queries_chosen` 只在读侧迁移：停在复述卡上的算 approved，
   已经走过的算 done（不重跑）。
"""

import pytest

from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.round.web import WebRequest, WebRequestState
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.infrastructure.sqlite.serialization import round_from_payload, round_to_payload
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim

LEGACY_WHY = "旧版复述卡勾选，无逐条理由"


def opening_round() -> Round:
    """一轮：主张已落，正停在复述卡上。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出功能", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    return round_


def request(
    request_id: str,
    query: str,
    *,
    origin: str = "analyzer",
    why: str = "要查外部事实",
    seat_id: str | None = None,
    pass_index: int | None = None,
) -> WebRequest:
    return WebRequest(
        request_id=request_id,
        query=query,
        why=why,
        origin=origin,
        seat_id=seat_id,
        pass_index=pass_index,
    )


def three(origin: str, *, seat_id: str | None = None) -> list[WebRequest]:
    prefix = {"analyzer": "a", "seat": "s", "integrator": "i"}[origin]
    return [
        request(
            f"{prefix}{index}",
            f"{origin} 第 {index} 条",
            origin=origin,
            seat_id=seat_id if origin == "seat" else None,
            pass_index=index if origin == "seat" else None,
        )
        for index in range(1, 4)
    ]


def restored(round_: Round, *, step: Step | None = None) -> Round:
    return round_from_payload(
        round_to_payload(round_),
        round_id=round_.round_id,
        project_id=round_.project_id,
        created_at=round_.created_at,
        step=round_.step if step is None else step,
        waiting_on=round_.waiting_on,
    )


def legacy_payload(*, queries: list[tuple[str, str]], chosen: list[str]) -> dict:
    """一份"新键出现之前"的 payload：只有旧的两把检索词键。"""

    payload = round_to_payload(opening_round())
    payload.pop("web_requests")
    payload.pop("allow_web")
    payload.pop("web_selection", None)
    payload["web_queries"] = [{"query": query, "why": why} for query, why in queries]
    payload["web_queries_chosen"] = list(chosen)
    return payload


def load(payload: dict, *, step: Step) -> Round:
    return round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=NOW,
        step=step,
        waiting_on=(
            WaitingOn.OPENING if step is Step.CLAIMS_READY else WaitingOn.NONE
        ),
    )


# —— 形状 ——


def test_a_request_needs_an_id_a_query_a_reason_and_a_known_origin():
    with pytest.raises(ShapeError):
        WebRequest(request_id=" ", query="q", why="要查", origin="analyzer")
    with pytest.raises(ShapeError):
        WebRequest(request_id="w1", query=" ", why="要查", origin="analyzer")
    with pytest.raises(ShapeError):
        WebRequest(request_id="w1", query="q", why="   ", origin="analyzer")
    with pytest.raises(ShapeError):
        WebRequest(request_id="w1", query="q", why="要查", origin="model")


# —— 挂载：去重、名额、身份 ——


def test_attach_dedupes_by_origin_seat_and_normalized_query_keeping_order():
    round_ = opening_round()
    round_.attach_web_requests(
        [
            request("w1", "RBAC  权限模型"),
            request("w2", "rbac 权限模型", why="同一个问题换个写法"),
            request("w3", "RBAC 权限模型", origin="seat", seat_id=SEAT, pass_index=1),
            request("w4", "RBAC 权限模型", origin="integrator"),
        ]
    )

    assert [item.request_id for item in round_.web_requests] == ["w1", "w3", "w4"]
    assert round_.web_requests[0].why == "要查外部事实"


def test_attach_caps_each_origin_at_three_proposals():
    round_ = opening_round()
    round_.attach_web_requests(three("analyzer") + three("seat", seat_id=SEAT))
    round_.attach_web_requests(three("integrator"))
    assert len(round_.web_requests) == 9

    with pytest.raises(ShapeError):
        round_.attach_web_requests([request("a4", "第 4 条 analyzer")])
    assert len(round_.web_requests) == 9


def test_attach_rejects_a_duplicate_request_id():
    round_ = opening_round()
    with pytest.raises(ShapeError):
        round_.attach_web_requests(
            [request("w1", "第一条"), request("w1", "第二条", origin="integrator")]
        )
    assert round_.web_requests == ()

    round_.attach_web_requests([request("w1", "第一条")])
    with pytest.raises(ShapeError):
        round_.attach_web_requests([request("w1", "第二条", origin="seat", seat_id=SEAT)])
    assert len(round_.web_requests) == 1


def test_next_request_id_skips_used_numbers_and_malformed_ids():
    round_ = opening_round()
    assert round_.next_web_request_id() == "w1"

    round_.attach_web_requests(
        [
            request("w1", "一"),
            request("w3", "三", origin="seat", seat_id=SEAT),
            request("wX", "不是编号", origin="integrator"),
        ]
    )
    assert round_.next_web_request_id() == "w4"


def test_three_parties_can_attach_without_colliding_ids():
    round_ = opening_round()
    for origin, seat_id in (
        ("analyzer", None),
        ("seat", SEAT),
        ("integrator", None),
    ):
        request_id = round_.next_web_request_id()
        round_.attach_web_requests(
            [request(request_id, f"{origin} 的问题", origin=origin, seat_id=seat_id)]
        )

    assert [item.request_id for item in round_.web_requests] == ["w1", "w2", "w3"]


# —— 状态机 ——


def test_decisions_only_leave_proposed_and_results_only_leave_approved():
    round_ = opening_round()
    round_.attach_web_requests(
        [request("w1", "一"), request("w2", "二"), request("w3", "三")]
    )

    round_.decide_web_request("w1", approve=True)
    round_.decide_web_request("w2", approve=False)
    assert [item.state for item in round_.web_requests] == [
        WebRequestState.APPROVED,
        WebRequestState.REJECTED,
        WebRequestState.PROPOSED,
    ]

    with pytest.raises(ShapeError):
        round_.decide_web_request("w1", approve=True)
    with pytest.raises(ShapeError):
        round_.decide_web_request("w2", approve=False)
    with pytest.raises(ShapeError):
        round_.decide_web_request("w9", approve=True)

    round_.mark_web_request("w1", state=WebRequestState.DONE, result_note="3 条结果")
    assert round_.web_requests[0].state is WebRequestState.DONE
    assert round_.web_requests[0].result_note == "3 条结果"

    with pytest.raises(ShapeError):
        round_.mark_web_request("w2", state=WebRequestState.DONE)
    with pytest.raises(ShapeError):
        round_.mark_web_request("w3", state=WebRequestState.FAILED)
    with pytest.raises(ShapeError):
        round_.mark_web_request("w1", state=WebRequestState.FAILED)
    with pytest.raises(ShapeError):
        round_.mark_web_request("w1", state=WebRequestState.APPROVED)


# —— 旧 key 派生 ——


def test_web_queries_are_derived_from_analyzer_requests_only():
    round_ = opening_round()
    round_.attach_web_requests(
        [
            request("w1", "analyzer 提议"),
            request("w2", "seat 提议", origin="seat", seat_id=SEAT),
            request("w3", "analyzer 已批准"),
        ]
    )
    round_.decide_web_request("w3", approve=True)

    assert [(item.query, item.why) for item in round_.web_queries] == [
        ("analyzer 提议", "要查外部事实"),
        ("analyzer 已批准", "要查外部事实"),
    ]
    assert round_.web_queries_chosen == ("analyzer 已批准",)


def test_chosen_queries_are_the_authorized_or_finished_ones_not_proposed_or_rejected():
    round_ = opening_round()
    round_.attach_web_requests(
        [request("w1", "一"), request("w2", "二"), request("w3", "三")]
    )
    round_.decide_web_request("w2", approve=True)
    round_.decide_web_request("w3", approve=True)
    round_.mark_web_request("w3", state=WebRequestState.FAILED, result_note="超时")
    round_.decide_web_request("w1", approve=False)

    assert round_.web_queries_chosen == ("二", "三")


# —— opening：新字段 + 旧字符串兼容 ——


def test_submit_opening_approves_ids_and_legacy_queries_without_duplicating():
    round_ = opening_round()
    round_.attach_web_requests([request("w1", "一"), request("w2", "二")])

    round_.submit_opening(
        seats=(SEAT,),
        allow_web=True,
        approved_request_ids=("w1", "w1"),
        web_queries_chosen=("一", "只有旧字符串"),
    )

    assert round_.allow_web is True
    assert [(item.request_id, item.query, item.state) for item in round_.web_requests] == [
        ("w1", "一", WebRequestState.APPROVED),
        ("w2", "二", WebRequestState.PROPOSED),
        ("w3", "只有旧字符串", WebRequestState.APPROVED),
    ]
    assert round_.web_requests[2].origin == "analyzer"
    assert round_.web_requests[2].why == LEGACY_WHY
    assert round_.web_queries_chosen == ("一", "只有旧字符串")


def test_submit_opening_acceptable_already_approved_id_but_rejects_unknown_or_rejected():
    round_ = opening_round()
    round_.attach_web_requests([request("w1", "一"), request("w2", "二")])
    round_.decide_web_request("w1", approve=True)

    round_.submit_opening(seats=(SEAT,), approved_request_ids=("w1",))
    assert round_.web_requests[0].state is WebRequestState.APPROVED

    other = opening_round()
    other.attach_web_requests([request("w1", "一")])
    with pytest.raises(ShapeError):
        other.submit_opening(seats=(SEAT,), approved_request_ids=("w9",))

    rejected = opening_round()
    rejected.attach_web_requests([request("w1", "一")])
    rejected.decide_web_request("w1", approve=False)
    with pytest.raises(ShapeError):
        rejected.submit_opening(seats=(SEAT,), approved_request_ids=("w1",))


# —— web_selection（步级快照） ——


def test_web_selection_is_none_until_noted_and_can_be_cleared():
    round_ = opening_round()
    assert round_.web_selection is None

    round_.note_web_selection(
        (("m1", "m1#c00"), ("m2", "m2#c00"), ("m1", "m1#c00"))
    )
    assert round_.web_selection == (("m1", "m1#c00"), ("m2", "m2#c00"))

    round_.clear_web_selection()
    assert round_.web_selection is None


# —— 序列化：新轮只写新键 ——


def test_new_payload_writes_only_the_new_keys_and_round_trips_them():
    round_ = opening_round()
    round_.attach_web_requests(
        [
            request("w1", "一", why="要查外部事实"),
            request("w2", "二", origin="seat", seat_id=SEAT, pass_index=1),
        ]
    )
    round_.decide_web_request("w1", approve=True)
    round_.mark_web_request("w1", state=WebRequestState.DONE, result_note="2 条")
    round_.note_web_selection((("m1", "m1#c00"),))

    payload = round_to_payload(round_)
    assert "web_queries" not in payload
    assert "web_queries_chosen" not in payload
    assert payload["allow_web"] is False
    assert payload["web_selection"] == [["m1", "m1#c00"]]
    assert payload["web_requests"][0] == {
        "request_id": "w1",
        "query": "一",
        "why": "要查外部事实",
        "origin": "analyzer",
        "seat_id": None,
        "pass_index": None,
        "state": "done",
        "result_note": "2 条",
        "created_at": None,
    }

    again = restored(round_)
    assert [
        (item.request_id, item.query, item.state, item.result_note)
        for item in again.web_requests
    ] == [
        ("w1", "一", WebRequestState.DONE, "2 条"),
        ("w2", "二", WebRequestState.PROPOSED, None),
    ]
    assert again.web_selection == (("m1", "m1#c00"),)
    assert again.allow_web is False


def test_web_selection_is_omitted_when_none_and_missing_key_reads_back_as_none():
    round_ = opening_round()
    payload = round_to_payload(round_)
    assert "web_selection" not in payload
    assert restored(round_).web_selection is None

    payload.pop("web_selection", None)
    assert load(payload, step=Step.CLAIMS_READY).web_selection is None


# —— 旧 payload 迁移：按 step 判定，不重跑 ——


def test_legacy_claims_ready_migrates_chosen_to_approved():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二")],
        chosen=["二"],
    )

    round_ = load(payload, step=Step.CLAIMS_READY)

    assert [
        (item.request_id, item.query, item.why, item.origin, item.state)
        for item in round_.web_requests
    ] == [
        ("w1", "一", "为什么一", "analyzer", WebRequestState.PROPOSED),
        ("w2", "二", "为什么二", "analyzer", WebRequestState.APPROVED),
    ]
    assert round_.web_queries_chosen == ("二",)


def test_legacy_advanced_step_marks_chosen_done_so_it_is_not_rerun():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二")],
        chosen=["二"],
    )
    payload["materials"] = [{"material_id": "m1", "chunk_id": "m1#c00", "eid": "E9"}]

    round_ = load(payload, step=Step.SEATS_RUNNING)

    assert [item.state for item in round_.web_requests] == [
        WebRequestState.PROPOSED,
        WebRequestState.DONE,
    ]
    assert [ref.chunk_id for ref in round_.materials] == ["m1#c00"]
    assert round_.web_selection is None


def test_legacy_chosen_without_a_nomination_becomes_a_legacy_analyzer_request():
    payload = legacy_payload(
        queries=[("一", "为什么一")],
        chosen=["一", "旧版手写的一条"],
    )

    round_ = load(payload, step=Step.CLAIMS_READY)

    assert [(item.query, item.why, item.state) for item in round_.web_requests] == [
        ("一", "为什么一", WebRequestState.APPROVED),
        ("旧版手写的一条", LEGACY_WHY, WebRequestState.APPROVED),
    ]


def test_legacy_migration_caps_the_analyzer_origin_at_three_and_traces_the_rest():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二"), ("三", "为什么三")],
        chosen=["四", "五"],
    )

    round_ = load(payload, step=Step.SEATS_RUNNING)

    assert [item.query for item in round_.web_requests] == ["一", "二", "三"]
    assert [item.handle for item in round_.dropped] == ["联网申请 四", "联网申请 五"]
    assert all("每方最多 3 条" in item.reason for item in round_.dropped)
    assert {item.source.value for item in round_.dropped} == {"tool"}


def test_old_keys_are_ignored_when_the_new_key_is_present():
    round_ = opening_round()
    payload = round_to_payload(round_)
    payload["web_queries"] = [{"query": "旧的", "why": "不该被读"}]
    payload["web_queries_chosen"] = ["旧的"]

    loaded = load(payload, step=Step.CLAIMS_READY)

    assert loaded.web_requests == ()
    assert loaded.web_queries == ()
    assert loaded.web_queries_chosen == ()
