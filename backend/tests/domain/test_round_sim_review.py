"""轮次记住"这一轮跑过模拟评测"（M11）：artifact + 次数；以及"减"提案追加进草案。

artifact 是 **plain dict**（不是领域对象）：`round` 上下文不许 import 其他 domain 模块
（`test_domain_contexts_do_not_import_each_other`），校验形状住在应用层，落轮次时已是干净 dict。

`augment_draft` 是**追加**不是覆盖：复核提案 id 与整合那批不撞（撞了必须炸，不静默）；
`dig` 出新草案时评测随旧草案一起作废（"每份草案限 1 次"的精确语义）。
"""

import pytest

from pmstudio.domain.round.draft import (
    Draft,
    Origin,
    OriginType,
    Proposal,
)
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.errors import DuplicateProposalId, ShapeError
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.sqlite.serialization import (
    proposal_from_json,
    proposal_to_json,
    round_from_payload,
    round_to_payload,
)
from tests.support.builders import PROJECT, ROUND_ID, make_round


def _review() -> dict:
    return {
        "evaluated_version": 2,
        "personas": [
            {
                "persona_id": "persona.first_touch",
                "title": "首次上手者",
                "features": [],
            }
        ],
        "advisories": [],
    }


def _proposal(proposal_id: str = "sim#1", entry_id: str = "r1#01") -> Proposal:
    return Proposal(
        proposal_id=ProposalId(proposal_id),
        entry_id=EntryId(entry_id),
        op=WriteOp.REMOVE,
        target=Target.body("s5", feature_slug="order-export"),
        text="运营按时间筛订单后导出",
        origin=Origin(OriginType.PROPOSED, (EvidenceId("E9"),), rationale="模拟评测"),
        kind=BlockKind.TEXT,
    )


def _draft_with_one() -> Draft:
    return Draft(
        draft_id=DraftId("d1"),
        round_id=RoundId(ROUND_ID),
        proposals=(
            Proposal(
                proposal_id=ProposalId("p01"),
                entry_id=EntryId("r1#02"),
                op=WriteOp.ADD,
                target=Target.body("s2"),
                text="现状：导出靠手工复制",
                origin=Origin(OriginType.USER, (EvidenceId("E1"),)),
                kind=BlockKind.TEXT,
            ),
        ),
    )


def _round_awaiting_confirm() -> object:
    round_ = make_round()
    round_.integrate(draft=_draft_with_one())
    assert round_.step is Step.AWAITING_CONFIRM and round_.waiting_on is WaitingOn.DRAFT
    return round_


def test_record_sim_review_sets_artifact_and_counts_runs() -> None:
    round_ = _round_awaiting_confirm()

    round_.record_sim_review(_review())

    assert round_.sim_review is not None
    assert round_.sim_review["evaluated_version"] == 2
    assert round_.sim_review_runs == 1

    twice = {**_review(), "evaluated_version": 3}
    round_.record_sim_review(twice)
    assert round_.sim_review["evaluated_version"] == 3
    assert round_.sim_review_runs == 2


def test_augment_draft_appends_proposals_and_gaps() -> None:
    round_ = _round_awaiting_confirm()

    round_.augment_draft(proposals=(_proposal(),), gaps=("评测只跑出一半，重跑一次",))

    draft = round_.draft
    assert draft is not None
    assert [str(item.proposal_id) for item in draft.proposals] == ["p01", "sim#1"]
    assert draft.gaps == ("评测只跑出一半，重跑一次",)
    assert str(draft.draft_id) == "d1", "追加不改草案身份"


def test_augment_draft_accepts_gaps_only() -> None:
    round_ = _round_awaiting_confirm()

    round_.augment_draft(gaps=("模拟评测未完成（persona.first_touch 引用落空），可重跑",))

    draft = round_.draft
    assert draft is not None
    assert len(draft.proposals) == 1
    assert draft.gaps == ("模拟评测未完成（persona.first_touch 引用落空），可重跑",)


def test_augment_draft_rejects_duplicate_proposal_id() -> None:
    round_ = _round_awaiting_confirm()

    with pytest.raises(DuplicateProposalId):
        round_.augment_draft(
            proposals=(_proposal("p01", entry_id="r1#03"),),
        )


def test_augment_draft_without_draft_raises() -> None:
    round_ = make_round()

    with pytest.raises(ShapeError):
        round_.augment_draft(proposals=(_proposal(),))


def test_dig_clears_sim_review_and_resets_runs() -> None:
    round_ = _round_awaiting_confirm()
    round_.record_sim_review(_review())

    round_.dig()

    assert round_.draft is None
    assert round_.sim_review is None, "评测属于那份草案；dig 出新草案，旧的随草案一起作废"
    assert round_.sim_review_runs == 0


def test_payload_roundtrip_keeps_sim_review_and_old_rows_fall_back() -> None:
    round_ = _round_awaiting_confirm()
    round_.record_sim_review(_review())
    round_.augment_draft(proposals=(_proposal(),))

    restored = round_from_payload(
        round_to_payload(round_),
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert restored.sim_review is not None
    assert restored.sim_review["personas"][0]["persona_id"] == "persona.first_touch"
    assert restored.sim_review_runs == 1
    assert restored.draft is not None
    assert [str(item.proposal_id) for item in restored.draft.proposals] == ["p01", "sim#1"]

    old = round_to_payload(round_)
    for key in ("sim_review", "sim_review_runs"):
        del old[key]  # 老行：没有这两把键
    restored_old = round_from_payload(
        old,
        round_id=ROUND_ID,
        project_id=PROJECT,
        created_at=round_.created_at,
        step=round_.step,
        waiting_on=round_.waiting_on,
    )
    assert restored_old.sim_review is None
    assert restored_old.sim_review_runs == 0


def test_proposal_allow_manual_override_survives_roundtrip_and_old_rows_default_false() -> None:
    """D5 例外（M11）的权限位必须随草案持久化；老行没有这把键 → False（行为与今天一致）。"""

    from dataclasses import replace as _replace

    proposal = _proposal()
    flagged = _replace(proposal, allow_manual_override=True)

    assert proposal_from_json(proposal_to_json(flagged)).allow_manual_override is True
    assert proposal_from_json(proposal_to_json(proposal)).allow_manual_override is False

    old = proposal_to_json(flagged)
    del old["allow_manual_override"]  # 老行：没有这把键
    assert proposal_from_json(old).allow_manual_override is False
