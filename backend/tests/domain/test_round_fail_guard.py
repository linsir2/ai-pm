"""终态守卫：fail 只对 ABANDONED 空操作、attest 同席同趟只一条、控制行幂等。"""

import pytest

from pmstudio.domain.round.opinion import SeatReview
from pmstudio.domain.round.skip import RoundSkip, user_skip_reason
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.errors import IllegalStepTransition
from tests.domain.round.test_round import SEAT, round_with_seats


def test_fail_is_a_noop_after_abandon() -> None:
    """放弃先落库：失败不能把 ABANDONED 改写成 FAILED，也不能编出 failure。"""

    round_ = round_with_seats()
    round_.abandon("用户不等了")

    round_.fail("迟到的失败")

    assert round_.step is Step.ABANDONED
    assert round_.failure is None
    assert round_.abandon_reason == "用户不等了"


def test_fail_on_an_already_failed_round_is_illegal() -> None:
    round_ = round_with_seats()
    round_.fail("第一次")

    with pytest.raises(IllegalStepTransition):
        round_.fail("第二次")


def test_attest_records_only_one_review_per_seat_and_pass() -> None:
    round_ = round_with_seats()
    review = SeatReview(seat_id=SEAT, basis="读了 E1", opinions=())

    round_.attest(review)
    round_.attest(review)

    assert len(round_.current_reviews) == 1


def test_apply_skip_controls_is_idempotent_and_keeps_dropped_clean() -> None:
    round_ = round_with_seats()
    record = RoundSkip(
        seat_id=SEAT,
        pass_index=1,
        reason=user_skip_reason(SEAT),
        source="user_skipped",
        requested_at_ms=1,
    )

    round_.apply_skip_controls((record,))
    round_.apply_skip_controls((record,))

    assert round_.skipped_seats == (SEAT,)
    assert round_.skip_records == (record,)
    assert round_.dropped == ()
    assert round_.pending_seats == ()
