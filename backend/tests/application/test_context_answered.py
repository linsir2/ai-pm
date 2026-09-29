"""答案的身份是"轮次 + 题号"（2026-09-29 拍板 D4）。

渲染"用户已答"那一段时，题面与跳过后果要回到**出题那一批**去找，出处编号要按
`(轮次, 题号)` 认。原来两样都按**裸题号**在**当前批**里找，第二批发到同一个号就全错——
真跑里两行答案挂同一个题面、写同一个 E 编号（另一条的编号根本没出现在提示词里）。

找不到（老库、题号重排过的历史行）就只写题号：**宁可不显示，也不显示错的**。
"""

from pmstudio.application.context import answer_eids, render_answers
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.opinion import SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId, FormId, QuestionId, RoundId
from pmstudio.domain.shared.target import Target
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim

OTHER_ROUND = RoundId("r0")


def question(question_id: str, title: str) -> Question:
    return Question(
        question_id=QuestionId(question_id),
        target=Target.body("s8"),
        type=QuestionType.SINGLE,
        title=title,
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现，超时风险由 PM 承担",
        options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
    )


def two_batch_round() -> Round:
    """第一批 `q1` 已答、第二批 `q2` 正等着答（题号按轮次发，见 D4）。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(question("q1", "第一批：单次导出的最大行数是多少？"),),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(question_id=QuestionId("q1"), text="10 万"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f2"),
            round_id=ROUND_ID,
            questions=(question("q2", "第二批：对端不可用时怎么降级？"),),
        )
    )
    return round_


def ledger_with_one_answer(*, question_id: str, text: str, round_id: RoundId) -> EvidenceLedger:
    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text=text,
        source=EvidenceSource(round_id=round_id, question_id=QuestionId(question_id)),
        now=NOW,
    )
    return ledger


def test_each_answer_carries_its_own_question_and_its_own_evidence_number():
    round_ = two_batch_round()
    ledger = ledger_with_one_answer(question_id="q1", text="10 万", round_id=ROUND_ID)
    ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text="重试三次",
        source=EvidenceSource(round_id=ROUND_ID, question_id=QuestionId("q2")),
        now=NOW,
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f2"),
            answers=(Answer(question_id=QuestionId("q2"), text="重试三次"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )

    text = render_answers(round_, ledger=ledger)

    assert "第一批：单次导出的最大行数是多少？" in text
    assert "第二批：对端不可用时怎么降级？" in text
    assert text.count("〔E1〕") == 1  # 每条答案各带各的编号
    assert text.count("〔E2〕") == 1


def test_the_number_map_only_covers_this_round():
    """老实现收整个项目账本：第二轮席位写 `q1` 会被翻译成**第一轮**那道题的答案（同一根因）。"""

    round_ = two_batch_round()
    ledger = ledger_with_one_answer(question_id="q1", text="上一轮的答案", round_id=OTHER_ROUND)
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f2"),
            answers=(Answer(question_id=QuestionId("q2"), text="重试三次"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )
    ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text="这一批的答案",
        source=EvidenceSource(round_id=ROUND_ID, question_id=QuestionId("q2")),
        now=NOW,
    )

    mapping = answer_eids(ledger=ledger, round_id=ROUND_ID)

    assert mapping == {"q2": "E2"}  # 别的轮那条（q1/E1）不许混进来


def test_an_ambiguous_question_number_shows_no_number_at_all():
    """D4 之前的老库：一轮里同一个题号有两条答案 ⇒ 认不出来，**不显示**也不乱显示。"""

    round_ = two_batch_round()
    ledger = EvidenceLedger.empty(PROJECT)
    for text in ("第一条", "第二条"):
        ledger.record(
            kind=EvidenceKind.CARD_ANSWER,
            text=text,
            source=EvidenceSource(round_id=ROUND_ID, question_id=QuestionId("q1")),
            now=NOW,
        )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f2"),
            answers=(Answer(question_id=QuestionId("q2"), text="重试三次"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )

    text = render_answers(round_, ledger=ledger)

    assert "〔" not in text
    assert answer_eids(ledger=ledger, round_id=ROUND_ID) == {}
