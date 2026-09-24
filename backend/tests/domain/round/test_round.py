from dataclasses import replace
from datetime import datetime

import pytest

from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import DroppedItem, DropSource, Failure, Round
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import (
    AlreadyAnswered,
    ContentWithoutEvidence,
    EmptyUserInput,
    FeatureAlreadyAuthorized,
    IllegalStepTransition,
    NotAwaitingAnswers,
    SeatRoundLimitReached,
    ShapeError,
    UnknownQuestion,
    UnknownSeat,
    WaitingOnHuman,
)
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.invariants import RInvariant
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

NOW = datetime(2026, 9, 22, 10, 0, 0)
REF = EvidenceId("E1")
SEAT = "seat.data_scale"


def claim() -> Claim:
    return Claim(
        claim_id="c1",
        text="要做订单导出功能",
        label=ClaimLabel.NEED,
        target=Target.body("s5"),
        raw_span="想做个订单导出的功能",
        eid=REF,
    )


def round_with_claims() -> Round:
    round_ = Round.start(
        round_id=RoundId("r1"),
        project_id=ProjectId("p1"),
        user_input="想做个订单导出的功能，数据量大概几万",
        now=NOW,
    )
    round_.attach_claims((claim(),))
    return round_


def round_with_seats() -> Round:
    round_ = round_with_claims()
    round_.submit_opening(seats=(SEAT,))
    return round_


def make_questionnaire(form_id: str = "f1", *, target: Target | None = None) -> Questionnaire:
    return Questionnaire(
        form_id=FormId(form_id),
        round_id=RoundId("r1"),
        questions=(
            Question(
                question_id=QuestionId("q1"),
                target=target or Target.body("s8"),
                type=QuestionType.SINGLE,
                title="单次导出最大行数？",
                why="量级决定同步还是异步",
                consequence_if_skipped="按同步实现，超时风险由 PM 承担",
                options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
                evidence_refs=(REF,),
                asked_by=(SEAT,),
            ),
        ),
    )


def make_draft() -> Draft:
    return Draft(
        draft_id=DraftId("d1"),
        round_id=RoundId("r1"),
        proposals=(
            Proposal(
                proposal_id=ProposalId("p1"),
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body("s5"),
                text="验收标准：5 万条内 60 秒可下载",
                origin=Origin(OriginType.DERIVED, (REF,), "量级在 1 万级"),
                kind=BlockKind.ACCEPTANCE,
            ),
        ),
    )


def answer_round(round_: Round, form_id: str) -> None:
    """走完一次"出问卷 → 交卷 → 回到席位轮"，用来数席位轮次数。"""

    round_.integrate(questionnaire=make_questionnaire(form_id=form_id))
    round_.submit_answers(
        AnswerSet(
            form_id=FormId(form_id),
            answers=(Answer(QuestionId("q1"), "1 万–10 万"),),
            answered_at=NOW,
        )
    )


def test_R1_ok_start_binds_input():
    round_ = Round.start(
        round_id=RoundId("r1"),
        project_id=ProjectId("p1"),
        user_input="想做个订单导出的功能",
        now=NOW,
    )
    assert round_.user_input == "想做个订单导出的功能"
    assert round_.step is Step.STARTED
    assert round_.waiting_on is WaitingOn.NONE
    assert round_.seat_round_count == 0


def test_R1_bad_empty_input():
    with pytest.raises(EmptyUserInput) as exc:
        Round.start(
            round_id=RoundId("r1"), project_id=ProjectId("p1"), user_input="   ", now=NOW
        )
    assert exc.value.invariant == RInvariant.R1


def test_R1_bad_input_is_read_only():
    round_ = round_with_claims()
    with pytest.raises(AttributeError):
        round_.user_input = "改输入"  # type: ignore[misc]


def test_GB1_bad_claim_without_eid():
    round_ = Round.start(
        round_id=RoundId("r1"), project_id=ProjectId("p1"), user_input="想法", now=NOW
    )
    with pytest.raises(ContentWithoutEvidence):
        round_.attach_claims(
            (
                Claim(
                    claim_id="c1",
                    text="要做导出",
                    label=ClaimLabel.NEED,
                    target=Target.body("s5"),
                    raw_span="要做导出",
                ),
            )
        )


