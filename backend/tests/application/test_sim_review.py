"""模拟评测机械层（M11-T4）：`document_anchors`（文档索引）、`review_issues`（筛）、
`evaluate_rules`（R1/R2/R3）。

这一层是全部"诚实门"的所在：模型说得再像，引用落空 / 验收没核完 / 编数字编身份，
都在这里被机械抓住；"减"的提案也只能由这里的规则产生（不靠模型发挥）。
"""

from pmstudio.application.sim_review import (
    MAX_SIM_REVIEW_PROPOSALS,
    SimCitation,
    document_anchors,
    evaluate_rules,
    review_issues,
    review_source_ref,
)
from pmstudio.domain.authoring.document import WriteInstruction
from pmstudio.domain.shared.ids import EntryId, EvidenceId, FeatureSlug
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, STD, document_with_feature


def _document():  # type: ignore[no-untyped-def]
    document = document_with_feature()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-flow",
                entry_id=EntryId("en-flow"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "main_flow", home=STD.feature_home),
                text="1. 进入订单列表\n2. 点导出\n3. 选时间范围",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.STEPS,
            ),
            WriteInstruction(
                instruction_id="i-prio",
                entry_id=EntryId("en-prio"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "priority", home=STD.feature_home),
                text="P0",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            ),
            WriteInstruction(
                instruction_id="i-acc1",
                entry_id=EntryId("en-acc1"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "acceptance", home=STD.feature_home),
                text="5 万条内 60 秒可下载",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.ACCEPTANCE,
            ),
            WriteInstruction(
                instruction_id="i-acc2",
                entry_id=EntryId("en-acc2"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "acceptance", home=STD.feature_home),
                text="无权限不出现导出按钮",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.ACCEPTANCE,
            ),
        ],
        now=NOW,
        summary="补字段",
    )
    return document


def _anchors():  # type: ignore[no-untyped-def]
    return document_anchors(_document().view())


def _step(outcome: str = "ok", need: str = "", action: str = "打开订单列表页") -> dict:
    return {
        "step_index": 1,
        "step_text": "进入订单列表",
        "from_doc": "en-flow",
        "action": action,
        "outcome": outcome,
        "need": need,
    }


def _friction(
    entry_id: str = "en-flow",
    *,
    local_id: str = "F1",
    kind: str = "dead_end",
    field: str = "",
    quote: str = "",
    not_documented: bool = True,
    blocks_flow: bool = True,
    note: str = "点了导出没有反应，文档没写失败算不算正常",
) -> dict:
    return {
        "local_id": local_id,
        "kind": kind,
        "entry_id": entry_id,
        "field": field,
        "quote": quote,
        "not_documented": not_documented,
        "blocks_flow": blocks_flow,
        "note": note,
    }


def _checks() -> list[dict]:
    return [
        {"local_id": "A1", "entry_id": "en-acc1", "status": "unverified", "note": ""},
        {"local_id": "A2", "entry_id": "en-acc2", "status": "verified", "note": ""},
    ]


def _review(
    persona_id: str = "persona.first_touch",
    *,
    slug: str = "order-export",
    started: bool = True,
    walkthrough: list[dict] | None = None,
    frictions: list[dict] | None = None,
    checks: list[dict] | None = None,
    verdict: str = "blocked",
    because: list[str] | None = None,
) -> dict:
    return {
        "persona_id": persona_id,
        "title": persona_id,
        "features": [
            {
                "feature_slug": slug,
                "started": started,
                "walkthrough": walkthrough
                if walkthrough is not None
                else [_step()],
                "frictions": frictions if frictions is not None else [_friction()],
                "acceptance_checks": checks if checks is not None else _checks(),
                "verdict": verdict,
                "verdict_because": because if because is not None else ["F1"],
            }
        ],
    }


def _issues(review: dict, *, requested: tuple[str, ...] = ("order-export",)) -> tuple[str, ...]:
    return review_issues(review, anchors=_anchors(), requested=requested)


# ── document_anchors ───────────────────────────────────────────────────────


