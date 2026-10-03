"""eval 的断言本身要能离线测：不然"提示词没退化"这个结论就站不住（`02` §5.4）。

真模型那条路（`scripts/eval.py`）只负责把一轮跑出来；判断全在这里，纯函数。
"""

from dataclasses import replace

from evals.assertions import EVAL_ASSERTIONS, check

from pmstudio.application import dto
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import (
    AnswerSet,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import DroppedItem, DropSource, Round
from pmstudio.domain.round.web_query import WebQuery
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProposalId,
    QuestionId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, ROUND_ID, SEAT, make_claim, make_round


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


def round_with_draft(*, out_of_scope: bool) -> Round:
    """一张草案卡：`p02` 要么落在本轮去处里（s5），要么跑到 s8（席位替你判断的）。"""

    round_ = make_round()
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND_ID,
            proposals=(
                Proposal(
                    proposal_id=ProposalId("p02"),
                    entry_id=EntryId("r1#02"),
                    op=WriteOp.ADD,
                    target=Target.body("s8") if out_of_scope else Target.body("s5"),
                    text="单次导出 60 秒内完成",
                    origin=Origin(OriginType.DERIVED, (EvidenceId("E9"),), "同类导出的常见上限"),
                    kind=BlockKind.NFR,
                ),
            ),
        )
    )
    return round_


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


def test_the_same_question_asked_twice_is_a_duplicate():
    """真重复 = 同一去处 + **同一问法**（这里 helper 的标题是固定的，所以算重复）。"""

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


def test_two_different_questions_on_the_same_place_are_allowed():
    """同一去处上的**两件不同的事**照问，不算重复（2026-09-28 作者拍板）。

    真模型实测（第四轮完整 eval 的 `case-a`）：`s8.availability` 上出过
    "幂等性与重试策略" 与 "对端不可用时的降级策略" 两道题——各自有选项与跳过后果，
    合并会丢结构、丢掉任何一道都是把用户该填的题吞了。旧判据（只看去处）把它们判成重复，
    三次复发（`edge-one-constraint` / `edge-long` / `case-a`）。
    """

    same_place = [
        question("q1", target=Target.body("s8")),
        question("q2", target=Target.body("s8")),
    ]
    same_place[1] = replace(same_place[1], title="对端不可用时的降级策略确认")

    assert check({"type": "no_duplicate_questions"}, view_of(round_with(same_place))) is None


def test_out_of_scope_items_are_labeled_when_both_surfaces_agree():
    """跑出本轮去处的条目**允许存在**（`02` §3①），但卡片与顶部提示必须一致地标出来。"""

    view = view_of(round_with_draft(out_of_scope=True))

    assert view.advisories  # 顶部有提示
    assert check({"type": "outside_scope_items_are_labeled"}, view) is None


def test_out_of_scope_items_are_labeled_catches_a_silent_accept():
    """顶部提示没了（卡片上还标着）→ 两处漂移，判失败。"""

    view = view_of(round_with_draft(out_of_scope=True))

    failure = check({"type": "outside_scope_items_are_labeled"}, replace(view, advisories=()))

    assert failure is not None
    assert "标注" in failure


def test_out_of_scope_items_are_labeled_ignores_a_round_inside_its_scope():
    view = view_of(round_with_draft(out_of_scope=False))

    assert view.advisories == ()
    assert check({"type": "outside_scope_items_are_labeled"}, view) is None


def round_whose_claims_name_a_feature(*, proposal_target: Target) -> Round:
    """主张点名了一个功能、草案却落在别处：同时触发"置顶告警"与"跑出本轮去处"。"""

    round_ = Round.start(
        round_id=ROUND_ID, project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims(
        (
            make_claim(
                eid=EvidenceId("E1"),
                target=Target.feature_field(FeatureSlug("order-export"), "one_liner"),
            ),
        )
    )
    round_.submit_opening(seats=(SEAT,))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND_ID,
            proposals=(
                Proposal(
                    proposal_id=ProposalId("p01"),
                    entry_id=EntryId("r1#01"),
                    op=WriteOp.ADD,
                    target=proposal_target,
                    text="现状：导出靠手工复制",
                    origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
                    kind=BlockKind.TEXT,
                ),
            ),
        )
    )
    return round_


