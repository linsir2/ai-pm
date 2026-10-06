from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from pmstudio.domain.provenance.evidence import Evidence, EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.errors import (
    DerivedFromRequired,
    DuplicateEvidenceId,
    EvidenceSourceShape,
    SelfDerivation,
    UnknownEvidence,
)
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, QuestionId, RoundId
from pmstudio.domain.shared.invariants import EInvariant

NOW = datetime(2026, 9, 22, 10, 0, 0)


def empty_ledger() -> EvidenceLedger:
    return EvidenceLedger.empty(ProjectId("p1"))


def record_user_input(
    ledger: EvidenceLedger, text: str = "运营每天都要用", *, eid: EvidenceId | None = None
) -> EvidenceId:
    return ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text=text,
        source=EvidenceSource(round_id=RoundId("r1"), raw_span=text),
        now=NOW,
        eid=eid,
    )


def record_derived(
    ledger: EvidenceLedger, *, basis: tuple[EvidenceId, ...], text: str = "单次导出 60 秒内"
) -> EvidenceId:
    return ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text=text,
        source=EvidenceSource(
            seat_id="seat.data_scale",
            derived_from=basis,
            rationale="量级在 1 万级，同步实现可满足",
        ),
        now=NOW,
    )


def test_E1_ok_numbers_grow():
    ledger = empty_ledger()
    ids = [record_user_input(ledger, f"第{n}句") for n in (1, 2, 3)]
    assert ids == [EvidenceId("E1"), EvidenceId("E2"), EvidenceId("E3")]
    assert len(ledger) == 3


def test_E1_bad_duplicate_eid():
    ledger = empty_ledger()
    record_user_input(ledger, "第一句", eid=EvidenceId("E1"))
    with pytest.raises(DuplicateEvidenceId) as exc:
        record_user_input(ledger, "又一句", eid=EvidenceId("E1"))
    assert exc.value.invariant == EInvariant.E1


def test_E1_bad_gap_is_not_reused_after_rehydrate():
    evidences = tuple(
        Evidence.build(
            eid=EvidenceId(f"E{n}"),
            kind=EvidenceKind.USER_INPUT,
            text=f"第{n}句",
            source=EvidenceSource(round_id=RoundId("r1"), raw_span=f"第{n}句"),
            now=NOW,
        )
        for n in (1, 2, 5)
    )
    ledger = EvidenceLedger.rehydrate(project_id=ProjectId("p1"), evidences=evidences)
    assert record_user_input(ledger, "新的") == EvidenceId("E6")


def test_E2_ok_revise_appends_and_supersedes():
    ledger = empty_ledger()
    original = record_user_input(ledger, "量级几万")
    revised = ledger.revise(original, text="量级 3 万（用户订正）", now=NOW)
    assert revised == EvidenceId("E2")
    assert ledger.get(original).text == "量级几万"
    assert ledger.get(revised).supersedes == original
    assert ledger.superseded_by(original) == revised
    assert ledger.is_superseded(original) is True


def test_E2_bad_evidence_is_frozen():
    ledger = empty_ledger()
    original = record_user_input(ledger)
    with pytest.raises(FrozenInstanceError):
        ledger.get(original).text = "改历史"  # type: ignore[misc]
    assert not hasattr(ledger, "rewrite")


def test_E3_ok_derived_with_existing_basis():
    ledger = empty_ledger()
    basis = record_user_input(ledger)
    derived = record_derived(ledger, basis=(basis,))
    assert ledger.get(derived).source.derived_from == (basis,)


def test_E3_bad_derived_without_basis():
    ledger = empty_ledger()
    with pytest.raises(DerivedFromRequired) as exc:
        record_derived(ledger, basis=())
    assert exc.value.invariant == EInvariant.E3


def test_E3_bad_unknown_basis():
    ledger = empty_ledger()
    with pytest.raises(UnknownEvidence) as exc:
        record_derived(ledger, basis=(EvidenceId("E99"),))
    assert exc.value.invariant == EInvariant.E3


