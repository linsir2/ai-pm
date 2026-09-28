"""步骤与等待。谁在推进、在等谁，只能由这里的表说话。"""

from enum import StrEnum


class Step(StrEnum):
    STARTED = "started"
    CLAIMS_READY = "claims_ready"
    SEATS_RUNNING = "seats_running"
    AWAITING_ANSWERS = "awaiting_answers"
    AWAITING_CONFIRM = "awaiting_confirm"
    DONE = "done"
    FAILED = "failed"
    #: 人主动不要这一轮了（`02` §7）。与 FAILED 的区别：失败是"系统出问题、可重试"，
    #: 放弃是"人不想要了、别再跑"——所以它是终点，没有出口，也不写 failure。
    ABANDONED = "abandoned"


class WaitingOn(StrEnum):
    NONE = "none"
    OPENING = "opening_card"
    QUESTIONNAIRE = "questionnaire"
    DRAFT = "draft_card"


LEGAL_ADVANCES: dict[Step, frozenset[Step]] = {
    Step.STARTED: frozenset({Step.CLAIMS_READY}),
    Step.CLAIMS_READY: frozenset({Step.SEATS_RUNNING}),
    Step.SEATS_RUNNING: frozenset({Step.AWAITING_ANSWERS, Step.AWAITING_CONFIRM}),
    Step.AWAITING_ANSWERS: frozenset({Step.SEATS_RUNNING}),
    Step.AWAITING_CONFIRM: frozenset({Step.DONE}),
    Step.DONE: frozenset(),
    Step.FAILED: frozenset(),
    Step.ABANDONED: frozenset(),
}

MAX_SEAT_ROUNDS = 3
