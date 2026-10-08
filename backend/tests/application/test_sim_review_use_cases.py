"""模拟评测用例层（M11-T5）：跑一趟 —— 门控、参数、重装、第 6 本账、减提案落草案。

口径：只在草案卡跑；失败整趟作废（`Draft.gaps` 一行 + 抛出，runs 不增、可重跑）；
成功才记 artifact + 发 simulated 编号 + 追加减提案。
"""

import json

import pytest

from pmstudio.domain.authoring.document import WriteInstruction
from pmstudio.domain.provenance.evidence import EvidenceKind
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import ShapeError, TooManySimReviews
from pmstudio.domain.shared.ids import EntryId, EvidenceId, FeatureSlug
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.harness.errors import ModelCallFailed
from tests.application.test_sim_use_cases import (  # noqa: F401
    CLAIMS_REPLY,
    DRAFT_REPLY,
    SEAT_REPLY,
)
from tests.support.builders import NOW, PROJECT, SEAT, document_with_feature
from tests.support.harness import Harness

PERSONAS = (
    "persona.first_touch",
    "persona.edge_maker",
    "persona.bulk_operator",
)


def _document():  # type: ignore[no-untyped-def]
    document = document_with_feature()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-flow",
                entry_id=EntryId("en-flow"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "main_flow"),
                text="1. 进入订单列表\n2. 点导出\n3. 选时间范围",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.STEPS,
            ),
            WriteInstruction(
                instruction_id="i-prio",
                entry_id=EntryId("en-prio"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "priority"),
                text="P0",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            ),
            WriteInstruction(
                instruction_id="i-acc1",
                entry_id=EntryId("en-acc1"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "acceptance"),
                text="5 万条内 60 秒可下载",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.ACCEPTANCE,
            ),
            WriteInstruction(
                instruction_id="i-acc2",
                entry_id=EntryId("en-acc2"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "acceptance"),
                text="无权限不出现导出按钮",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.ACCEPTANCE,
            ),
        ],
        now=NOW,
        summary="补字段",
    )
    return document


def _packet(
    *,
    started: bool = True,
    entry_id: str = "en-flow",
    kind: str = "dead_end",
    verdict: str = "friction",
) -> str:
    frictions = [
        {
            "local_id": "F1",
            "kind": kind,
            "entry_id": entry_id,
            "field": "",
            "quote": "",
            "not_documented": True,
            "blocks_flow": True,
            "note": "点了导出没有反应；文档没写失败算不算正常",
        }
    ]
    return json.dumps(
        {
            "basis": "读了 s5/order-export 的主流程与验收",
            "features": [
                {
                    "feature_slug": "order-export",
                    "started": started,
                    "walkthrough": [
                        {
                            "step_index": 1,
                            "step_text": "进入订单列表",
                            "from_doc": "en-flow",
                            "action": "打开订单列表页",
                            "outcome": "stuck",
                            "need": "文档没写导出按钮在哪",
                        }
                    ]
                    if started
                    else [],
                    "frictions": frictions,
                    "acceptance_checks": [
                        {
                            "local_id": "A1",
                            "entry_id": "en-acc1",
                            "status": "unverified",
                            "note": "",
                        },
                        {
                            "local_id": "A2",
                            "entry_id": "en-acc2",
                            "status": "verified",
                            "note": "",
                        },
                    ],
                    "verdict": verdict,
                    "verdict_because": ["F1"],
                }
            ],
        },
        ensure_ascii=False,
    )


def _harness(replies: dict, persona_replies: dict) -> Harness:
    harness = Harness(
        replies={
            "claims": CLAIMS_REPLY,
            "integration": DRAFT_REPLY,
            **replies,
        },
        seat_replies={SEAT: SEAT_REPLY},
        persona_replies=persona_replies,
    )
    return harness


async def _round_on_draft_card(harness: Harness):  # type: ignore[no-untyped-def]
    harness.seed_project()
    harness.documents.seed(_document())
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)
    await harness.submit_opening()(round_id=round_id, seats=(SEAT,))
    await harness.run_seat_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    round_.authorize_new_feature(FeatureSlug("order-export"), basis=EvidenceId("E1"))
    harness.rounds.save(round_)
    await harness.integrate_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    assert round_.step is Step.AWAITING_CONFIRM
    assert round_.waiting_on is WaitingOn.DRAFT
    return round_id


