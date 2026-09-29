"""本轮每一批问卷都要记住，题号在一轮里唯一（2026-09-29 作者拍板 D4/D5）。

`integrate(questionnaire=…)` 原来直接覆盖 `_questionnaire`：下一批一到，上一批的题面就没了。
而"按题号回查"的地方一共六处（计划 §0.1），全是拿**裸题号**去当前问卷里找——
第二批发到同一个 `q1` 就全指错（真跑里两行答案挂同一个题面、引同一个 E 编号）。

所以两件事：
① **问卷历史**：追加不覆盖（`questionnaire` 仍是"当前批"，历史给渲染与回查用）；
② **题号按轮次发**：第一批 `q1–q5`，第二批从 `q6` 起——轮内唯一，`(轮次, 题号)` 才是身份。
老行（没有历史键）读回来退化成"只有当前这一批"，不编历史、不抛。
"""

import pytest

from pmstudio.domain.round.claim import Contradiction
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    ListItem,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.round.step import Step, WaitingOn
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
from tests.support.builders import (
    NOW,
    PROJECT,
    ROUND_ID,
    SEAT,
    make_claim,
    make_round,
    make_round_with_contradictions,
)


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
    """一轮里问过两批：第一批 2 题、第二批 1 题。"""

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
            questions=(question("q1", "第一批第一题？"), question("q2", "第一批第二题？")),
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
            questions=(question("q3", "第二批唯一一题？"),),
        )
    )
    return round_


def test_every_batch_is_kept_in_the_history():
    round_ = two_batch_round()

    assert [str(form.form_id) for form in round_.questionnaire_history] == ["f1", "f2"]
    # 当前批还是最后一个：既有读法（dto / 前端 / list_items）全都不用动
    assert str(round_.questionnaire.form_id) == "f2"


def test_asked_question_count_adds_up_across_batches():
    """下一批的题号就是从这里接着发：2 + 1 = 3 ⇒ 下一题是 q4。"""

    assert two_batch_round().asked_question_count == 3


def test_a_round_that_never_asked_anything_counts_zero():
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))

    assert round_.questionnaire_history == ()
    assert round_.asked_question_count == 0


def test_payload_roundtrip_keeps_the_history():
    round_ = two_batch_round()

    payload = round_to_payload(round_)
    restored = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert [str(form.form_id) for form in restored.questionnaire_history] == ["f1", "f2"]
    assert str(restored.questionnaire.form_id) == "f2"


def test_an_old_payload_without_the_key_falls_back_to_the_current_batch():
    """老行没有这个键：退化成"只有当前这一批"，绝不编历史。"""

    round_ = two_batch_round()
    payload = round_to_payload(round_)
    del payload["questionnaire_history"]

    restored = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert [str(form.form_id) for form in restored.questionnaire_history] == ["f2"]
    assert restored.asked_question_count == 1


def list_item(text: str) -> ListItem:
    return ListItem(text=text, target=Target.body("s8"), reason="本批装不下，记下来")


def two_batch_round_with_list_items(
    first: tuple[str, ...], second: tuple[str, ...]
) -> Round:
    """一轮里两批问卷**各记下一批清单项**（F8：原来后一批把前一批盖掉）。

    与用例里同一个套路：清单项先记进轮次，再进这一批的问卷。
    """

    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.remember_list_items(tuple(list_item(text) for text in first))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(question("q1", "第一批第一题？"),),
            list_items=tuple(list_item(text) for text in first),
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
    round_.remember_list_items(tuple(list_item(text) for text in second))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f2"),
            round_id=ROUND_ID,
            questions=(question("q2", "第二批唯一一题？"),),
            list_items=tuple(list_item(text) for text in second),
        )
    )
    return round_


def test_list_items_accumulate_across_batches():
    """F8：清单项的真相源是**轮次**，不再是当轮问卷——不然换一批就消失。"""

    round_ = two_batch_round_with_list_items(("第一批记的",), ("第二批记的",))

    assert [item.text for item in round_.list_items] == ["第一批记的", "第二批记的"]
    # 当轮那批没动：问卷仍然只记自己这一批的
    assert [item.text for item in round_.questionnaire.list_items] == ["第二批记的"]


def test_payload_roundtrip_keeps_list_items_and_old_rows_fall_back():
    round_ = two_batch_round_with_list_items(("第一批记的",), ("第二批记的",))

    payload = round_to_payload(round_)
    restored = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert [item.text for item in restored.list_items] == ["第一批记的", "第二批记的"]

    del payload["list_items"]  # 老行：没有这个键 → 回落到当轮问卷的清单项
    legacy = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert [item.text for item in legacy.list_items] == ["第二批记的"]