def test_R2_ok_legal_path():
    round_ = round_with_seats()
    assert round_.step is Step.SEATS_RUNNING
    assert round_.seat_round_count == 1
    round_.integrate(questionnaire=make_questionnaire())
    assert (round_.step, round_.waiting_on) == (Step.AWAITING_ANSWERS, WaitingOn.QUESTIONNAIRE)
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(QuestionId("q1"), "1 万–10 万"),),
            answered_at=NOW,
        )
    )
    assert (round_.step, round_.waiting_on) == (Step.SEATS_RUNNING, WaitingOn.NONE)
    assert round_.seat_round_count == 2
    round_.integrate(draft=make_draft())
    assert (round_.step, round_.waiting_on) == (Step.AWAITING_CONFIRM, WaitingOn.DRAFT)
    round_.confirm(version_no=2)
    assert round_.step is Step.DONE
    assert round_.confirmed_version_no == 2


def test_R2_bad_backward_step():
    round_ = round_with_seats()
    with pytest.raises(IllegalStepTransition) as exc:
        round_.advance(Step.CLAIMS_READY)
    assert exc.value.invariant == RInvariant.R2


def test_R2_bad_advance_while_waiting():
    round_ = round_with_claims()
    with pytest.raises(WaitingOnHuman) as exc:
        round_.advance(Step.SEATS_RUNNING)
    assert exc.value.invariant == RInvariant.R2


def test_R3_ok_three_seat_rounds():
    round_ = round_with_seats()  # 第 1 次席位轮
    answer_round(round_, "f1")  # 第 2 次
    answer_round(round_, "f2")  # 第 3 次
    assert round_.seat_round_count == 3
    assert round_.can_begin_seat_round is False


def test_R3_bad_fourth_seat_round():
    round_ = round_with_seats()
    answer_round(round_, "f1")
    answer_round(round_, "f2")
    round_.integrate(questionnaire=make_questionnaire(form_id="f3"))
    with pytest.raises(SeatRoundLimitReached) as exc:
        round_.submit_answers(
            AnswerSet(
                form_id=FormId("f3"),
                answers=(Answer(QuestionId("q1"), "1 万–10 万"),),
                answered_at=NOW,
            )
        )
    assert exc.value.invariant == RInvariant.R3
    assert round_.step is Step.AWAITING_ANSWERS


def test_R5_ok_draft_from_integrate():
    round_ = round_with_seats()
    round_.integrate(draft=make_draft())
    assert round_.draft is not None
    assert round_.draft.grouped()[0][0] == "它替你判断的"


def test_R5_bad_draft_before_integrate():
    round_ = round_with_claims()
    with pytest.raises(IllegalStepTransition) as exc:
        round_.integrate(draft=make_draft())
    assert exc.value.invariant == RInvariant.R2
    with pytest.raises(ShapeError):
        round_with_seats().integrate()


def test_R6_ok_answer_set():
    round_ = round_with_seats()
    round_.integrate(questionnaire=make_questionnaire())
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(QuestionId("q1"), "1 万–10 万"),),
            skipped=(),
            answered_at=NOW,
        )
    )
    assert len(round_.answer_sets) == 1


def test_R6_bad_unknown_question():
    round_ = round_with_seats()
    round_.integrate(questionnaire=make_questionnaire())
    with pytest.raises(UnknownQuestion) as exc:
        round_.submit_answers(
            AnswerSet(
                form_id=FormId("f1"),
                answers=(Answer(QuestionId("q99"), "随便答"),),
                answered_at=NOW,
            )
        )
    assert exc.value.invariant == RInvariant.R6


