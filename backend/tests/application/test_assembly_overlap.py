"""add 防线：整块重述式 add 的行级标注（2026-10-29）。

`docs/plans/2026-10-29-整块重述-add-防线.md` §3 的用例 1–8。口径：

- 判重（`assemble` 的整段键）照旧用**原始** draft.text——清洗只影响判空与最终 text；
- 行级重叠是**机械判定快照**（"dup/total"）随草案落库，界面靠它默认不勾；
- 清洗后剩空的 add/replace 走既有"丢这一条 + 留痕"分支，**绝不**到 `Proposal`
  （那边 ShapeError → PacketParseError → 整包重装，计划明令禁止）。
"""

from pmstudio.application import assembly
from pmstudio.application.agents import schemas
from pmstudio.application.dto import _proposal_view
from pmstudio.application.use_cases import IntegrateRound, IntegrationOutcome
from pmstudio.domain.provenance.evidence import EvidenceId, EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.shared.ids import EntryId, FeatureSlug, ProjectId, ProposalId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from pmstudio.harness.model.envelope import Envelope
from pmstudio.infrastructure.postgres.serialization import (
    proposal_from_json,
    proposal_to_json,
)
from tests.support.builders import (
    NOW,
    ROUND_ID,
    STD,
    document_with_feature,
    make_round,
)

WHOLE_LINE = "把订单导出成表格"
SEAT = "seat.data_scale"


# ── 夹具：与 test_assembly.py 同款 ────────────────────────────────────────────


def ledger_with_two_entries() -> EvidenceLedger:
    ledger = EvidenceLedger.empty(ProjectId("p1"))
    e1 = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=ROUND_ID, raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出应在 60 秒内完成",
        source=EvidenceSource(seat_id=SEAT, derived_from=(e1,), rationale="量级在 1 万级"),
        now=NOW,
    )
    return ledger


def packet(**overrides: object) -> schemas.IntegrationPacket:
    payload: dict[str, object] = {"questions": [], "list_items": [], "proposals": []}
    payload.update(overrides)
    return schemas.IntegrationPacket.model_validate(payload)


def draft(
    *,
    op: str = "add",
    field: str = "one_liner",
    text: str,
    entry_id: str | None = None,
    refs: tuple[str, ...] = ("E1", "E2"),
) -> dict:
    spec: dict[str, object] = {
        "op": op,
        "target": {
            "zone": "body",
            "section_id": "s5",
            "feature_slug": "order-export",
            "field": field,
        },
        "text": text,
        "origin_type": "derived",
        "evidence_refs": list(refs),
    }
    if entry_id is not None:
        spec["entry_id"] = entry_id
    return spec


def assemble(*proposals: dict):
    return assembly.assemble(
        packet(proposals=list(proposals)),
        round_=make_round(),
        document=document_with_feature(),
        ledger=ledger_with_two_entries(),
    )


def _old() -> "Target":
    return Target.feature_field(
        FeatureSlug("order-export"), "one_liner", home=STD.feature_home
    )


def base_proposal(**overrides: object) -> Proposal:
    """最小可用 Proposal：refs 非空（领域不变式），其余按默认组装。"""

    fields: dict[str, object] = {
        "proposal_id": ProposalId("p01"),
        "entry_id": EntryId("en-bootstrap"),
        "op": WriteOp.ADD,
        "target": Target.body("s5"),
        "text": "一条新内容",
        "origin": Origin(type=OriginType.PROPOSED, refs=(EvidenceId("E1"),)),
    }
    fields.update(overrides)
    return Proposal(**fields)


# ── §3.1 整块重述（R02 报文形状）──────────────────────────────────────────────


def test_three_verbatim_lines_plus_one_new_line_marks_overlap_3_4():
    """4 行非空、3 行与既有条目逐字相同 → 提案照常入卡、overlap=="3/4"、无留痕。"""

    result = assemble(
        draft(text=f"{WHOLE_LINE}\n{WHOLE_LINE}\n{WHOLE_LINE}\n新的口径是异步上传")
    )

    assert len(result.proposals) == 1
    assert result.proposals[0].overlap == "3/4"
    # 行级标注是"标 + 默认不勾"的防线，不丢也不禁：dropped 一条都不该有
    assert result.dropped == ()


