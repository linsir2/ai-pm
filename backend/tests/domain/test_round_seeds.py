"""轮次记住"这一轮带进了哪些调研产物"（M8 种子）。

只记**引用**（exploration_id + candidate_id）——候选正文在探索表里，别在轮次 payload 里
放第二份（与 `MaterialRef` 同一条口径）。种子只是回链：主张的出处仍是 user_input。
"""

import pytest

from pmstudio.domain.round.round import Round, SeedRef
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.infrastructure.sqlite.serialization import round_from_payload, round_to_payload
from tests.support.builders import NOW, PROJECT, ROUND_ID, make_round


def round_with_seeds() -> Round:
    round_ = Round.start(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="把导出的入口提到列表页（来自一条调研产物）",
        now=NOW,
        seeds=(
            SeedRef(exploration_id="x-1", candidate_id="H1"),
            SeedRef(exploration_id="x-1", candidate_id="B2"),
        ),
    )
    return round_


def test_seeds_are_recorded_in_order():
    round_ = round_with_seeds()

    assert [(ref.exploration_id, ref.candidate_id) for ref in round_.seeds] == [
        ("x-1", "H1"),
        ("x-1", "B2"),
    ]


def test_a_round_without_seeds_has_none():
    assert make_round().seeds == ()


def test_payload_roundtrip_keeps_seeds_and_old_rows_fall_back():
    round_ = round_with_seeds()

    restored = round_from_payload(
        round_to_payload(round_),
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert [ref.candidate_id for ref in restored.seeds] == ["H1", "B2"]

    old = round_to_payload(round_)
    del old["seeds"]  # 老行：没有这把键
    restored_old = round_from_payload(
        old,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert restored_old.seeds == ()


def test_seed_ref_needs_both_ids():
    with pytest.raises(ShapeError):
        SeedRef(exploration_id=" ", candidate_id="H1")
    with pytest.raises(ShapeError):
        SeedRef(exploration_id="x-1", candidate_id=" ")
