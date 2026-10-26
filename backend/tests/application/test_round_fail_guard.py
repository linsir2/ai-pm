"""`fail_round` 的终态守卫：放弃先落库时整段不写、不发事件。"""

from pmstudio.application.use_cases import fail_round
from pmstudio.domain.round.step import Step
from tests.support.builders import PROJECT
from tests.support.harness import Harness


async def test_fail_round_is_a_noop_after_abandon() -> None:
    harness = Harness()
    harness.seed_project()
    round_id = await harness.start_round().create(
        project_id=PROJECT, user_input="想做个订单导出的功能"
    )
    round_ = harness.rounds.get(round_id)
    round_.abandon("用户不等了")
    harness.rounds.save(round_)
    saves = harness.rounds.saves
    events = len(harness.events.names)

    fail_round(
        rounds=harness.rounds,
        round_writer=harness.rounds,
        uow=harness.uow,
        events=harness.events,
        clock=harness.clock,
        round_id=round_id,
        reason="迟到的失败",
    )

    stored = harness.rounds.get(round_id)
    assert stored.step is Step.ABANDONED
    assert stored.failure is None
    assert harness.rounds.saves == saves
    assert len(harness.events.names) == events