def test_document_anchors_indexes_entries_features_and_acceptance() -> None:
    anchors = _anchors()

    assert anchors.entry_text["en-acc1"] == "5 万条内 60 秒可下载"
    assert anchors.acceptance["order-export"] == ("en-acc1", "en-acc2")
    assert anchors.priority_entry["order-export"] == "en-prio"
    assert "main_flow" in anchors.feature_fields["order-export"]
    assert anchors.entry_feature["en-flow"] == "order-export"
    assert anchors.entry_target["en-flow"] == Target.feature_field(
                                                  "order-export", "main_flow",
                                                  home=STD.feature_home
                                              )
    assert anchors.entry_order["en-flow"] < anchors.entry_order["en-prio"]


# ── review_issues：干净包 ───────────────────────────────────────────────────


def test_clean_review_has_no_issues() -> None:
    assert _issues(_review()) == ()


def test_quote_counts_as_documented_alternative() -> None:
    review = _review(
        frictions=[
            _friction(quote="筛选为空导出全部", not_documented=False, kind="ambiguous_rule")
        ],
        verdict="friction",
    )

    assert _issues(review) == ()


# ── review_issues：覆盖与结构 ───────────────────────────────────────────────


def test_missing_requested_feature_is_an_issue() -> None:
    issues = _issues(_review(), requested=("order-export", "checkout"))

    assert any("checkout" in item for item in issues)


def test_unknown_feature_is_an_issue() -> None:
    issues = _issues(_review(slug="nope"), requested=("order-export",))

    assert any("nope" in item for item in issues)


def test_acceptance_checks_must_cover_every_entry() -> None:
    issues = _issues(_review(checks=[_checks()[0]]))

    assert any("en-acc2" in item for item in issues), "漏核一条验收 = 没走完，重装"


def test_acceptance_checks_reject_foreign_entry() -> None:
    checks = _checks() + [
        {"local_id": "A3", "entry_id": "en-flow", "status": "verified", "note": ""}
    ]
    issues = _issues(_review(checks=checks))

    assert any("en-flow" in item for item in issues)


def test_started_false_requires_cannot_start_friction() -> None:
    issues = _issues(
        _review(
            started=False,
            walkthrough=[],
            frictions=[_friction(kind="ambiguous_rule")],
            verdict="blocked",
        )
    )

    assert any("cannot_start" in item for item in issues)


def test_started_true_requires_walkthrough() -> None:
    issues = _issues(_review(walkthrough=[]))

    assert any("走查" in item for item in issues)


def test_cannot_start_kind_only_when_not_started() -> None:
    issues = _issues(_review(frictions=[_friction(kind="cannot_start")]))

    assert any("cannot_start" in item for item in issues)


# ── review_issues：锚点与引用 ───────────────────────────────────────────────


def test_friction_entry_must_exist() -> None:
    issues = _issues(_review(frictions=[_friction(entry_id="nope")]))

    assert any("nope" in item for item in issues)


def test_friction_field_must_exist() -> None:
    issues = _issues(_review(frictions=[_friction(entry_id="", field="nope_field")]))

    assert any("nope_field" in item for item in issues)


def test_feature_level_friction_needs_no_anchor() -> None:
    review = _review(
        frictions=[_friction(entry_id="", field="", kind="missing_feedback", note="没有错误提示")]
    )

    assert _issues(review) == ()


def test_quote_and_not_documented_are_exactly_one() -> None:
    both = _issues(
        _review(frictions=[_friction(quote="导出全部", not_documented=True)])
    )
    neither = _issues(_review(frictions=[_friction(quote="", not_documented=False)]))

    assert both and neither


def test_verdict_because_must_point_at_existing_frictions() -> None:
    issues = _issues(_review(because=["F9"]))

    assert any("F9" in item for item in issues)


def test_friction_verdict_needs_a_because_and_smooth_must_not_have_one() -> None:
    empty = _issues(_review(verdict="friction", because=[]))
    smooth = _issues(_review(verdict="smooth", because=["F1"], frictions=[]))

    assert empty and smooth


def test_stuck_step_requires_need() -> None:
    issues = _issues(_review(walkthrough=[_step(outcome="stuck", need="")]))

    assert any("need" in item.lower() or "缺" in item for item in issues)


# ── review_issues：窄表（只扫模型自撰字段，文档引文豁免） ─────────────────────


def test_identity_words_in_notes_are_flagged() -> None:
    issues = _issues(
        _review(frictions=[_friction(note="白领运营会卡在这里")])
    )

    assert any("身份" in item for item in issues)


