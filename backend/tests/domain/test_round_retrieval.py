"""检索 trace 与手动带料的值对象（P0 Task 0.1）。

只测**不变量**：去重、状态一致、manual 的身份。检索行为在应用层测。

## 为什么 `selected` 不要求是 `hits` 的子集

同一趟投影可能选中：
- 本趟检索命中的块；
- 上一趟已经投影、留在池里的块；
- 用户手动带进的块（`manual_chunks`，不在本趟 `hits` 里）。

要求子集会把后两类误判成非法——那是把"池"错当成"本趟命中"。
"""

import pytest

from pmstudio.domain.round.retrieval import (
    ManualChunk,
    RetrievalHit,
    RetrievalStage,
    RetrievalTrace,
)
from pmstudio.domain.shared.errors import ShapeError
from tests.support.builders import NOW


def hit(material_id: str, chunk_id: str, score: float = 1.0) -> RetrievalHit:
    return RetrievalHit(material_id=material_id, chunk_id=chunk_id, score=score)


def trace(**overrides) -> RetrievalTrace:
    base = dict(
        stage=RetrievalStage.SEAT,
        pass_index=1,
        query="导出权限",
        hits=(),
        selected=(),
        ok=True,
        error=None,
        created_at=NOW,
    )
    base.update(overrides)
    return RetrievalTrace(**base)


# —— R：形状 ——


def test_a_trace_rejects_duplicate_hits() -> None:
    with pytest.raises(ShapeError):
        trace(hits=(hit("m1", "m1#c00"), hit("m1", "m1#c00")))


def test_a_trace_rejects_duplicate_selected() -> None:
    with pytest.raises(ShapeError):
        trace(selected=(hit("m1", "m1#c03"), hit("m1", "m1#c03")))


def test_selected_may_include_chunks_outside_this_turns_hits() -> None:
    """manual 块、上一趟已投影的块都可能被本步选中。"""

    item = trace(hits=(), selected=(hit("m1", "m1#c03"),))

    assert item.selected[0].chunk_id == "m1#c03"


def test_a_zero_hit_trace_is_legal() -> None:
    item = trace(hits=(), selected=())

    assert item.ok is True
    assert item.error is None


def test_an_empty_query_is_legal() -> None:
    """INTEGRATE 在没有缺口时会构造空 query——空不等于错。"""

    assert trace(query="").query == ""


def test_a_negative_pass_index_is_rejected() -> None:
    with pytest.raises(ShapeError):
        trace(pass_index=-1)


def test_an_unknown_stage_is_rejected() -> None:
    with pytest.raises(ShapeError):
        trace(stage="seat")  # type: ignore[arg-type]


def test_an_empty_error_is_rejected() -> None:
    with pytest.raises(ShapeError):
        trace(ok=False, error="   ")


def test_ok_and_error_must_agree() -> None:
    with pytest.raises(ShapeError):
        trace(ok=True, error="检索失败")
    with pytest.raises(ShapeError):
        trace(ok=False, error=None)


def test_a_failed_trace_is_legal() -> None:
    item = trace(ok=False, error="检索器超时")

    assert item.ok is False
    assert item.error == "检索器超时"


# —— ManualChunk ——


def test_a_manual_chunk_keeps_material_identity() -> None:
    item = ManualChunk(material_id="m1", chunk_id="m1#c03")

    assert (item.material_id, item.chunk_id) == ("m1", "m1#c03")


def test_a_manual_chunk_rejects_an_empty_material_id() -> None:
    with pytest.raises(ShapeError):
        ManualChunk(material_id="  ", chunk_id="m1#c03")


# —— RetrievalHit 的 score 哨兵 ——


def test_a_non_hit_chunk_uses_zero_as_its_score() -> None:
    """手动/池内块没有检索分；0.0 是显式哨兵，不让实现随手编一个。"""

    assert RetrievalHit(material_id="m1", chunk_id="m1#c03").score == 0.0


# —— Round 的三个字段（P0 Task 0.2）——


def _roundtrip(round_):
    from pmstudio.infrastructure.postgres.serialization import (
        round_from_payload,
        round_to_payload,
    )
    from tests.support.builders import PROJECT, ROUND_ID

    return round_from_payload(
        round_to_payload(round_),
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )


def test_pin_materials_dedupes_and_preserves_order() -> None:
    from tests.support.builders import make_round

    item = make_round()
    item.pin_materials(["m2", "m1", "m2"])

    assert item.pinned_materials == ("m2", "m1")


def test_note_manual_chunks_dedupes_by_pair() -> None:
    from tests.support.builders import make_round

    item = make_round()
    item.note_manual_chunks(
        [
            ManualChunk("m1", "m1#c03"),
            ManualChunk("m1", "m1#c03"),
            ManualChunk("m2", "m2#c00"),
        ]
    )

    assert [(x.material_id, x.chunk_id) for x in item.manual_chunks] == [
        ("m1", "m1#c03"),
        ("m2", "m2#c00"),
    ]


def test_note_retrieval_is_append_only() -> None:
    from tests.support.builders import make_round

    item = make_round()
    item.note_retrieval(trace(stage=RetrievalStage.SEAT, pass_index=1))
    item.note_retrieval(trace(stage=RetrievalStage.INTEGRATE, pass_index=1))

    assert [one.stage for one in item.retrievals] == [
        RetrievalStage.SEAT,
        RetrievalStage.INTEGRATE,
    ]


def test_has_retrieval_matches_stage_and_pass() -> None:
    from tests.support.builders import make_round

    item = make_round()
    item.note_retrieval(trace(stage=RetrievalStage.SEAT, pass_index=1))

    assert item.has_retrieval(RetrievalStage.SEAT, 1) is True
    assert item.has_retrieval(RetrievalStage.SEAT, 2) is False
    assert item.has_retrieval(RetrievalStage.INTEGRATE, 1) is False


def test_new_fields_round_trip() -> None:
    from tests.support.builders import make_round

    item = make_round()
    item.pin_materials(["m1"])
    item.note_manual_chunks([ManualChunk("m1", "m1#c03")])
    item.note_retrieval(
        trace(
            stage=RetrievalStage.SEAT,
            pass_index=1,
            hits=(hit("m1", "m1#c00"),),
            selected=(hit("m1", "m1#c03"),),
        )
    )

    again = _roundtrip(item)

    assert again.pinned_materials == ("m1",)
    assert [(x.material_id, x.chunk_id) for x in again.manual_chunks] == [
        ("m1", "m1#c03")
    ]
    assert again.retrievals[0].stage is RetrievalStage.SEAT
    assert again.retrievals[0].selected[0].chunk_id == "m1#c03"
    assert again.retrievals[0].created_at == NOW


def test_an_old_payload_without_new_keys_still_loads() -> None:
    from pmstudio.infrastructure.postgres.serialization import (
        round_from_payload,
        round_to_payload,
    )
    from tests.support.builders import PROJECT, ROUND_ID, make_round

    item = make_round()
    payload = round_to_payload(item)
    for key in ("retrievals", "pinned_materials", "manual_chunks"):
        del payload[key]

    again = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=item.created_at,
        step=item.step,
        waiting_on=item.waiting_on,
    )

    assert again.retrievals == ()
    assert again.pinned_materials == ()
    assert again.manual_chunks == ()
