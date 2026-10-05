"""探索聚合（调研）的不变量 X1–X4 与状态机行为。

X1 假设必须可证伪；X2 一趟产物有上限；X3 候选状态单向且幂等；X4 运行状态转换合法。
"""

from datetime import UTC, datetime

import pytest

from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.item import (
    MAX_INQUIRY_BLIND_SPOTS,
    MAX_INQUIRY_HYPOTHESES,
    BlindSpot,
    CandidateState,
    Grounding,
    Hypothesis,
)
from pmstudio.domain.shared.errors import (
    CandidateNotReviewable,
    DuplicateCandidateId,
    IllegalExplorationTransition,
    ShapeError,
    TooManyInquiryItems,
    UnfalsifiableHypothesis,
)
from pmstudio.domain.shared.ids import EvidenceId, ExplorationId, ProjectId

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def start() -> Exploration:
    return Exploration.start(
        exploration_id=ExplorationId("x-1"),
        project_id=ProjectId("p-1"),
        scope=ExplorationScope(kind=ScopeKind.DOCUMENT),
        now=NOW,
    )


def hypo(local_id: str = "H1", **over: object) -> Hypothesis:
    fields: dict[str, object] = {
        "local_id": local_id,
        "statement": "运营做日报时其实是按客户名找单，不是按订单号",
        "grounding": Grounding.INVENTED,
        "based_on": (),
        "falsification": "访谈 5 个运营，若没人这么找单据即被推翻",
        "evidence_needed": "至少两个真实运营的访谈记录",
    }
    fields.update(over)
    return Hypothesis(**fields)  # type: ignore[arg-type]


def blind(local_id: str = "B1") -> BlindSpot:
    return BlindSpot(
        local_id=local_id,
        statement="客户合并后订单归属谁，模拟答不了",
        next_step="去问数据组：合并客户的订单到底挂在哪个 ID 上",
    )


def done(*, hypotheses: tuple[Hypothesis, ...] | None = None) -> Exploration:
    run = start()
    run.complete(hypotheses=hypotheses or (hypo(),), blind_spots=(blind(),))
    return run


# —— X1：假设必须可证伪 ——


def test_X1_ok_hypothesis_with_falsification() -> None:
    item = hypo()
    run = done(hypotheses=(item,))
    assert run.candidates()[0].falsification == item.falsification


def test_X1_bad_hypothesis_without_falsification() -> None:
    with pytest.raises(UnfalsifiableHypothesis):
        hypo(falsification="   ")


def test_X1_bad_hypothesis_without_evidence_needed() -> None:
    with pytest.raises(UnfalsifiableHypothesis):
        hypo(evidence_needed="")


def test_hypothesis_grounding_rules() -> None:
    with pytest.raises(ShapeError):
        hypo(grounding=Grounding.INVENTED, based_on=(EvidenceId("E1"),))
    with pytest.raises(ShapeError):
        hypo(grounding=Grounding.DOCUMENT, based_on=())
    grounded = hypo(
        grounding=Grounding.DOCUMENT, based_on=(EvidenceId("E2"),)
    )
    assert grounded.based_on == (EvidenceId("E2"),)


def test_blind_spot_needs_next_step() -> None:
    with pytest.raises(ShapeError):
        BlindSpot(local_id="B1", statement="不知道", next_step=" ")


# —— X2：产物上限 ——


def test_X2_ok_at_caps() -> None:
    run = start()
    run.complete(
        hypotheses=tuple(hypo(f"H{i}") for i in range(1, MAX_INQUIRY_HYPOTHESES + 1)),
        blind_spots=tuple(blind(f"B{i}") for i in range(1, MAX_INQUIRY_BLIND_SPOTS + 1)),
    )
    assert len(run.hypotheses) == MAX_INQUIRY_HYPOTHESES


def test_X2_bad_too_many_hypotheses() -> None:
    run = start()
    with pytest.raises(TooManyInquiryItems):
        run.complete(
            hypotheses=tuple(
                hypo(f"H{i}") for i in range(1, MAX_INQUIRY_HYPOTHESES + 2)
            ),
            blind_spots=(),
        )