def test_E3_bad_self_derivation():
    ledger = empty_ledger()
    with pytest.raises(SelfDerivation) as exc:
        record_derived(ledger, basis=(EvidenceId("E1"),))
    assert exc.value.invariant == EInvariant.E3


@pytest.mark.parametrize(
    ("kind", "source"),
    [
        (EvidenceKind.CARD_ANSWER, EvidenceSource(round_id=RoundId("r1"))),
        (EvidenceKind.AGENT_PROPOSED, EvidenceSource(rationale="纯专业判断")),
        (EvidenceKind.SYSTEM_DEFAULT, EvidenceSource(round_id=RoundId("r1"))),
        (EvidenceKind.EXTERNAL, EvidenceSource()),
        # M8：探索产物必须指回是哪一条（external_ref = exploration:{id}#{候选}）
        (EvidenceKind.SIMULATED, EvidenceSource()),
    ],
)
def test_source_shape_per_kind(kind: EvidenceKind, source: EvidenceSource):
    ledger = empty_ledger()
    with pytest.raises(EvidenceSourceShape):
        ledger.record(kind=kind, text="内容", source=source, now=NOW)


def test_card_answer_needs_question_id():
    ledger = empty_ledger()
    eid = ledger.record(
        kind=EvidenceKind.CARD_ANSWER,
        text="运营专员 + 主管",
        source=EvidenceSource(round_id=RoundId("r1"), question_id=QuestionId("q3")),
        now=NOW,
    )
    assert ledger.get(eid).source.question_id == QuestionId("q3")


def test_simulated_evidence_records_and_is_not_terminal():
    """探索产物**不是**可追溯终点：只引它的提案会被装配层降级为 proposed（M8）。

    这条与 `test_manual_edit_is_a_terminal_source` 成对：终点集合只能含
    user_input / card_answer / manual——模拟推演永远不是"人能负责"的来源。
    """

    from pmstudio.domain.shared.evidence_kind import TERMINAL_KINDS

    ledger = empty_ledger()
    eid = ledger.record(
        kind=EvidenceKind.SIMULATED,
        text="运营做日报时按客户名找单（推演，未验证）",
        source=EvidenceSource(external_ref="exploration:x-1#H1"),
        now=NOW,
    )

    assert ledger.get(eid).kind is EvidenceKind.SIMULATED
    assert EvidenceKind.SIMULATED not in TERMINAL_KINDS


def test_manual_edit_is_a_terminal_source():
    """手改的内容也算"用户直接给的"：不要求来源字段，并且是可追溯链的终点。

    依据：`04-未决.md` D1（手改内容如何获得出处）+ `01-规格.md` §5
    （卡片改的与文档改的谁优先——手改过的块 AI 不能再动）。
    """

    from pmstudio.domain.shared.evidence_kind import TERMINAL_KINDS

    ledger = empty_ledger()
    eid = ledger.record(
        kind=EvidenceKind.MANUAL,
        text="手工改写：把订单按时间筛出来导出成表格",
        source=EvidenceSource(),
        now=NOW,
    )

    assert ledger.get(eid).kind is EvidenceKind.MANUAL
    assert EvidenceKind.MANUAL in TERMINAL_KINDS


def test_chain_walks_derived_from_and_dedups():
    ledger = empty_ledger()
    user = record_user_input(ledger)
    first = record_derived(ledger, basis=(user,), text="判断一")
    second = record_derived(ledger, basis=(user, first), text="判断二")
    chain = ledger.chain(second)
    assert [item.eid for item in chain] == [second, first, user]
