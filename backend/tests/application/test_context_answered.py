"""答案的身份是"轮次 + 题号"（2026-09-29 拍板 D4）。

渲染"用户已答"那一段时，题面与跳过后果要回到**出题那一批**去找，出处编号要按
`(轮次, 题号)` 认。原来两样都按**裸题号**在**当前批**里找，第二批发到同一个号就全错——
真跑里两行答案挂同一个题面、写同一个 E 编号（另一条的编号根本没出现在提示词里）。

找不到（老库、题号重排过的历史行）就只写题号：**宁可不显示，也不显示错的**。
"""

from pmstudio.application import context
from pmstudio.application.context import answer_eids, render_answers, render_asked_questions
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


def round_with_unanswered_questions() -> Round:
    """第一批问出去 2 道题，用户**一道都没答、也没跳过**（真跑里最常见的那条路）。

    "没答案"不是异常：`use_cases.py` 整合那一步明说「答案要么被引用、要么机械落地」，
    用户直接点确认是正常主路径。而 `AnswerSet.__post_init__` 又规定「交卷至少要有
    答案或显式跳过」——所以**没有答案集**就意味着这一批一道都没碰过。
    """

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
            questions=(
                question("q1", "单次导出的最大行数是多少？"),
                question("q2", "导出文件的具体格式要求是什么？"),
            ),
        )
    )
    return round_


def test_a_question_asked_but_never_answered_is_shown_to_the_model():
    """问过、没答也没跳过的题**要渲染出来**——模型看不见它，就一定会重问。

    真跑实测（2026-10-09，10 轮 14 条留痕）：这些题在 `assembly.py` 进了
    `seen_questions` 种子，而 `render_answers` 只认 `answer_sets`——**没答案集时
    题面一个字都不进提示词**。后果：模型在下一趟把上一批的题**逐字重写**（14/14 条
    题面完全相同，不是措辞近似），被判重丢掉。它那一趟白烧，系统也没告诉它为什么。
    """

    text = render_asked_questions(round_with_unanswered_questions())

    assert "单次导出的最大行数是多少？" in text
    assert "导出文件的具体格式要求是什么？" in text


def test_the_asked_list_names_the_questions_not_just_their_numbers():
    """光给 `q2` 没用——模型要能判断"这道我是不是问过"，所以**题号与题面都要**。"""

    text = render_asked_questions(round_with_unanswered_questions())

    assert "q1 单次导出的最大行数是多少？" in text
    assert "q2 导出文件的具体格式要求是什么？" in text


def test_answered_and_skipped_questions_do_not_repeat_in_the_asked_list():
    """已答的、已跳过的**都不许再进这一段**：它们前两段已经说过了，重复渲染是噪音。

    身份按 `(form_id, question_id)` 认（不是裸题号）——题号跨批会重复（D4 之前的老库，
    `render_answers` 开头那段注释就是这件事）。
    """

    round_ = round_with_unanswered_questions()
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(question_id=QuestionId("q1"), text="10 万"),),
            skipped=(QuestionId("q2"),),
            answered_at=NOW,
        ),
        active_seats=(SEAT,),
    )

    text = render_asked_questions(round_)

    assert "单次导出的最大行数" not in text
    assert "导出文件的具体格式要求" not in text
    assert text == context.NO_PENDING_QUESTION_TEXT


def test_the_asked_list_is_a_slot_of_its_own_not_a_third_paragraph_of_the_answers():
    """**不加进 `render_answers`**——调用侧（`use_cases.py`）用 `.partition("--")`
    把它的输出切成 `answered` / `skipped` 两段，多塞一段会被切错位置，把没答的题
    显示在「用户跳过」标题下。所以这里是独立函数、独立槽。
    """

    text = render_answers(round_with_unanswered_questions())

    assert text.count("\n--\n") == 1  # 只切得开两段
    assert "单次导出的最大行数" not in text
