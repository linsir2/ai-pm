"""轮次记住"这一轮带进了哪些过目产物"（M8 调研种子 / M12 灵感箱）。

只记**引用**（origin + ref_id + item_id）——正文在探索/讨论表里，别在轮次 payload 里
放第二份（与 `MaterialRef` 同一条口径）。种子只是回链：主张的出处仍是 user_input。
"""

import pytest

from pmstudio.domain.round.round import Round, SeedRef
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.infrastructure.postgres.serialization import round_from_payload, round_to_payload
from tests.support.builders import NOW, PROJECT, ROUND_ID, make_round


def round_with_seeds() -> Round:
    round_ = Round.start(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="把导出的入口提到列表页（来自一条过目产物）",
        now=NOW,
        seeds=(
            SeedRef(origin="exploration", ref_id="x-1", item_id="H1"),
            SeedRef(origin="discussion", ref_id="d-1", item_id="C1"),
        ),
    )
    return round_


def _restore(payload: dict, round_: Round) -> Round:
    return round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )


def test_seeds_are_recorded_in_order():
    round_ = round_with_seeds()

    assert [(ref.origin, ref.ref_id, ref.item_id) for ref in round_.seeds] == [
        ("exploration", "x-1", "H1"),
        ("discussion", "d-1", "C1"),
    ]


def test_a_round_without_seeds_has_none():
    assert make_round().seeds == ()


def test_payload_roundtrip_keeps_seeds():
    round_ = round_with_seeds()

    restored = _restore(round_to_payload(round_), round_)

    assert [(ref.origin, ref.ref_id, ref.item_id) for ref in restored.seeds] == [
        ("exploration", "x-1", "H1"),
        ("discussion", "d-1", "C1"),
    ]


def test_old_rows_fall_back_to_exploration_seeds_and_missing_key_is_empty():
    """老行（M8 的两键形状）读回来就是探索种子；没有 seeds 键的再老的行 → 空。"""

    round_ = round_with_seeds()
    old = round_to_payload(round_)
    old["seeds"] = [{"exploration_id": "x-1", "candidate_id": "H1"}]
    restored_old = _restore(old, round_)
    assert [(ref.origin, ref.ref_id, ref.item_id) for ref in restored_old.seeds] == [
        ("exploration", "x-1", "H1")
    ]

    oldest = round_to_payload(round_)
    del oldest["seeds"]
    assert _restore(oldest, round_).seeds == ()


def test_seed_ref_needs_known_origin_and_both_ids():
    with pytest.raises(ShapeError):
        SeedRef(origin="gap", ref_id="x-1", item_id="H1")
    with pytest.raises(ShapeError):
        SeedRef(origin="exploration", ref_id="  ", item_id="H1")
    with pytest.raises(ShapeError):
        SeedRef(origin="exploration", ref_id="x-1", item_id="  ")