def test_find_by_local_can_be_scoped_to_one_round():
    """真跑抓到（P22 回归）：两轮的 `p1:O1` 会撞名，只按 (席位, 条目) 找会串到上一轮。

    带 `round_id` 时必须精确命中那一轮；老行（没有 `round_id`）当兜底——
    老文档里的判断照旧查得到，不许因为修这个洞让历史轮次变成"查不到编号"。
    """

    ledger = empty_ledger()
    user = record_user_input(ledger)
    old = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="第一轮：导出要分页",
        source=EvidenceSource(
            seat_id="seat.x", local_id="p1:O1", derived_from=(user,), rationale="同类做法"
        ),
        now=NOW,
    )
    new = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="第二轮：导出要脱敏",
        source=EvidenceSource(
            seat_id="seat.x",
            local_id="p1:O1",
            round_id=RoundId("r-two"),
            derived_from=(user,),
            rationale="合规要求",
        ),
        now=NOW,
    )

    assert ledger.find_by_local(seat_id="seat.x", local_id="p1:O1").eid == old
    assert (
        ledger.find_by_local(seat_id="seat.x", local_id="p1:O1", round_id=RoundId("r-two")).eid
        == new
    )
    assert (
        ledger.find_by_local(seat_id="seat.x", local_id="p1:O1", round_id=RoundId("r-one")).eid
        == old
    )


def test_find_by_local_strict_mode_refuses_the_legacy_fallback():
    """写入端那条路要严格：老行（没有 `round_id`）**不许**让新的一轮复用它的编号——
    真跑里就是这么串的号。读取端才需要那条兜底。
    """

    ledger = empty_ledger()
    user = record_user_input(ledger)
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="第一轮：导出要分页",
        source=EvidenceSource(
            seat_id="seat.x", local_id="p1:O1", derived_from=(user,), rationale="同类做法"
        ),
        now=NOW,
    )

    assert (
        ledger.find_by_local(
            seat_id="seat.x",
            local_id="p1:O1",
            round_id=RoundId("r-two"),
            allow_legacy=False,
        )
        is None
    )


def record_material(
    ledger: EvidenceLedger, text: str = "讨论追问", *, ref: str = "discussion:d-1#C1"
) -> EvidenceId:
    return ledger.record(
        kind=EvidenceKind.SIMULATED,
        text=text,
        source=EvidenceSource(external_ref=ref),
        now=NOW,
    )


def test_user_input_may_cite_material_chunks():
    """主张读过材料：user_input 可以带 derived_from，但只能是材料 kind。"""

    ledger = empty_ledger()
    material = record_material(ledger)
    claim = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="按材料说的导出",
        source=EvidenceSource(
            round_id=RoundId("r1"), raw_span="按材料说的", derived_from=(material,)
        ),
        now=NOW,
    )
    assert ledger.get(claim).source.derived_from == (material,)
    assert material in [item.eid for item in ledger.chain(claim)]


def test_user_input_citing_a_judgment_is_still_refused():
    """墙还在：引席位的判断（非材料）照旧拒绝——根必须留在用户手里或材料本身。"""

    ledger = empty_ledger()
    material = record_material(ledger)
    judgment = record_derived(ledger, basis=(material,), text="同步导出够快")
    with pytest.raises(EvidenceSourceShape, match="只能引用材料块"):
        ledger.record(
            kind=EvidenceKind.USER_INPUT,
            text="按判断写",
            source=EvidenceSource(
                round_id=RoundId("r1"), raw_span="按判断写", derived_from=(judgment,)
            ),
            now=NOW,
        )


def test_user_input_citing_unknown_evidence_is_refused():
    ledger = empty_ledger()
    with pytest.raises(UnknownEvidence):
        ledger.record(
            kind=EvidenceKind.USER_INPUT,
            text="按空气写",
            source=EvidenceSource(
                round_id=RoundId("r1"),
                raw_span="按空气写",
                derived_from=(EvidenceId("E99"),),
            ),
            now=NOW,
        )


def test_other_kinds_still_refuse_derived_from():
    """user_input 之外的口子没开：system_default 带 derived_from 照旧是形状错误。"""

    ledger = empty_ledger()
    material = record_material(ledger)
    with pytest.raises(EvidenceSourceShape):
        ledger.record(
            kind=EvidenceKind.SYSTEM_DEFAULT,
            text="系统默认",
            source=EvidenceSource(
                round_id=RoundId("r1"),
                rationale="结构规则",
                derived_from=(material,),
            ),
            now=NOW,
        )
