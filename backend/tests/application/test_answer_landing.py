"""答案要么被引用、要么机械落地（2026-09-29 拍板 D3）。

跨轮去重只认文档（P9 定过：不新建第二真相源）：一条答案要是没落进文档，下一轮必然再问一遍
（报告 F6 里"备件 Excel 同步的触发方式"第二轮答过、第三轮又原样出现）。

所以出草案那一刻核查：这条答案的出处编号**有没有被这一趟的提案/题目引用**？没有就机械落一条——
去处 = 那道题的去处，文本 = `用户已定：<选项文案 或 自由文本>`（用选项文案，不用 `new_feature_no`
这种机器 id），出处 = 这条答案自己的 E 编号，`origin=user`（用户自己说的话，卡片上默认勾选）。

三种**不落地**（各自写清为什么，绝不静默）：
① 题的 target 是**未授权的新功能 slug**——落下去等于凭空建功能子树，撞硬约束；
② 这道题来自 s11 未决条目——答完已经有机械 `remove` 把它撤下，再 ADD 一条会当场对冲；
③ 答案已经被引用（整合者干完了活）。
"""

from pmstudio.application import assembly
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
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
from pmstudio.domain.shared.ids import EvidenceId, FeatureSlug, FormId, QuestionId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import NOW, PROJECT, ROUND_ID, document_with_feature


def question(question_id: str, *, target: Target) -> Question:
    return Question(
        question_id=QuestionId(question_id),
        target=target,
        type=QuestionType.SINGLE,
        title="单次导出的最大行数是多少？",
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现，超时风险由 PM 承担",
        options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
    )


def round_with_one_answer(
    *,
    target: Target,
    selected: tuple[str, ...] = ("a",),
    text: str = "",
    carried: bool = False,
    authorized: tuple[str, ...] = (),
) -> Round:
    form = Questionnaire(
        form_id=FormId("f1"),
        round_id=ROUND_ID,
        questions=(question("q1", target=target),),
    )
    return Round(
        round_id=ROUND_ID,
        project_id=PROJECT,
        user_input="想做个订单导出",
        created_at=NOW,
        questionnaire=form,
        questionnaire_history=(form,),
        answer_sets=(
            AnswerSet(
                form_id=FormId("f1"),
                answers=(Answer(question_id=QuestionId("q1"), text=text, selected=selected),),
                answered_at=NOW,
            ),
        ),
        carried_sources={"f1#q1": "r0#open01"} if carried else {},
        authorized_new_features={
            FeatureSlug(slug): EvidenceId("E9") for slug in authorized
        },
    )


def ledger_with_the_answer(*, text: str = "少于 1 万") -> EvidenceLedger:
    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text=text,
        source=EvidenceSource(round_id=ROUND_ID, question_id=QuestionId("q1")),
        now=NOW,
    )
    return ledger


def document() -> Document:
    return document_with_feature()


def test_an_uncited_answer_lands_as_content_with_its_own_evidence():
    ledger = ledger_with_the_answer()

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(target=Target.body("s8")),
        ledger=ledger,
        document=document(),
    )

    assert len(landed.proposals) == 1
    proposal = landed.proposals[0]
    assert proposal.op is WriteOp.ADD
    assert proposal.target == Target.body("s8")
    assert proposal.text == "用户已定：少于 1 万"  # 选项**文案**，不是 option_id
    assert proposal.origin.refs == (EvidenceId("E1"),)
    assert proposal.origin.type.value == "user"
    assert landed.notes == ()


def test_a_cited_answer_is_not_landed_twice():
    """整合者已经引用了这条答案 ⇒ 它自己会写进文档，机械通道不许再落一条。"""

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(target=Target.body("s8")),
        ledger=ledger_with_the_answer(),
        document=document(),
        cited=frozenset({EvidenceId("E1")}),
    )

    assert landed.proposals == ()


def test_an_answer_about_an_unauthorized_new_feature_is_not_landed():
    """落下去等于凭空建功能子树（硬约束）——不落，但要说清为什么。"""

    target = Target.feature_field(FeatureSlug("brand-new-thing"), "acceptance")

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(target=target),
        ledger=ledger_with_the_answer(),
        document=document(),
    )

    assert landed.proposals == ()
    assert len(landed.notes) == 1
    assert "brand-new-thing" in landed.notes[0]


def test_an_authorized_new_feature_still_lands():
    """用户答了"是独立功能"（于是被授权）⇒ 照落，别把这条路堵死。"""

    target = Target.feature_field(FeatureSlug("brand-new-thing"), "acceptance")

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(target=target, authorized=("brand-new-thing",)),
        ledger=ledger_with_the_answer(),
        document=document(),
    )

    assert [item.target for item in landed.proposals] == [target]
    assert landed.notes == ()


def test_an_answer_carried_from_s11_is_not_landed():
    """来自 s11 未决条目的答案已经有机械 `remove` 撤下它，再 ADD 一条会当场对冲。"""

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(target=Target.body("s11"), carried=True),
        ledger=ledger_with_the_answer(),
        document=document(),
    )

    assert landed.proposals == ()
    assert landed.notes == ()


def test_a_free_text_answer_lands_verbatim():
    ledger = ledger_with_the_answer(text="定时轮询，10 分钟一次")

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(
            target=Target.body("s9"), selected=(), text="定时轮询，10 分钟一次"
        ),
        ledger=ledger,
        document=document(),
    )

    assert landed.proposals[0].text == "用户已定：定时轮询，10 分钟一次"


def test_an_empty_answer_lands_nothing():
    """空答没有内容可落：不编、也不留噪声。"""

    landed = assembly.landed_answer_proposals(
        round_with_one_answer(target=Target.body("s8"), selected=(), text="  "),
        ledger=ledger_with_the_answer(text="单次导出的最大行数是多少？（空答）"),
        document=document(),
    )

    assert landed.proposals == ()
    assert landed.notes == ()