def test_fabricated_numbers_in_notes_are_flagged() -> None:
    issues = _issues(_review(frictions=[_friction(note="大约 62% 的人会放弃")]))

    assert any("数量" in item for item in issues)


def test_document_numbers_in_quote_and_step_text_are_exempt() -> None:
    review = _review(
        walkthrough=[_step()],
        frictions=[
            _friction(quote="5 万条内 60 秒可下载", not_documented=False, note="这条验收要分批导"),
        ],
        verdict="friction",
    )
    review["features"][0]["walkthrough"][0]["step_text"] = "5 万条内 60 秒可下载"

    assert _issues(review) == ()


def test_author_words_that_would_be_demographic_if_anchored_are_still_scanned_in_actions() -> None:
    issues = _issues(_review(walkthrough=[_step(action="作为一个中老年用户，我先找导出按钮")]))

    assert any("身份" in item for item in issues)


# ── evaluate_rules：R1 删条目 ────────────────────────────────────────────────


def test_r1_two_personas_on_same_entry_make_a_remove_proposal() -> None:
    outcome = evaluate_rules(
        [
            _review("persona.first_touch"),
            _review("persona.edge_maker"),
        ],
        anchors=_anchors(),
        requested=("order-export",),
    )

    removes = [item for item in outcome.proposals if item.op is WriteOp.REMOVE]
    assert len(removes) == 1
    proposal = removes[0]
    assert proposal.entry_id == "en-flow"
    assert proposal.text == "1. 进入订单列表\n2. 点导出\n3. 选时间范围", "删条提案抄现行正文"
    assert proposal.text != "", "remove 必须补正文（P26 口径）"
    assert len(outcome.citations) == 2
    assert {item.persona_id for item in outcome.citations} == {
        "persona.first_touch",
        "persona.edge_maker",
    }


def test_r1_single_persona_is_not_enough() -> None:
    outcome = evaluate_rules(
        [_review("persona.first_touch")], anchors=_anchors(), requested=("order-export",)
    )

    assert outcome.proposals == ()


def test_r1_contradicted_checks_count_as_flags() -> None:
    checks = [
        {
            "local_id": "A1",
            "entry_id": "en-acc1",
            "status": "contradicted",
            "note": "与 main_flow 打架",
        },
        {"local_id": "A2", "entry_id": "en-acc2", "status": "verified", "note": ""},
    ]
    soft = _friction(entry_id="en-flow", kind="extra_work", blocks_flow=False)
    a = _review("persona.first_touch", checks=checks, frictions=[soft], verdict="friction")
    b = _review("persona.edge_maker", checks=checks, frictions=[soft], verdict="friction")

    outcome = evaluate_rules([a, b], anchors=_anchors(), requested=("order-export",))

    targets = {item.entry_id for item in outcome.proposals}
    assert "en-acc1" in targets, "被两条 contradicted 标中的验收条目也要出删条建议"
    assert len(outcome.citations) >= 2


def test_r1_two_frictions_from_same_persona_do_not_count_twice() -> None:
    review = _review(
        "persona.first_touch",
        frictions=[
            _friction(local_id="F1"),
            _friction(local_id="F2", note="又一个问题"),
        ],
    )
    outcome = evaluate_rules([review], anchors=_anchors(), requested=("order-export",))

    assert outcome.proposals == ()


# ── evaluate_rules：R2 降级 / R3 建议复核 ────────────────────────────────────


def test_r2_two_blocked_personas_make_a_priority_replace() -> None:
    outcome = evaluate_rules(
        [
            _review("persona.first_touch"),
            _review("persona.edge_maker"),
            _review(
                "persona.bulk_operator",
                frictions=[_friction(kind="extra_work", blocks_flow=False, note="多两次点击")],
                verdict="friction",
                because=["F1"],
            ),
        ],
        anchors=_anchors(),
        requested=("order-export",),
    )

    replace = [item for item in outcome.proposals if item.op is WriteOp.REPLACE]
    assert len(replace) == 1
    assert replace[0].entry_id == "en-prio"
    assert "2/3" in replace[0].text
    assert "待复核" in replace[0].text