def test_the_marked_proposal_reports_itself_in_the_gaps_line():
    """overlap 非空条数 > 0 → 缺口行（走既有通道）报数；没有就不报。"""

    def gaps_for(*proposals: dict) -> tuple[str, ...]:
        runner = object.__new__(IntegrateRound)
        runner._clock = type("_Clock", (), {"now": staticmethod(lambda: NOW)})()
        runner._checker = type(
            "_NoChecker", (), {"check": staticmethod(lambda *a, **k: ())}
        )()
        outcome = IntegrationOutcome(
            envelope=Envelope(
                packet_id=f"pkt-{ROUND_ID}-integration-integrator",
                run_id=str(ROUND_ID),
                kind="integration",
                produced_by="integrator",
                basis="读了本轮输入",
                payload={},
                packet=packet(proposals=list(proposals)),
            )
        )
        _, gaps = runner._assemble_and_gaps(
            round_=make_round(),
            document=document_with_feature(),
            ledger=ledger_with_two_entries(),
            outcome=outcome,
            carried=(),
        )
        return gaps

    marked = gaps_for(draft(text=f"{WHOLE_LINE}\n{WHOLE_LINE}\n{WHOLE_LINE}\n新的一句"))
    assert any(
        "本轮有 1 条提案与字段已有内容重叠（已默认不勾，看清再勾）" in line for line in marked
    )

    clean = gaps_for(draft(text="整句都是新的话\n另一句也是全新的"))
    assert not any("与字段已有内容重叠" in line for line in clean)


# ── §3.2 / §3.4 行级阈值 ──────────────────────────────────────────────────────


def test_all_new_lines_get_no_overlap():
    result = assemble(draft(text="一句全新的话\n另一句全新的话"))

    assert result.proposals[0].overlap is None


def test_below_threshold_is_none_and_at_threshold_is_marked():
    """1/4 < 阈值 → None（提案照常在）；3/6 恰好 dup×2≥total → 标注；2/6 不过线。"""

    below = assemble(draft(text=f"{WHOLE_LINE}\n新的甲\n新的乙\n新的丙"))
    assert below.proposals[0].overlap is None
    assert len(below.proposals) == 1

    # 阈值边界直接对着判据函数测（独立断言，不搭整包）
    existing = assembly._existing_lines(document_with_feature())
    assert assembly._overlap_note(
        f"{WHOLE_LINE}\n{WHOLE_LINE}\n{WHOLE_LINE}\n丙\n丁\n戊",
        target=_old(),
        existing_lines=existing,
    ) == "3/6"
    assert (
        assembly._overlap_note(
            f"{WHOLE_LINE}\n{WHOLE_LINE}\n丙\n丁\n戊\n己", target=_old(), existing_lines=existing
        )
        is None
    )
    # 单行不算：单行撞到既有内容是常事（整段判重管它），成段重述才是这道防线要拦的
    assert assembly._overlap_note(WHOLE_LINE, target=_old(), existing_lines=existing) is None


# ── §3.3 既有判重不松（锚）────────────────────────────────────────────────────


def test_whole_text_verbatim_dup_still_caught_by_existing_dedup():
    """整段文本与既有条目逐字相同的单条 add：仍是既有判重静默跳过（不是行级标注）。"""

    result = assemble(draft(text=WHOLE_LINE))

    assert result.proposals == ()
    # 判重跳过与"没法用留痕"是两条路：这里不该有留痕
    assert result.dropped == ()


# ── §3.5 replace / remove 不清洗、不重叠 ──────────────────────────────────────


def test_replace_and_remove_get_no_overlap_and_dedup_stays_loose_for_them():
    for op, text, entry_id in (
        ("replace", WHOLE_LINE, "en-bootstrap"),  # 照抄既有正文——判重本来就不拦 replace
        ("remove", "", "en-bootstrap"),
    ):
        result = assemble(draft(op=op, text=text, entry_id=entry_id))

        assert len(result.proposals) == 1, f"op={op} 该照常出提案"
        assert result.proposals[0].overlap is None


def test_remove_text_is_not_cleaned():
    """remove 的文本是展示（撤哪一条看 entry_id），清洗路径碰它一个字都不该。"""

    result = assemble(draft(op="remove", text="（E16）撤下这条", entry_id="en-bootstrap"))

    assert result.proposals[0].text == "（E16）撤下这条"