def test_R6_bad_answer_twice():
    round_ = round_with_seats()
    round_.integrate(questionnaire=make_questionnaire())
    answers = AnswerSet(
        form_id=FormId("f1"),
        answers=(Answer(QuestionId("q1"), "1 万–10 万"),),
        answered_at=NOW,
    )
    round_.submit_answers(answers)
    round_.integrate(questionnaire=make_questionnaire())
    with pytest.raises(AlreadyAnswered) as exc:
        round_.submit_answers(answers)
    assert exc.value.invariant == RInvariant.R6


def test_R6_bad_not_awaiting_answers():
    with pytest.raises(NotAwaitingAnswers) as exc:
        round_with_seats().submit_answers(
            AnswerSet(
                form_id=FormId("f1"),
                answers=(Answer(QuestionId("q1"), "x"),),
                answered_at=NOW,
            )
        )
    assert exc.value.invariant == RInvariant.R6


def test_R7_ok_attest_known_seat():
    round_ = round_with_seats()
    round_.attest(
        SeatReview(
            seat_id=SEAT,
            basis="读了 s5 与本轮 E1",
            opinions=(
                Opinion(
                    local_id="O1",
                    form=OpinionForm.JUDGMENT,
                    about="导出的性能上限",
                    claim="单次导出 60 秒内",
                    because="量级在 1 万级",
                    evidence_refs=(REF,),
                ),
            ),
        )
    )
    assert [opinion.local_id for opinion in round_.opinions] == ["O1"]


def test_R7_bad_attest_unknown_seat():
    round_ = round_with_seats()
    with pytest.raises(UnknownSeat) as exc:
        round_.attest(SeatReview(seat_id="seat.mystery", basis="读了 s5"))
    assert exc.value.invariant == RInvariant.R7


def test_write_scope_covers_claims_and_answered_questions():
    round_ = round_with_seats()
    assert round_.write_scope == frozenset({Target.body("s5")})
    round_.integrate(questionnaire=make_questionnaire())
    assert Target.body("s8") not in round_.write_scope
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(QuestionId("q1"), "1 万–10 万"),),
            answered_at=NOW,
        )
    )
    assert Target.body("s8") in round_.write_scope


def test_fail_and_resume_restore_step_and_waiting():
    round_ = round_with_seats()
    round_.integrate(questionnaire=make_questionnaire())
    round_.fail("模型超时")
    assert round_.step is Step.FAILED
    assert round_.failure is not None
    round_.resume()
    assert round_.step is Step.AWAITING_ANSWERS
    assert round_.waiting_on is WaitingOn.QUESTIONNAIRE
    with pytest.raises(ShapeError):
        round_.resume()


def test_authorize_new_feature_twice_raises():
    round_ = round_with_seats()
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=REF)
    assert round_.authorized_new_features == frozenset({FeatureSlug("order-export")})
    with pytest.raises(FeatureAlreadyAuthorized):
        round_.authorize_new_feature(FeatureSlug("order-export"), basis=REF)


def test_R3_ok_current_reviews_isolates_seat_pass():
    round_ = round_with_seats()  # 第 1 次席位轮
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 s5 与 E1"))
    answer_round(round_, "f1")  # 交卷 → 第 2 次席位轮
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 q1 的答案"))

    assert [review.pass_index for review in round_.seat_reviews] == [1, 2]
    assert len(round_.current_reviews) == 1
    assert round_.current_reviews[0].basis == "读了 q1 的答案"


def test_R3_ok_pending_seats_shrinks_as_seats_attest():
    round_ = round_with_claims()
    round_.submit_opening(seats=(SEAT, "seat.permissions"))

    assert round_.pending_seats == (SEAT, "seat.permissions")

    round_.attest(SeatReview(seat_id=SEAT, basis="读了 s5 与 E1"))

    assert round_.pending_seats == ("seat.permissions",)
    assert len(round_.current_reviews) == 1  # 已交回的那条还在，重进不会重跑


def test_opening_ok_revise_claims_changes_label_and_target():
    round_ = round_with_claims()
    revised = replace(claim(), label=ClaimLabel.GOAL, target=Target.body("s2"))
    round_.revise_claims((revised,))

    assert round_.claims[0].label is ClaimLabel.GOAL
    assert round_.claims[0].target == Target.body("s2")
    assert round_.step is Step.CLAIMS_READY
    assert round_.waiting_on is WaitingOn.OPENING


