"""轮内未决队列：被 5 题上限挤掉的题要真的"留到下一轮"，而不是写完就没人读。

真跑实测（`.codex/runs/2026-09-28/报告.md` F12）：溢出题原来被写成 `list_items`，
理由"本批只问 5 题，这条留到下一轮"——但 `list_items` 只挂在当轮问卷上，答完就被覆盖，**凭空消失**。
"""

from dataclasses import replace

import pytest

from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import Option, Question, QuestionType
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import MAX_SEAT_ROUNDS, Step, WaitingOn
from pmstudio.domain.shared.errors import IllegalStepTransition, SeatRoundLimitReached
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FormId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.sqlite.serialization import round_from_payload, round_to_payload
from tests.support.builders import NOW, PROJECT, ROUND_ID, make_round


def question(title: str = "导出要支持多大？") -> Question:
    return Question(
        question_id=QuestionId("q1"),
        target=Target.body("s8"),
        type=QuestionType.SINGLE,
        title=title,
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现，超时风险由 PM 承担",
        options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
    )


def test_defer_then_take_is_fifo_and_dedups():
    round_ = make_round()

    round_.defer([question("A"), question("B"), question("A")])  # 同一件事只留第一次
    taken = round_.take_deferred(2)

    assert [item.title for item in taken] == ["A", "B"]
    assert round_.deferred == ()  # 取走了


def test_peek_does_not_consume():
    round_ = make_round()
    round_.defer([question("A")])

    assert [item.title for item in round_.peek_deferred(5)] == ["A"]
    assert len(round_.deferred) == 1


def test_clear_deferred_returns_everything_left():
    round_ = make_round()
    round_.defer([question("A"), question("B")])

    assert [item.title for item in round_.clear_deferred()] == ["A", "B"]
    assert round_.deferred == ()


def test_deferred_survives_a_payload_round_trip():
    round_ = make_round()
    round_.defer([replace(question("A"), from_entry="r1#open01")])

    payload = round_to_payload(round_)
    back = round_from_payload(
        payload,
        round_id=RoundId(str(round_.round_id)),
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert round_.created_at == NOW
    assert [item.title for item in back.deferred] == ["A"]
    assert back.deferred[0].from_entry == "r1#open01"


def test_old_payload_without_deferred_reads_as_an_empty_queue():
    """老行没有 `deferred_questions` 键：读回来是空队列，不能崩。"""

    payload = round_to_payload(make_round())
    payload.pop("deferred_questions")

    back = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=NOW,
        step=make_round().step,
        waiting_on=make_round().waiting_on,
    )

    assert back.deferred == ()


def test_carried_sources_survive_the_questionnaire_being_replaced():
    """问卷下一批会被覆盖，但"这道题来自哪条未决条目"必须留着（答完要撤下它）。"""

    round_ = make_round()
    round_.remember_carried(
        FormId("f1"),
        [replace(question("A"), question_id=QuestionId("q1"), from_entry="r0#open01")],
    )
    round_.remember_carried(
        FormId("f1"),
        [replace(question("B"), question_id=QuestionId("q2"), from_entry="r0#open02")],
    )

    assert round_.carried_sources() == {"f1#q1": "r0#open01", "f1#q2": "r0#open02"}


def test_carried_sources_round_trip_and_old_payload_is_empty():
    round_ = make_round()
    round_.remember_carried(
        FormId("f1"),
        [replace(question("A"), question_id=QuestionId("q1"), from_entry="r0#open01")],
    )
    payload = round_to_payload(round_)
    back = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=NOW,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert back.carried_sources() == {"f1#q1": "r0#open01"}

    payload.pop("carried_sources")
    old = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=NOW,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert old.carried_sources() == {}


# —— C6：用户主动"继续深挖"（P14）——


def round_awaiting_confirm() -> Round:
    """一轮停在草案卡（**带一条没问上的题**，这正是 dig 要把它问回来的东西）。"""

    round_ = make_round()
    round_.defer((question("这条没问上"),))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND_ID,
            proposals=(
                Proposal(
                    proposal_id=ProposalId("p01"),
                    entry_id=EntryId("r1#01"),
                    op=WriteOp.ADD,
                    target=Target.body("s2"),
                    text="现状：导出靠手工复制",
                    origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
                    kind=BlockKind.TEXT,
                ),
            ),
        )
    )
    assert round_.step is Step.AWAITING_CONFIRM and round_.waiting_on is WaitingOn.DRAFT
    return round_


def test_dig_sends_the_round_back_to_the_seat_round_with_a_spare_batch():
    round_ = round_awaiting_confirm()

    round_.dig()

    assert round_.step is Step.SEATS_RUNNING
    assert round_.extra_batches == 1
    assert round_.draft is None  # 旧草案作废（没确认写入，文档不受影响）
    assert round_.seat_round_count == 2  # 新开一次席位轮
    assert round_.deferred == (question("这条没问上"),)  # 队列留着 → 下一趟问得回来


def test_dig_is_refused_when_the_round_is_not_awaiting_confirm():
    with pytest.raises(IllegalStepTransition):
        make_round().dig()


def test_dig_is_refused_when_the_seat_rounds_are_used_up():
    round_ = round_awaiting_confirm()
    for _ in range(MAX_SEAT_ROUNDS - 1):
        round_._begin_seat_round()  # 把这一轮的席位轮额度用满（测试内部手法）

    with pytest.raises(SeatRoundLimitReached):
        round_.dig()


def test_payload_roundtrip_keeps_the_extra_batches():
    round_ = round_awaiting_confirm()
    round_.dig()

    payload = round_to_payload(round_)
    restored = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert restored.extra_batches == 1

    del payload["extra_batches"]  # 老行：没有这个键 → 0
    legacy = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert legacy.extra_batches == 0