def test_cleaning_touched_only_the_text_refs_survive_intact():
    proposal = assemble(draft(text="（E16）好的方向")).proposals[0]

    assert proposal.text == "好的方向"
    assert tuple(str(ref) for ref in proposal.origin.refs) == ("E1", "E2")


def test_all_citations_text_is_dropped_with_a_note_not_a_package_error():
    """砍完整条只剩空 → 走既有"丢这一条 + 留痕"分支；PacketParseError（整包重装）不许发生。"""

    result = assemble(draft(text="（E16 E5）\n（E16）"))

    assert result.proposals == ()
    assert len(result.dropped) == 1
    assert "提案文本为空" in result.dropped[0].reason


# ── 清洗 / label 公式的独立断言（分层）────────────────────────────────────────


def test_pure_eid_brackets_are_stripped_anywhere_but_non_e_brackets_survive():
    """`（E16 E5）`任一位置剥干净；`（见 E12）`带非 E token 的括号一个字不碰；保多行。"""

    assert (
        assembly._strip_eid_citations("（E16 E5）任一位置\n见底（E16）。\n（见 E12）保留")
        == "任一位置\n见底。\n（见 E12）保留"
    )
    assert assembly._strip_eid_citations("甲（E1）\n乙\n（E2 E3）") == "甲\n乙\n"


def test_list_marker_and_existing_lines_helpers():
    """列表记号剥的是**行首**记号；行级集合与 `_existing_content` 共用同一把 label 公式。"""

    assert assembly._strip_list_marker("- 点检未完成") == "点检未完成"
    assert assembly._strip_list_marker("• 点检未完成") == "点检未完成"
    assert assembly._strip_list_marker("* 点检未完成") == "点检未完成"
    assert assembly._strip_list_marker("· 点检未完成") == "点检未完成"
    assert assembly._strip_list_marker("1. 点检未完成") == "点检未完成"
    assert assembly._strip_list_marker("1、点检未完成") == "点检未完成"
    # 句中的记号是内容：不是行首就一个字不碰
    assert assembly._strip_list_marker("点检未完成的 1. 种情况") == "点检未完成的 1. 种情况"

    existing = assembly._existing_lines(document_with_feature())
    label = f"{STD.feature_home}/order-export.one_liner"
    assert (label, assembly.normalize(WHOLE_LINE)) in existing
    # 同一把 label 公式：整段键的每个 label，行级键也认（不会各说各话）
    whole = assembly._existing_content(document_with_feature())
    assert {item for item in whole} == {
        (lb, assembly.normalize(text)) for lb, text in whole
    }
    assert label in {lb for lb, _ in whole}


# ── §3.7 序列化分层 ───────────────────────────────────────────────────────────


def test_overlap_round_trips_through_serialization():
    dumped = proposal_to_json(base_proposal(overlap="3/4"))

    assert dumped["overlap"] == "3/4"
    assert proposal_from_json(dumped).overlap == "3/4"


def test_old_payload_without_overlap_key_is_none():
    dumped = proposal_to_json(base_proposal())
    del dumped["overlap"]

    assert proposal_from_json(dumped).overlap is None
    # 新载荷即使全 None 也带这把键（键在与不在语义不同）
    assert proposal_to_json(base_proposal())["overlap"] is None


# ── §3.8 dto：warning 合成 + 字段透出 ─────────────────────────────────────────


def test_overlap_assembles_a_human_warning_when_no_scope_violation():
    view = _proposal_view(base_proposal(overlap="3/4"), warning=None)

    assert view.overlap == "3/4"
    assert view.warning is not None
    assert "与已有内容重叠" in view.warning
    assert "3/4" in view.warning
    assert "replace" in view.warning


def test_scope_guard_violation_wins_but_overlap_still_shown():
    view = _proposal_view(base_proposal(overlap="3/4"), warning="这条越出写权限了")

    assert view.warning == "这条越出写权限了"  # 违例优先
    assert view.overlap == "3/4"  # 字段照旧透出（判默认勾选用）


def test_no_overlap_no_warning():
    view = _proposal_view(base_proposal(), warning=None)

    assert view.warning is None
    assert view.overlap is None