def test_opening_bad_revise_claims_must_cover_the_same_ids():
    round_ = round_with_claims()
    with pytest.raises(ShapeError):
        round_.revise_claims(())


def test_opening_bad_revise_claims_cannot_change_eid_or_text():
    round_ = round_with_claims()
    with pytest.raises(ShapeError):
        round_.revise_claims((replace(claim(), eid=EvidenceId("E99")),))
    with pytest.raises(ShapeError):
        round_.revise_claims((replace(claim(), text="换个说法"),))


def test_opening_bad_revise_claims_after_seat_round_started():
    round_ = round_with_seats()
    with pytest.raises(IllegalStepTransition):
        round_.revise_claims((claim(),))


def test_fail_is_idempotent_so_a_second_writer_cannot_move_the_resume_point():
    """并发/重复失败时，`at_step` 必须还是"从哪一步重跑"，不能被后一次改写。

    作者库里真出现过 `at_step=failed`：两个重试同时跑，第二次记失败时 step 已经是 failed，
    于是续跑点被写成 failed，这一轮再也恢复不了（`04-未决.md` C9）。
    """

    round_ = round_with_seats()
    round_.fail("第一次失败")
    round_.fail("第二次失败（另一次并发尝试）")

    assert round_.step is Step.FAILED
    assert round_.failure is not None
    assert round_.failure.at_step is Step.SEATS_RUNNING
    assert round_.failure.reason == "第一次失败"


def test_resume_refuses_a_resume_point_that_is_itself_failed():
    """老库里的坏行：`at_step=failed`。要明确报错，而不是让路由表 KeyError（500）。"""

    round_ = Round(
        round_id=RoundId("r1"),
        project_id=ProjectId("p1"),
        user_input="想做个订单导出的功能",
        created_at=NOW,
        step=Step.FAILED,
        waiting_on=WaitingOn.NONE,
        failure=Failure(at_step=Step.FAILED, waiting_on=WaitingOn.NONE, reason="坏行"),
    )

    with pytest.raises(ShapeError) as error:
        round_.resume()

    assert "at_step" in error.value.message


# —— P5b Task 1：留痕通道 ——


def test_dropped_items_are_recorded_on_the_round():
    """留痕落进聚合：`04` C5 要的"这条为什么没进卡片"要能过两天再查。"""

    round_ = round_with_seats()
    round_.integrate(
        questionnaire=make_questionnaire(),
        dropped=(
            DroppedItem(
                handle="seat.data_scale:O1",
                reason="与文档已有内容重复",
                source=DropSource.INTEGRATOR,
                seat_id="seat.data_scale",
                pass_index=1,
            ),
        ),
    )

    assert [(item.handle, item.reason, item.source) for item in round_.dropped] == [
        ("seat.data_scale:O1", "与文档已有内容重复", DropSource.INTEGRATOR)
    ]


def test_round_without_dropped_items_is_empty():
    assert round_with_seats().dropped == ()


@pytest.mark.parametrize(
    "override",
    [
        {"handle": "   "},
        {"reason": "  "},
        {"pass_index": 0},
    ],
)
def test_dropped_item_requires_handle_reason_and_pass(override):
    base = {
        "handle": "seat.x:O1",
        "reason": "重复",
        "source": DropSource.INTEGRATOR,
        "seat_id": None,
        "pass_index": 1,
    }

    with pytest.raises(ShapeError):
        DroppedItem(**{**base, **override})


def test_dropped_handle_shape_is_not_validated_on_purpose():
    """模型写歪的地址正是要留痕的东西——写歪就抛错等于"写歪了就不留痕"。"""

    item = DroppedItem(
        handle="查不到:O9",
        reason="归因对不上号",
        source=DropSource.ASSEMBLY,
        seat_id=None,
        pass_index=1,
    )

    assert item.seat_id is None
    assert item.handle == "查不到:O9"
