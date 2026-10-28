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
from pmstudio.infrastructure.postgres.serialization import round_from_payload, round_to_payload
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


def snapshot(round_: Round) -> tuple:
    return (
        round_.step,
        round_.waiting_on,
        round_.seats,
        round_.allow_web,
        round_.web_requests,
    )


def test_submit_opening_acceptable_already_approved_id_but_rejects_unknown_or_rejected():
    round_ = opening_round()
    round_.attach_web_requests([request("w1", "一"), request("w2", "二")])
    round_.decide_web_request("w1", approve=True)

    round_.submit_opening(seats=(SEAT,), approved_request_ids=("w1",))
    assert round_.web_requests[0].state is WebRequestState.APPROVED

    other = opening_round()
    other.attach_web_requests([request("w1", "一")])
    before = snapshot(other)
    with pytest.raises(ShapeError):
        other.submit_opening(seats=(SEAT,), approved_request_ids=("w9",))
    assert snapshot(other) == before

    rejected = opening_round()
    rejected.attach_web_requests([request("w1", "一")])
    rejected.decide_web_request("w1", approve=False)
    before = snapshot(rejected)
    with pytest.raises(ShapeError):
        rejected.submit_opening(seats=(SEAT,), approved_request_ids=("w1",))
    assert snapshot(rejected) == before


def test_submit_opening_treats_done_and_failed_ids_as_already_approved():
    """逐条批准并执行过的 id 再来开轮是 no-op——与旧 `web_queries` 通道一致。

    B5 的本地勾选状态可能还没清：`done` / `failed` 不是"没批过"，不能 422。
    """

    round_ = opening_round()
    round_.attach_web_requests([request("w1", "一"), request("w2", "二")])
    round_.decide_web_request("w1", approve=True)
    round_.mark_web_request("w1", state=WebRequestState.DONE, result_note="3 条结果")
    round_.decide_web_request("w2", approve=True)
    round_.mark_web_request("w2", state=WebRequestState.FAILED, result_note="超时")

    round_.submit_opening(seats=(SEAT,), approved_request_ids=("w1", "w2"))

    assert [(item.request_id, item.state) for item in round_.web_requests] == [
        ("w1", WebRequestState.DONE),
        ("w2", WebRequestState.FAILED),
    ]


def test_submit_opening_rejects_a_free_query_when_analyzer_is_full_without_mutating():
    round_ = opening_round()
    round_.attach_web_requests(three("analyzer"))
    before = snapshot(round_)

    with pytest.raises(ShapeError) as error:
        round_.submit_opening(
            seats=(SEAT,),
            allow_web=True,
            web_queries_chosen=("自由勾选的一条",),
        )

    assert "3" in error.value.message
    assert snapshot(round_) == before


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


def test_empty_web_selection_is_persisted_as_empty_not_missing():
    round_ = opening_round()
    round_.note_web_selection(())

    payload = round_to_payload(round_)
    assert payload["web_selection"] == []
    assert restored(round_).web_selection == ()

    # `[]`（这一趟一块都没选）与缺键（旧 payload，回退全部旧 web refs）不是一回事
    without = dict(payload)
    without.pop("web_selection")
    assert load(without, step=Step.CLAIMS_READY).web_selection is None


def test_clear_web_selection_removes_the_key_and_reads_back_none():
    round_ = opening_round()
    round_.note_web_selection((("m1", "m1#c00"),))
    round_.clear_web_selection()

    payload = round_to_payload(round_)
    assert "web_selection" not in payload
    assert restored(round_).web_selection is None


# —— 旧 payload 迁移：按 step 判定，不重跑 ——