def _all_good() -> dict:
    return {persona: _packet() for persona in PERSONAS}


def _run(harness: Harness):  # type: ignore[no-untyped-def]
    return harness.run_sim_review()


# ── R9：每份草案 ≤1 次（正例 / 反例 / dig 重置） ─────────────────────────────


async def test_R9_ok_one_run_per_draft_is_within_cap() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)

    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    assert harness.rounds.get(round_id).sim_review_runs == 1


async def test_R9_bad_second_run_is_rejected() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    calls_before = len(harness.gateway.requests)
    with pytest.raises(TooManySimReviews):
        await _run(harness)(
            round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
        )
    assert len(harness.gateway.requests) == calls_before


async def test_R9_ok_dig_clears_review_and_allows_a_fresh_run() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    await harness.dig_round()(round_id=round_id)
    round_ = harness.rounds.get(round_id)
    assert round_.sim_review is None and round_.sim_review_runs == 0


# ── 门控与参数 ──────────────────────────────────────────────────────────────


async def test_run_rejects_when_not_on_draft_card() -> None:
    from pmstudio.domain.shared.errors import IllegalStepTransition

    harness = _harness({}, _all_good())
    harness.seed_project()
    start = harness.start_round()
    round_id = await start.create(project_id=PROJECT, user_input="想做个订单导出的功能")
    await start.analyze(round_id=round_id)

    with pytest.raises(IllegalStepTransition):
        await _run(harness)(
            round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
        )


async def test_rejects_unknown_persona() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)

    with pytest.raises(ShapeError):
        await _run(harness)(
            round_id=round_id, persona_ids=("persona.nobody",), feature_slugs=("order-export",)
        )


async def test_rejects_too_many_personas() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)

    with pytest.raises(ShapeError):
        await _run(harness)(
            round_id=round_id,
            persona_ids=(
                "persona.first_touch",
                "persona.edge_maker",
                "persona.interrupted",
                "persona.boundary_walker",
            ),
            feature_slugs=("order-export",),
        )


async def test_rejects_unknown_feature() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)

    with pytest.raises(ShapeError):
        await _run(harness)(
            round_id=round_id, persona_ids=PERSONAS, feature_slugs=("nope",)
        )


async def test_rejects_empty_selection() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)

    with pytest.raises(ShapeError):
        await _run(harness)(round_id=round_id, persona_ids=(), feature_slugs=("order-export",))
    with pytest.raises(ShapeError):
        await _run(harness)(round_id=round_id, persona_ids=PERSONAS, feature_slugs=())


# ── 成功路径 ────────────────────────────────────────────────────────────────


async def test_success_records_artifact_and_appends_remove_proposal() -> None:
    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)
    before = harness.rounds.get(round_id)

    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    round_ = harness.rounds.get(round_id)
    assert round_.sim_review is not None
    assert [item["persona_id"] for item in round_.sim_review["personas"]] == list(PERSONAS)
    document_version = harness.documents.get(PROJECT).current_version.version_no
    assert round_.sim_review["evaluated_version"] == document_version
    draft = round_.draft
    assert draft is not None
    assert len(draft.proposals) == len(before.draft.proposals) + 1
    added = draft.proposals[-1]
    assert added.proposal_id == "sim#1"
    assert added.op is WriteOp.REMOVE
    assert str(added.entry_id) == "en-flow"
    assert added.origin.type.value == "proposed", "评测提案必须落'它提议的'"
    assert added.origin.refs, "提案必须带编号"

    ledger = harness.evidences.get(PROJECT)
    simulated = [item for item in ledger.evidences() if item.kind is EvidenceKind.SIMULATED]
    assert len(simulated) == 3, "三个 persona 各引了一条 dead_end 摩擦"
    assert all(
        str(item.source.external_ref).startswith(f"simulation:review:{round_id}#")
        for item in simulated
    )
    assert "round.updated" in harness.events.names