def test_round_keeps_contradictions_and_survives_the_payload_roundtrip():
    """互斥对子（analyzer 判的）要活过落库：装配层盖章要用它（T8）。"""

    round_ = make_round_with_contradictions()
    assert [(item.claim_id_a, item.claim_id_b) for item in round_.contradictions] == [("c1", "c2")]

    payload = round_to_payload(round_)
    restored = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )

    assert [item.claim_id_a for item in restored.contradictions] == ["c1"]
    assert restored.contradictions[0].why

    del payload["contradictions"]  # 老行：没有这个键 → 空（不编对子）
    legacy = round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert legacy.contradictions == ()


def test_a_contradiction_must_point_at_claims_this_round_actually_has():
    """对子指向不存在的主张：领域层就拦住（解析那一层已经先丢过一遍）。"""

    from pmstudio.domain.shared.errors import ShapeError

    round_ = make_round_with_contradictions()

    with pytest.raises(ShapeError):
        round_.attach_contradictions(
            (Contradiction(claim_id_a="c1", claim_id_b="c9", why="越界"),)
        )


# —— 续跑口径：失败原因留到重试的第一次尝试（E3，P13）——


def restored(payload: dict) -> Round:
    return round_from_payload(
        payload,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=NOW,
        step=Step.FAILED,
        waiting_on=WaitingOn.NONE,
    )


def test_a_failed_round_remembers_the_reason_for_the_next_attempt():
    """报文类失败要留下"上次错在哪"：重试时递给第一次尝试，省掉一次盲调。"""

    round_ = make_round()
    round_.fail("整合两次都过不了校验：…", hint="evidence_refs 要的是数组，你给的是字符串")

    payload = round_to_payload(round_)
    assert restored(payload).retry_hint == "evidence_refs 要的是数组，你给的是字符串"

    del payload["retry_hint"]  # 老行：没有这个键 → None（不编提示）
    assert restored(payload).retry_hint is None


def test_take_retry_hint_hands_it_over_exactly_once():
    """取走即清——不然陈年错误会被后面每个动作当成校验错误递进去。"""

    round_ = make_round()
    round_.fail("整合两次都过不了校验：…", hint="上一版报错")

    assert round_.take_retry_hint() == "上一版报错"
    assert round_.take_retry_hint() is None
    assert round_.retry_hint is None


def test_a_gateway_failure_has_no_hint():
    """网关那类失败（超时 / 5xx）对模型没有信息量，不递。"""

    round_ = make_round()
    round_.fail("模型调用失败：ReadTimeout")

    assert round_.retry_hint is None


# —— C4：确认时记下"它提议的"给了几条、收了几条（P14）——


def proposal_draft(proposal_id: str, *, origin: OriginType) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId(f"r1#{proposal_id[-2:]}"),
        op=WriteOp.ADD,
        target=Target.body("s2"),
        text=f"{proposal_id} 的内容",
        origin=Origin(origin, (EvidenceId("E1"),), "理由"),
        kind=BlockKind.TEXT,
    )


def round_with_a_draft(*, round_id: str = "r1", seat_round_count: int = 1) -> Round:
    """一轮走到草案卡：草案里 1 条"它提议的" + 1 条"它替你判断的" + 1 条"你说过的"。"""

    round_ = Round.start(
        round_id=RoundId(round_id), project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=RoundId(round_id),
            proposals=(
                proposal_draft("p01", origin=OriginType.USER),
                proposal_draft("p02", origin=OriginType.DERIVED),
                proposal_draft("p03", origin=OriginType.PROPOSED),
            ),
        ),
        dropped=(),
    )
    return round_


def test_confirming_records_how_many_proposals_were_offered_and_accepted():
    round_ = round_with_a_draft()
    round_.confirm(7, proposed_offered=1, proposed_accepted=0)

    assert round_.proposed_offered == 1
    assert round_.proposed_accepted == 0
    assert round_.confirmed_version_no == 7
    assert round_.step is Step.DONE


def test_a_round_that_never_confirmed_has_no_tally():
    round_ = round_with_a_draft()

    assert round_.proposed_offered is None
    assert round_.proposed_accepted is None


def test_payload_roundtrip_keeps_the_tally_and_old_rows_fall_back():
    round_ = round_with_a_draft()
    round_.confirm(7, proposed_offered=3, proposed_accepted=1)

    payload = round_to_payload(round_)
    assert restored(payload).proposed_offered == 3
    assert restored(payload).proposed_accepted == 1

    del payload["proposed_offered"]  # 老行：没有这两把键 → None（不编 0）
    del payload["proposed_accepted"]
    assert restored(payload).proposed_offered is None
    assert restored(payload).proposed_accepted is None


def test_payload_roundtrip_keeps_the_ignore_streak_and_old_rows_default_to_zero():
    round_ = round_with_a_draft()
    round_.remember_proposal_ignore_streak(3)

    payload = round_to_payload(round_)
    assert restored(payload).proposal_ignore_streak == 3

    del payload["proposal_ignore_streak"]  # 老行：没有这个键 → 0（不提示）
    assert restored(payload).proposal_ignore_streak == 0
