from datetime import datetime

from pmstudio.application import context
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DocumentId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProjectId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

NOW = datetime(2026, 9, 22, 10, 0)
PROJECT = ProjectId("p1")


def document_with_one_feature() -> Document:
    document = Document.new(
        document_id=DocumentId("doc1"), project_id=PROJECT, title="订单中台", now=NOW
    )
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id="en1",  # type: ignore[arg-type]
                op=WriteOp.ADD,
                target=Target.feature_field(FeatureSlug("order-export"), "one_liner"),
                text="把订单导出成表格",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )
    return document


def test_render_document_lists_thirteen_sections_and_marks_empty_ones():
    text = context.render_document(document_with_one_feature().view())
    assert text.count("## ") == 13
    assert "## s1 背景与问题" in text
    assert "（空）" in text
    assert "feature#order-export" in text
    assert "把订单导出成表格" in text
    assert "E1" in text


def test_render_claims_keeps_numbers_labels_targets_and_raw_span():
    claim = Claim(
        claim_id="c1",
        text="要做一个订单导出功能",
        label=ClaimLabel.NEED,
        target=Target.body("s5"),
        raw_span="想做个订单导出的功能",
        eid=EvidenceId("E1"),
    )
    text = context.render_claims((claim,))
    assert "[E1]" in text
    assert "需求" in text
    assert "s5" in text
    assert "想做个订单导出的功能" in text


def test_render_reviews_shows_forms_basis_and_refs():
    review = SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5 与 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.QUESTION,
                about="导出的数据量级",
                claim="量级决定同步还是异步，必须问用户",
                because="1 万以内可同步，10 万以上必须异步",
                evidence_refs=(EvidenceId("E1"),),
            ),
            Opinion(
                local_id="O2",
                form=OpinionForm.JUDGMENT,
                about="导出的性能上限",
                claim="单次导出应在 60 秒内完成",
                because="量级在 1 万级，同步实现可满足",
                evidence_refs=(EvidenceId("E1"),),
            ),
        ),
        pass_index=2,
    )
    text = context.render_reviews((review,), titles={"seat.data_scale": "数据规模与性能"})
    assert "数据规模与性能" in text
    assert "疑问" in text
    assert "判断" in text
    assert "O2" in text
    assert "读了 s5 与 E1" in text


def test_render_existing_entries_lists_entry_ids_for_rewrites():
    text = context.render_existing_entries(document_with_one_feature())
    assert "en1" in text
    assert "把订单导出成表格" in text


def round_after_one_answer_one_skip() -> Round:
    round_ = Round.start(
        round_id=RoundId("r1"), project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims(
        (
            Claim(
                claim_id="c1",
                text="要做一个订单导出功能",
                label=ClaimLabel.NEED,
                target=Target.body("s5"),
                raw_span="想做个订单导出",
                eid=EvidenceId("E1"),
            ),
        )
    )
    round_.submit_opening(seats=("seat.data_scale",))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=RoundId("r1"),
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.SINGLE,
                    title="单次导出最大行数？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                    options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
                ),
                Question(
                    question_id=QuestionId("q2"),
                    target=Target.body("s3"),
                    type=QuestionType.SINGLE,
                    title="谁能导出？",
                    why="决定权限口径",
                    consequence_if_skipped="默认只有运营专员",
                    options=(Option("a", "运营专员"), Option("b", "运营 + 主管")),
                ),
            ),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(QuestionId("q1"), "1 万–10 万", ("b",)),),
            skipped=(QuestionId("q2"),),
            answered_at=NOW,
        )
    )
    return round_


def test_render_answers_separates_answered_and_skipped():
    text = context.render_answers(round_after_one_answer_one_skip())
    answered, _, skipped = text.partition("--")
    assert "1 万–10 万" in answered
    assert "q1" in answered
    assert "q2" in skipped
    assert "默认只有运营专员" in skipped  # 跳过后果要带给整合者


def test_labels_and_sections_text_come_from_domain_constants():
    assert "猜测" in context.LABELS_TEXT
    assert "s5 功能需求" in context.SECTIONS_TEXT