def test_legacy_claims_ready_migrates_chosen_to_approved():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二")],
        chosen=["二"],
    )

    round_ = load(payload, step=Step.CLAIMS_READY)

    # 迁移先把用户勾过的排在前面，再填没勾的提案。
    assert [
        (item.request_id, item.query, item.why, item.origin, item.state)
        for item in round_.web_requests
    ] == [
        ("w1", "二", "为什么二", "analyzer", WebRequestState.APPROVED),
        ("w2", "一", "为什么一", "analyzer", WebRequestState.PROPOSED),
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
        WebRequestState.DONE,
        WebRequestState.PROPOSED,
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

    # 用户勾的两条先保住；剩余名额给第一条未勾选提案，后两条提案 TOOL 留痕。
    assert [item.query for item in round_.web_requests] == ["四", "五", "一"]
    assert [item.handle for item in round_.dropped] == ["联网申请 二", "联网申请 三"]
    assert all("未勾选提案" in item.reason for item in round_.dropped)
    assert {item.source.value for item in round_.dropped} == {"tool"}


def test_legacy_cap_keeps_user_choices_before_unselected_proposals():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二"), ("三", "为什么三")],
        chosen=["三", "未提名的一条"],
    )

    round_ = load(payload, step=Step.CLAIMS_READY)

    assert [(item.query, item.why, item.state) for item in round_.web_requests] == [
        ("三", "为什么三", WebRequestState.APPROVED),
        ("未提名的一条", LEGACY_WHY, WebRequestState.APPROVED),
        ("一", "为什么一", WebRequestState.PROPOSED),
    ]
    assert [item.handle for item in round_.dropped] == ["联网申请 二"]
    assert "未勾选提案" in round_.dropped[0].reason


def test_legacy_drop_texts_distinguish_unselected_proposals_from_unnominated_choices():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二"), ("三", "为什么三")],
        chosen=["四", "五", "六", "七"],
    )

    round_ = load(payload, step=Step.SEATS_RUNNING)

    assert [item.query for item in round_.web_requests] == ["四", "五", "六"]
    reasons = {item.handle: item.reason for item in round_.dropped}
    assert "未提名勾选" in reasons["联网申请 七"]
    assert all(
        "未勾选提案" in reasons[f"联网申请 {query}"] for query in ("一", "二", "三")
    )


def test_legacy_nominated_choice_over_the_cap_keeps_order_and_uses_the_third_reason():
    payload = legacy_payload(
        queries=[("一", "为什么一"), ("二", "为什么二"), ("三", "为什么三")],
        chosen=["四", "五", "六", "一"],
    )

    round_ = load(payload, step=Step.CLAIMS_READY)

    # 截断只留前三条勾选；第四条勾选是旧版已提名提案，走第三段文案。
    assert [item.query for item in round_.web_requests] == ["四", "五", "六"]
    assert [item.handle for item in round_.dropped] == [
        "联网申请 一",
        "联网申请 二",
        "联网申请 三",
    ]
    assert "旧版勾选（已提名）没进本轮" in round_.dropped[0].reason
    assert all("未勾选提案" in item.reason for item in round_.dropped[1:])
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


def test_cancel_approved_web_request_only_moves_approved_to_rejected_with_note():
    round_ = opening_round()
    round_.attach_web_requests(
        [request("w1", "第一条"), request("w2", "第二条"), request("w3", "第三条")]
    )
    round_.decide_web_request("w1", approve=True)
    round_.decide_web_request("w2", approve=False)

    round_.cancel_approved_web_request("w1", note="超额恢复：用户放弃执行")

    items = {item.request_id: item for item in round_.web_requests}
    assert items["w1"].state is WebRequestState.REJECTED
    assert items["w1"].result_note == "超额恢复：用户放弃执行"
    # 五态没有扩：拒绝仍是不占名额的 rejected，不是 failed。
    assert items["w2"].state is WebRequestState.REJECTED
    assert items["w3"].state is WebRequestState.PROPOSED

    for request_id in ("w1", "w2", "w3"):
        with pytest.raises(ShapeError):
            round_.cancel_approved_web_request(request_id, note="再放弃一次")