def test_X2_bad_too_many_blind_spots() -> None:
    run = start()
    with pytest.raises(TooManyInquiryItems):
        run.complete(
            hypotheses=(),
            blind_spots=tuple(
                blind(f"B{i}") for i in range(1, MAX_INQUIRY_BLIND_SPOTS + 2)
            ),
        )


def test_duplicate_local_id_rejected() -> None:
    run = start()
    with pytest.raises(DuplicateCandidateId):
        run.complete(hypotheses=(hypo("X1"),), blind_spots=(blind("X1"),))


def test_review_before_done_rejected() -> None:
    run = start()
    with pytest.raises(CandidateNotReviewable):
        run.keep("H1", as_="seed")


# —— X3：候选状态单向且幂等 ——


def test_X3_ok_keep_is_idempotent() -> None:
    run = done()
    run.keep("H1", as_="seed")
    run.keep("H1", as_="seed")  # 幂等：同去向重复收下不报错
    assert run.candidates()[0].kept_as == "seed"


def test_X3_bad_change_destination_after_keep() -> None:
    run = done()
    run.keep("H1", as_="seed")
    with pytest.raises(CandidateNotReviewable):
        run.keep("H1", as_="material", material_id="m-1")


def test_X3_bad_keep_after_dismiss() -> None:
    run = done()
    run.dismiss("H1")
    with pytest.raises(CandidateNotReviewable):
        run.keep("H1", as_="seed")


def test_X3_bad_dismiss_after_keep() -> None:
    run = done()
    run.keep("H1", as_="seed")
    with pytest.raises(CandidateNotReviewable):
        run.dismiss("H1")


def test_dismiss_is_idempotent_and_terminal() -> None:
    run = done()
    run.dismiss("B1")
    run.dismiss("B1")
    assert run.candidates()[1].state is CandidateState.DISMISSED


def test_material_keep_needs_material_id() -> None:
    run = done()
    with pytest.raises(ShapeError):
        run.keep("H1", as_="material")


def test_unknown_candidate_rejected() -> None:
    run = done()
    with pytest.raises(ShapeError):
        run.dismiss("H9")


# —— X4：运行状态机 ——


def test_X4_ok_fail_then_resume() -> None:
    run = start()
    run.fail("模型两次都没给出合格报文")
    assert run.status is ExplorationStatus.FAILED
    run.resume()
    assert run.status is ExplorationStatus.RUNNING
    run.complete(hypotheses=(hypo(),), blind_spots=())
    assert run.status is ExplorationStatus.DONE


def test_X4_bad_complete_after_failed() -> None:
    run = start()
    run.fail("坏了")
    with pytest.raises(IllegalExplorationTransition):
        run.complete(hypotheses=(hypo(),), blind_spots=())


def test_X4_bad_complete_twice() -> None:
    run = done()
    with pytest.raises(IllegalExplorationTransition):
        run.complete(hypotheses=(hypo(),), blind_spots=())


def test_X4_bad_resume_when_running() -> None:
    run = start()
    with pytest.raises(IllegalExplorationTransition):
        run.resume()


def test_fail_records_first_reason_only_and_is_idempotent() -> None:
    run = start()
    run.fail("第一次的原因")
    run.fail("第二次的原因")
    assert run.failure is not None
    assert run.failure.reason == "第一次的原因"


def test_abandon_is_terminal_and_idempotent() -> None:
    run = start()
    run.abandon("用户放弃了这一趟")
    run.abandon("再点一次")
    assert run.status is ExplorationStatus.ABANDONED
    assert run.abandon_reason == "用户放弃了这一趟"


def test_entry_scope_needs_ref() -> None:
    with pytest.raises(ShapeError):
        ExplorationScope(kind=ScopeKind.ENTRY)

    ok = ExplorationScope(kind=ScopeKind.ENTRY, ref="entry-ab12")
    assert ok.ref == "entry-ab12"