async def test_clean_review_without_triggers_makes_no_proposals() -> None:
    import json as _json

    def smooth_packet() -> str:
        payload = _json.loads(_packet())
        feature = payload["features"][0]
        feature["frictions"] = []
        feature["walkthrough"] = [
            {
                "step_index": 1,
                "step_text": "进入订单列表",
                "from_doc": "en-flow",
                "action": "打开订单列表页",
                "outcome": "ok",
                "need": "",
            }
        ]
        feature["verdict"] = "smooth"
        feature["verdict_because"] = []
        return _json.dumps(payload, ensure_ascii=False)

    smooth = {persona: smooth_packet() for persona in PERSONAS}
    harness = _harness({}, smooth)
    round_id = await _round_on_draft_card(harness)
    before = harness.rounds.get(round_id)

    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    round_ = harness.rounds.get(round_id)
    assert round_.sim_review_runs == 1
    assert len(round_.draft.proposals) == len(before.draft.proposals)  # type: ignore[union-attr]
    simulated = [
        item
        for item in harness.evidences.get(PROJECT).evidences()
        if item.kind is EvidenceKind.SIMULATED
    ]
    assert simulated == [], "没人被引用就不发号（按需发号）"


# ── 重装与失败 ──────────────────────────────────────────────────────────────


async def test_repair_then_success_does_not_write_a_gap() -> None:
    bad = _packet(entry_id="nope")
    responses = {
        "persona.first_touch": [bad, _packet()],
        "persona.edge_maker": _packet(),
        "persona.bulk_operator": _packet(),
    }
    harness = _harness({}, responses)
    round_id = await _round_on_draft_card(harness)
    gaps_before = harness.rounds.get(round_id).draft.gaps  # type: ignore[union-attr]

    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    round_ = harness.rounds.get(round_id)
    assert round_.sim_review_runs == 1
    assert round_.draft is not None and round_.draft.gaps == gaps_before, "重装成功不留痕"
    by_persona = [
        request.metadata["persona_id"]
        for request in harness.gateway.requests
        if request.purpose == "sim_review"
    ]
    assert by_persona.count("persona.first_touch") == 2


async def test_second_strike_fails_the_whole_run_with_a_gap_line() -> None:
    bad = _packet(entry_id="nope")
    responses = {
        "persona.first_touch": bad,
        "persona.edge_maker": _packet(),
        "persona.bulk_operator": _packet(),
    }
    harness = _harness({}, responses)
    round_id = await _round_on_draft_card(harness)
    before = harness.rounds.get(round_id)

    from pmstudio.harness.errors import PacketParseError

    with pytest.raises(PacketParseError):
        await _run(harness)(
            round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
        )

    round_ = harness.rounds.get(round_id)
    assert round_.sim_review_runs == 0, "失败不计数，可重跑"
    assert round_.sim_review is None
    draft = round_.draft
    assert draft is not None
    assert len(draft.proposals) == len(before.draft.proposals), "失败不落提案"
    assert len(draft.gaps) == len(before.draft.gaps) + 1
    assert draft.gaps[-1].startswith("模拟评测未完成")
    assert "persona.first_touch" in draft.gaps[-1]


async def test_model_failure_fails_the_run_with_a_gap_line() -> None:
    responses = {
        "persona.first_touch": _packet(),
        "persona.edge_maker": _packet(),
        # bulk_operator 不在脚本里：网关直接失败（不返回空串）
    }
    harness = _harness({}, responses)
    round_id = await _round_on_draft_card(harness)

    with pytest.raises(ModelCallFailed):
        await _run(harness)(
            round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
        )

    round_ = harness.rounds.get(round_id)
    assert round_.sim_review_runs == 0
    assert round_.draft is not None
    assert round_.draft.gaps[-1].startswith("模拟评测未完成")
    assert "persona.bulk_operator" in round_.draft.gaps[-1]


async def test_review_proposals_carry_the_manual_override_permission() -> None:
    """M11 的 D5 例外：评测提案带 `allow_manual_override`；整合提案不带（权限只给评测）。"""

    harness = _harness({}, _all_good())
    round_id = await _round_on_draft_card(harness)

    await _run(harness)(
        round_id=round_id, persona_ids=PERSONAS, feature_slugs=("order-export",)
    )

    round_ = harness.rounds.get(round_id)
    draft = round_.draft
    assert draft is not None
    flags = {str(p.proposal_id): p.allow_manual_override for p in draft.proposals}
    assert flags["sim#1"] is True
    for proposal_id, flag in flags.items():
        if not proposal_id.startswith("sim#"):
            assert flag is False, f"{proposal_id} 不该带 D5 例外位"