def test_the_two_surfaces_still_agree_when_the_zero_feature_advisory_is_also_there():
    """置顶告警（零功能候选）不挂在任何提案上，不许把它算进"顶部提示数"（2026-09-29 修）。

    真跑前发现：`feature_coverage_advisory` 只出现在"草案里一条 s5 都没有"时，
    而那一刻草案里的章节级条目很可能正好越界——两条提示同时出现就假红，判据却跟提示词没关系。
    """

    view = view_of(round_whose_claims_name_a_feature(proposal_target=Target.body("s1")))

    assert len(view.advisories) == 2  # 置顶告警 + 一条越界提示
    assert check({"type": "outside_scope_items_are_labeled"}, view) is None


def test_a_contradiction_must_be_labelled_conflict_or_decision():
    """矛盾题与缺信息题在界面上分开显示（`document/status.ts`），所以判据要能看出 kind。

    P11 C3：`conflict` 与 `decision` 在**前端与就绪度里等价**（都算"要你拍板"）——
    模型把"两侧互斥"落成"在两个方案里选一个"（`decision`）是合理读法（真跑试点 q1 就是这样）。
    真正要抓的失败模式是"把它当普通缺信息题、只出 `gap`"。这条断言挂在 `06-edge-contradiction` 上。
    """

    gap = question("q1", target=Target.body("s8"))
    as_conflict = replace(gap, kind=QuestionKind.CONFLICT, title="到底按量级还是按耗时？")
    as_decision = replace(gap, kind=QuestionKind.DECISION, title="到底按量级还是按耗时？")

    only_gap = view_of(round_with([gap]))
    assert check({"type": "contradiction_is_labelled"}, only_gap) is not None
    assert check({"type": "contradiction_is_labelled"}, view_of(round_with([as_conflict]))) is None
    assert check({"type": "contradiction_is_labelled"}, view_of(round_with([as_decision]))) is None


def round_with_two_batches(*, first_kind: QuestionKind) -> Round:
    """两批问卷：第 1 批一道题（kind 由调用方定），用户跳过；第 2 批一道 gap 题。

    真跑现场（P30 那次完整 eval 的第 6 条用例，轮 `r-8889fcf0`）：
    `q4 kind=conflict`（依据 E1+E3）出现在**第 1 批**，用户跳过之后第 2 批只剩
    `q6`/`q7` 两道 gap 题。`RoundView.questions` 只有当前批（第 2 批）——
    「互斥题有没有出」这件事因此从判据里消失了。
    """

    round_ = make_round()
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f-1"),
            round_id=ROUND_ID,
            questions=(replace(question("q1", target=Target.body("s5")), kind=first_kind),),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f-1"),
            answers=(),
            skipped=(QuestionId("q1"),),
            answered_at=NOW,
        )
    )
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f-2"),
            round_id=ROUND_ID,
            questions=(question("q2", target=Target.body("s7")),),
        )
    )
    return round_


def test_a_conflict_asked_in_an_earlier_batch_still_counts():
    """判据要看**整轮问过的题**：互斥题在第 1 批出过，就不该因为第 2 批全是 gap 而判红。

    P30 那次 `edge-contradiction` 的唯一一红就是这么来的：判据只读当前批。
    """

    view = view_of(round_with_two_batches(first_kind=QuestionKind.CONFLICT))

    assert check({"type": "contradiction_is_labelled"}, view) is None


def test_a_round_that_only_ever_asked_gaps_still_fails():
    """反例：两批全是 gap —— 判据必须照旧红。

    改的是取样面（当前批 → 整轮），**不是**把判据改松：真·没出互斥题时还得能抓住。
    """

    view = view_of(round_with_two_batches(first_kind=QuestionKind.GAP))

    assert check({"type": "contradiction_is_labelled"}, view) is not None


