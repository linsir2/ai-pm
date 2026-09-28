"""eval 的断言本身要能离线测：不然"提示词没退化"这个结论就站不住（`02` §5.4）。

真模型那条路（`scripts/eval.py`）只负责把一轮跑出来；判断全在这里，纯函数。
"""

from evals.assertions import EVAL_ASSERTIONS, check

from pmstudio.application import dto
from pmstudio.domain.round.questionnaire import (
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import DroppedItem, DropSource, Round
from pmstudio.domain.shared.ids import EvidenceId, FormId, QuestionId
from pmstudio.domain.shared.target import Target
from tests.support.builders import ROUND_ID, make_round


def question(question_id: str, *, target: Target, eids: tuple[str, ...] = ("E1",)) -> Question:
    return Question(
        question_id=QuestionId(question_id),
        target=target,
        type=QuestionType.TEXT,
        title="这面怎么定？",
        why="缺一个只有你能给的信息",
        consequence_if_skipped="按默认处理",
        evidence_refs=tuple(EvidenceId(eid) for eid in eids),
    )


def round_with(questions: list[Question], *, dropped: tuple[DroppedItem, ...] = ()) -> Round:
    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=tuple(questions),
        ),
        dropped=dropped,
    )
    return round_


def view_of(round_: Round):  # type: ignore[no-untyped-def]
    return dto.round_view(round_)


def test_evidence_refs_must_be_eids():
    """题目与提案的编号都必须是 E 开头（P4 的坑：模型把 seat.x:O1 当编号写）。"""

    view = view_of(
        round_with([question("q1", target=Target.body("s8"), eids=("seat.data_scale:O1",))])
    )

    failure = check({"type": "evidence_refs_are_eids"}, view)

    assert failure is not None
    assert "seat.data_scale:O1" in failure


def test_questions_must_stay_within_the_batch_limit():
    view = view_of(
        round_with([question(f"q{index}", target=Target.body("s8")) for index in range(1, 6)])
    )

    assert check({"type": "questions_at_most", "count": 4}, view) is not None
    assert check({"type": "questions_at_most", "count": 5}, view) is None


def test_no_duplicate_questions_on_the_same_target():
    view = view_of(
        round_with(
            [
                question("q1", target=Target.body("s8")),
                question("q2", target=Target.body("s8")),
            ]
        )
    )

    failure = check({"type": "no_duplicate_questions"}, view)

    assert failure is not None
    assert "s8" in failure


def test_dropped_items_must_have_a_reason():
    view = view_of(
        round_with(
            [question("q1", target=Target.body("s8"))],
            dropped=(
                DroppedItem(
                    handle="seat.a:O1",
                    reason="与文档已有内容重复",
                    source=DropSource.INTEGRATOR,
                    seat_id="seat.a",
                    pass_index=1,
                ),
            ),
        )
    )

    assert check({"type": "dropped_items_are_explained"}, view) is None


def test_a_clean_round_passes_everything():
    """一条正常的题 + 一条正常的留痕 → 七条断言全过（否则断言本身有问题）。"""

    view = view_of(
        round_with(
            [question("q1", target=Target.body("s8"))],
            dropped=(
                DroppedItem(
                    handle="seat.a:O1",
                    reason="与文档已有内容重复",
                    source=DropSource.INTEGRATOR,
                    seat_id="seat.a",
                    pass_index=1,
                ),
            ),
        )
    )

    for assertion in EVAL_ASSERTIONS:
        assert check(assertion, view) is None, assertion


def test_unknown_assertion_is_an_error():
    """写错断言类型要炸，不许静默通过——静默通过等于这条 eval 不存在。"""

    import pytest

    with pytest.raises(ValueError):
        check({"type": "nope"}, view_of(make_round()))
