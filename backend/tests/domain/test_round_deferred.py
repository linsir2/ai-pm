"""轮内未决队列：被 5 题上限挤掉的题要真的"留到下一轮"，而不是写完就没人读。

真跑实测（`.codex/runs/2026-09-28/报告.md` F12）：溢出题原来被写成 `list_items`，
理由"本批只问 5 题，这条留到下一轮"——但 `list_items` 只挂在当轮问卷上，答完就被覆盖，**凭空消失**。
"""

from dataclasses import replace

from pmstudio.domain.round.questionnaire import Option, Question, QuestionType
from pmstudio.domain.shared.ids import QuestionId, RoundId
from pmstudio.domain.shared.target import Target
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
