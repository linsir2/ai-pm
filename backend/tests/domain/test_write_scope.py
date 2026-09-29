"""写范围（本轮去处）的粒度：功能字段放宽到**功能级**（2026-09-29 作者拍板 D1）。

主张点了 `s5/order-export.one_liner`，整合者写 `s5/order-export.goal` 不该被标成"跑出本轮去处"：
P8 的提示词明确要求"已授权功能要把内容落到功能字段"（`02` §11.0.9 口径三），
写范围却只认那一个字段，自己跟自己打架——真跑里一轮刷出 20 条 advisory，把置顶告警挤没了。

但**章节槽不放宽**：说"兼容性"不等于授权"安全"（`s8.compatibility` → `s8.security` 仍然要报）。
写范围只是**提示**通道（`02` §3①：跑出本轮去处只标注、不拒绝），
不得新增未授权功能子树那条硬约束在别处（`assembly` 的 `pending_features` + `Document.apply`）。
"""

from pmstudio.domain.round.draft import Origin, OriginType, Proposal
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
from pmstudio.domain.services.scope_guard import OUTSIDE_ROUND_SCOPE, ScopeGuard
from pmstudio.domain.shared.ids import EntryId, EvidenceId, FormId, ProposalId, QuestionId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim


def round_with_claim(target: Target) -> Round:
    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1"), target=target),))
    round_.submit_opening(seats=(SEAT,))
    return round_


def proposal(target: Target, index: int = 1) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(f"p{index:02d}"),
        entry_id=EntryId(f"r1#{index:02d}"),
        op=WriteOp.ADD,
        target=target,
        text="内容",
        origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
        kind=BlockKind.TEXT,
    )


def violations(round_: Round, *targets: Target):  # type: ignore[no-untyped-def]
    return ScopeGuard().check(
        [proposal(target, index) for index, target in enumerate(targets, start=1)],
        write_scope=round_.write_scope,
    )


def test_a_feature_field_claim_covers_the_whole_feature():
    """主张点了功能的一个字段 ⇒ 这个功能的其它字段也算在范围内（D1 的核心）。"""

    round_ = round_with_claim(Target.feature_field("order-export", "one_liner"))

    assert violations(round_, Target.feature_field("order-export", "goal")) == ()


def test_another_feature_is_still_outside_the_round():
    """放宽只对**同一个功能**生效：别的功能没被点名，照样报。"""

    round_ = round_with_claim(Target.feature_field("order-export", "one_liner"))

    result = violations(round_, Target.feature_field("other-feature", "name"))

    assert [item.code for item in result] == [OUTSIDE_ROUND_SCOPE]


def test_a_section_slot_claim_does_not_widen_the_section():
    """章节槽不放宽：说兼容性不等于授权安全。"""

    round_ = round_with_claim(Target.body("s8", field="compatibility"))

    result = violations(round_, Target.body("s8", field="security"))

    assert [item.code for item in result] == [OUTSIDE_ROUND_SCOPE]


def test_a_section_level_claim_still_covers_the_whole_section():
    """章节级（不给 field）本来就覆盖整章——别被 D1 改坏。"""

    round_ = round_with_claim(Target.body("s2"))

    assert violations(round_, Target.body("s2")) == ()


def question(question_id: str, target: Target) -> Question:
    return Question(
        question_id=QuestionId(question_id),
        target=target,
        type=QuestionType.SINGLE,
        title="这面怎么定？",
        why="缺一个只有你能给的信息",
        consequence_if_skipped="按默认处理",
        options=(Option("a", "甲"), Option("b", "乙")),
    )


def round_with_two_batches() -> Round:
    """第一批问了 `s5` 并答了；第二批问的是 `s8`（还没答）。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1"), target=Target.body("s2")),))
    round_.submit_opening(seats=(SEAT,))
    round_.attest(SeatReview(seat_id=SEAT, basis="读了 E1", opinions=()))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=ROUND_ID,
            questions=(question("q1", Target.body("s5")),),
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
            questions=(question("q2", Target.body("s8")),),
        )
    )
    return round_


def test_an_answered_questions_target_comes_from_its_own_batch():
    """第一批答过的题的去处要进写范围；第二批**只是问了、还没答**的不进。

    老实现按裸题号去**当前**问卷里反查：第一批的 `q1` 在第二批的问卷里根本不存在，
    于是第一批的去处丢了、第二批的去处混进来（计划 §0.2 探针 2）。
    """

    labels = {target.label() for target in round_with_two_batches().write_scope}

    assert "s2" in labels  # 主张
    assert "s5" in labels  # 第一批答过的那道题的去处
    assert "s8" not in labels  # 第二批还没答
