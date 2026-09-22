from datetime import datetime

import pytest

from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    ListItem,
    Option,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.shared.errors import (
    DuplicateProposalId,
    DuplicateQuestionId,
    ProposalWithoutRefs,
    ShapeError,
    TooManyQuestions,
)
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FormId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.invariants import RInvariant
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

NOW = datetime(2026, 9, 22, 10, 0, 0)
REF = EvidenceId("E1")
ROUND = RoundId("r1")


def question(index: int) -> Question:
    return Question(
        question_id=QuestionId(f"q{index}"),
        target=Target.body("s8"),
        type=QuestionType.SINGLE,
        title=f"第 {index} 题",
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步导出实现，超时风险由 PM 承担",
        options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
        evidence_refs=(REF,),
        asked_by=("seat.data_scale",),
    )


def questionnaire(count: int) -> Questionnaire:
    return Questionnaire(
        form_id=FormId("f1"),
        round_id=ROUND,
        questions=tuple(question(index) for index in range(1, count + 1)),
    )


def proposal(proposal_id: str, origin: Origin, **overrides: object) -> Proposal:
    payload = {
        "proposal_id": ProposalId(proposal_id),
        "entry_id": EntryId(f"{proposal_id}-en"),
        "op": WriteOp.ADD,
        "target": Target.body("s5"),
        "text": "验收标准：5 万条内 60 秒可下载",
        "origin": origin,
        "kind": BlockKind.ACCEPTANCE,
    }
    payload.update(overrides)
    return Proposal(**payload)  # type: ignore[arg-type]


def test_R4_ok_five_questions():
    assert len(questionnaire(5).questions) == 5


def test_R4_bad_six_questions():
    with pytest.raises(TooManyQuestions) as exc:
        questionnaire(6)
    assert exc.value.invariant == RInvariant.R4
    assert exc.value.details["limit"] == 5


def test_question_needs_two_options_for_choice_type():
    with pytest.raises(ShapeError):
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s8"),
            type=QuestionType.SINGLE,
            title="标题",
            why="原因",
            consequence_if_skipped="后果",
            options=(Option("a", "只有一个"),),
        )


def test_free_text_question_has_no_options():
    item = Question(
        question_id=QuestionId("q1"),
        target=Target.body("s1"),
        type=QuestionType.TEXT,
        title="现状是什么样的？",
        why="背景要写事实",
        consequence_if_skipped="s1 缺口保留",
    )
    assert item.options == ()


def test_questionnaire_rejects_duplicate_question_ids():
    with pytest.raises(DuplicateQuestionId):
        Questionnaire(
            form_id=FormId("f1"), round_id=ROUND, questions=(question(1), question(1))
        )


def test_questionnaire_carries_downgraded_list_items():
    book = Questionnaire(
        form_id=FormId("f1"),
        round_id=ROUND,
        questions=(question(1),),
        list_items=(
            ListItem(text="导出记录保留多久？", target=Target.body("s8"), reason="超出本批上限"),
        ),
    )
    assert book.list_items[0].reason == "超出本批上限"


def test_answer_set_cannot_answer_and_skip_same_question():
    with pytest.raises(ShapeError):
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(question_id=QuestionId("q1"), text="1 万–10 万"),),
            skipped=(QuestionId("q1"),),
            answered_at=NOW,
        )


def test_R5_bad_draft_without_refs():
    origin = Origin(type=OriginType.DERIVED, refs=(), rationale="有理由但没编号")
    with pytest.raises(ProposalWithoutRefs) as exc:
        proposal("p1", origin)
    assert exc.value.invariant == RInvariant.R5


def test_R5_bad_origin_without_refs():
    """空 refs 的 Origin 只是值对象，允许存在；用它做提案一定被拒（R5 在提案层）。"""

    origin = Origin(type=OriginType.USER, refs=())
    assert origin.refs == ()
    with pytest.raises(ProposalWithoutRefs):
        proposal("p1", origin)


def test_draft_groups_into_three_groups_and_keeps_system_badge():
    draft = Draft(
        draft_id=DraftId("d1"),
        round_id=ROUND,
        proposals=(
            proposal("p1", Origin(OriginType.USER, (EvidenceId("E1"),))),
            proposal(
                "p2", Origin(OriginType.DERIVED, (EvidenceId("E9"),), "量级在 1 万级")
            ),
            proposal(
                "p3",
                Origin(OriginType.SYSTEM_DEFAULT, (EvidenceId("E16"),), "跳过 Q5 的后果落此"),
            ),
            proposal("p4", Origin(OriginType.PROPOSED, (EvidenceId("E15"),))),
        ),
        gaps=("订单导出 · 验收标准",),
    )
    grouped = draft.grouped()
    assert [title for title, _ in grouped] == ["你说过的", "它替你判断的", "它提议的"]
    derived_group = dict(grouped)["它替你判断的"]
    assert [item.origin.type for item in derived_group] == [
        OriginType.DERIVED,
        OriginType.SYSTEM_DEFAULT,
    ]
    assert draft.gaps == ("订单导出 · 验收标准",)


def test_draft_rejects_duplicate_proposal_ids():
    origin = Origin(OriginType.USER, (EvidenceId("E1"),))
    with pytest.raises(DuplicateProposalId):
        Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND,
            proposals=(proposal("p1", origin), proposal("p1", origin)),
        )


def test_question_kind_defaults_to_gap():
    question = Question(
        question_id=QuestionId("q1"),
        target=Target.body("s8"),
        type=QuestionType.SINGLE,
        title="单次导出最大行数？",
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现，超时风险由 PM 承担",
        options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
    )
    assert question.kind is QuestionKind.GAP


def test_question_kind_conflict_is_preserved():
    question = Question(
        question_id=QuestionId("q6"),
        target=Target.body("s4"),
        type=QuestionType.SINGLE,
        title="你上轮定过「不做定时导出」，这轮要「每天定时导出」，改哪个？",
        why="本轮输入与 s4 的既有非目标冲突",
        consequence_if_skipped="维持原文，两处结论并存到交付",
        options=(Option("change_scope", "改非目标，本版做"), Option("keep", "维持不做")),
        kind=QuestionKind.CONFLICT,
    )
    assert question.kind is QuestionKind.CONFLICT


def test_question_bad_unknown_kind_is_rejected():
    with pytest.raises(ShapeError):
        Question(
            question_id=QuestionId("q1"),
            target=Target.body("s8"),
            type=QuestionType.TEXT,
            title="t",
            why="w",
            consequence_if_skipped="c",
            kind="debate",  # type: ignore[arg-type]
        )