def test_r2_one_cannot_start_is_enough() -> None:
    outcome = evaluate_rules(
        [
            _review(
                "persona.first_touch",
                started=False,
                walkthrough=[],
                frictions=[_friction(kind="cannot_start", note="文档没有主流程，无从开始")],
                verdict="blocked",
            ),
            _review("persona.edge_maker", verdict="smooth", frictions=[], because=[]),
            _review("persona.bulk_operator", verdict="smooth", frictions=[], because=[]),
        ],
        anchors=_anchors(),
        requested=("order-export",),
    )

    replace = [item for item in outcome.proposals if item.op is WriteOp.REPLACE]
    assert len(replace) == 1
    assert "1/3" in replace[0].text


def test_r2_without_priority_entry_only_advises() -> None:
    checkout = document_with_feature()
    checkout.apply(
        [
            WriteInstruction(
                instruction_id="i-checkout",
                entry_id=EntryId("en-co-name"),
                op=WriteOp.ADD,
                target=Target.feature_field(FeatureSlug("checkout"), "name", home=STD.feature_home),
                text="结账",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="第二个功能",
        authorized_new_features=frozenset({FeatureSlug("checkout")}),
    )
    anchors = document_anchors(checkout.view())
    feature_level = _friction(entry_id="", field="", kind="missing_feedback", note="没有错误提示")
    outcome = evaluate_rules(
        [
            _review("persona.first_touch", slug="checkout", checks=[], frictions=[feature_level]),
            _review("persona.edge_maker", slug="checkout", checks=[], frictions=[feature_level]),
        ],
        anchors=anchors,
        requested=("checkout",),
    )

    assert all(item.op is not WriteOp.REPLACE for item in outcome.proposals)
    assert any("优先级" in item for item in outcome.advisories)


def test_r3_all_walked_nowhere_gives_a_review_advisory() -> None:
    outcome = evaluate_rules(
        [
            _review("persona.first_touch"),
            _review("persona.edge_maker"),
        ],
        anchors=_anchors(),
        requested=("order-export",),
    )

    assert any("人工复核" in item for item in outcome.advisories)


def test_proposals_are_capped_and_overflow_is_told() -> None:
    def flagged(entry: str, kind: str = "dead_end") -> dict:
        return _review(
            "persona.first_touch",
            frictions=[_friction(entry_id=entry, kind=kind)],
            verdict="blocked",
        )

    def flagged_edge(entry: str, kind: str = "dead_end") -> dict:
        return _review(
            "persona.edge_maker",
            frictions=[_friction(entry_id=entry, kind=kind)],
            verdict="blocked",
        )

    entries = ["en-flow", "en-acc1", "en-acc2", "en-prio"]
    because = [f"F{i}" for i, _ in enumerate(entries, start=1)]

    def packed(persona: str) -> dict:
        return {
            "persona_id": persona,
            "title": persona,
            "features": [
                {
                    "feature_slug": "order-export",
                    "started": True,
                    "walkthrough": [_step()],
                    "frictions": [_friction(entry_id=e) for e in entries],
                    "acceptance_checks": _checks(),
                    "verdict": "blocked",
                    "verdict_because": because,
                }
            ],
        }

    reviews = [packed("persona.first_touch"), packed("persona.edge_maker")]

    outcome = evaluate_rules(reviews, anchors=_anchors(), requested=("order-export",))

    assert len(outcome.proposals) == MAX_SIM_REVIEW_PROPOSALS
    assert [item.proposal_id for item in outcome.proposals] == ["sim#1", "sim#2", "sim#3"]
    assert any("未进卡片" in item for item in outcome.advisories), "溢出必须如实告知，不静默丢"


def test_r1_r2_share_citations_without_duplicating_them() -> None:
    outcome = evaluate_rules(
        [
            _review("persona.first_touch"),
            _review("persona.edge_maker"),
        ],
        anchors=_anchors(),
        requested=("order-export",),
    )

    keys = [item.key for item in outcome.citations]
    assert len(keys) == len(set(keys))
    ref_keys = [key for item in outcome.proposals for key in item.ref_keys]
    assert set(ref_keys) <= set(keys)


def test_source_ref_is_readable_and_namespaced_per_persona_feature_item() -> None:
    citation = SimCitation(
        persona_id="persona.first_touch", feature_slug="order-export", local_id="F1", text="x"
    )

    assert (
        review_source_ref("r-1", citation)
        == "simulation:review:r-1#persona.first_touch@order-export#F1"
    )