def test_web_nomination_is_required_when_the_input_needs_external_facts():
    """M3b：输入要的是外部客观事实时，分析者至少要提名一条检索词。

    判据本身是纯函数（`view.web_queries` 进），所以"该查的不查"能离线钉住；
    真模型那侧由 `09-edge-external-facts` 那条用例负责。
    """

    assert (
        check({"type": "web_queries_at_least", "count": 1}, view_of(make_round())) is not None
    )

    round_ = make_round()
    round_.attach_web_queries(
        (
            WebQuery(
                query="21 CFR Part 11 电子签名与审计追踪要求",
                why="法规条款原文，文档里没有、用户也答不了",
            ),
        )
    )
    assert check({"type": "web_queries_at_least", "count": 1}, view_of(round_)) is None
    # 提了一条但要求两条时照旧红（判据比的是数，不是"有没有非空"）
    assert check({"type": "web_queries_at_least", "count": 2}, view_of(round_)) is not None


def round_with_one_proposal(*, target: Target, kind: BlockKind) -> Round:
    """一张只带一条提案的草案卡（M4 的断言看的就是它）。"""

    round_ = make_round()
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND_ID,
            proposals=(
                Proposal(
                    proposal_id=ProposalId("p01"),
                    entry_id=EntryId("r1#01"),
                    op=WriteOp.ADD,
                    target=target,
                    text="1. 点检员点检；2. 发现异常报修；3. 主管派单。",
                    origin=Origin(OriginType.DERIVED, (EvidenceId("E1"),), "席位推导"),
                    kind=kind,
                ),
            ),
        )
    )
    return round_


def test_draft_section_kind_reads_the_draft_card():
    """M4：第 6 章那条判据看的是"草案里有没有落在 s6 的 steps"。

    真跑背景（P30 五条车道 449 条提案）：`kind=steps` 13 条全落在 `s5/*.main_flow`，
    落 `s6` 的只有 1 条且是 `text`——于是那一章永远空着。这条断言把"派活之后真写了没"钉住。
    """

    assertion = {"type": "draft_section_has_kind", "section": "s6", "kind": "steps"}

    good = view_of(round_with_one_proposal(target=Target.body("s6"), kind=BlockKind.STEPS))
    assert check(assertion, good) is None

    # 落在别处（功能字段的主流程）不算数：那一章还是空的
    wrong_place = view_of(
        round_with_one_proposal(
            target=Target.feature_field(FeatureSlug("order-export"), "main_flow"),
            kind=BlockKind.STEPS,
        )
    )
    failure = check(assertion, wrong_place)
    assert failure is not None
    assert "s6" in failure

    # 落对了章节但块类型不对（普通正文）也不算
    wrong_kind = view_of(
        round_with_one_proposal(target=Target.body("s6"), kind=BlockKind.TEXT)
    )
    assert check(assertion, wrong_kind) is not None


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


def unaccounted(count: int) -> tuple[DroppedItem, ...]:
    return tuple(
        DroppedItem(
            handle=f"seat.a:O{index}",
            reason="量级：…（没被变成题，整合也没申报丢弃）",
            source=DropSource.UNACCOUNTED,
            seat_id="seat.a",
            pass_index=1,
        )
        for index in range(1, count + 1)
    )


def test_unaccounted_opinions_are_capped():
    """报告 F19：`unaccounted` 每轮 5–26 条，而它 48–100% 是假阳性（内容其实进了卡片）。

    判据只数 `source=unaccounted` 的条数——**不去猜**哪条"其实被用了"。
    阈值给 2 不给 0：这条判据的输入是模型行为，抖一下不该变成红灯；
    但要的是"降一个数量级"，不是"允许它继续每轮十几条"。
    """

    view = view_of(
        round_with(
            [question("q1", target=Target.body("s8"))], dropped=unaccounted(3)
        )
    )

    assert check({"type": "unaccounted_at_most", "count": 2}, view) is not None
    assert check({"type": "unaccounted_at_most", "count": 3}, view) is None
