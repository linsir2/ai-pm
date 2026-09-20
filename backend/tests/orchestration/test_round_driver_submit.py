"""RoundDriver.submit_cards：确认 → 填充卡 → 写入 → 轮次完成。"""

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from pmstudio.bootstrap.runtime import build_runtime_sync
from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    BlockOpKind,
    CardGroupState,
    CardKind,
    CardStatus,
    ProposalState,
    RegionName,
    RoundEntry,
    RoundPhase,
    VersionTrigger,
)
from pmstudio.contracts.models.card import Card, CardAnswer, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.document import BlockOp
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RoundRegion

AT = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


def _make_fill_card() -> Card:
    return Card(
        card_id="crd_fill",
        kind=CardKind.FILL,
        prompt="这次我打算改动 1 处",
        proposals=(
            Proposal(
                proposal_id="prp_1",
                target_label="功能清单",
                op=BlockOpKind.APPEND,
                content="支持把讨论候选带进主闭环",
            ),
        ),
    )


def _setup_round_with_fill_card(tmp_path: Path, scope: Scope | None = None):
    """建项目 + 开轮 + 写填充卡，返回 (runtime, project_id, doc_id)。"""
    r = build_runtime_sync(tmp_path / "test.sqlite3", clock=FixedClock())
    p = asyncio.run(r.project_service.create_project("reg_tpl_initial", "Test"))

    # 开轮（完整 11 步）
    rid = asyncio.run(r.round_driver.start_round(
        p.project_id, RoundEntry.MAIN, "细化功能清单", scope or Scope(),
    ))

    # 手动写填充卡（模拟 M28 生成的结果）
    card = _make_fill_card()
    group = CardGroup(group_id="grp_1", cards=(card,), round_id=rid)
    asyncio.run(r.round_driver._writer.write(RegionName.CARD_GROUP, group))

    return r, p.project_id, p.doc_id, rid


def test_submit_writes_document_and_creates_version(tmp_path: Path) -> None:
    """确认填充卡 → 文档写入 → 新版本。"""
    r, project_id, doc_id, rid = _setup_round_with_fill_card(tmp_path)

    answer = CardAnswer(
        card_id="crd_fill",
        verdict="confirm",
        status=CardStatus.ANSWERED,
        proposal_states={"prp_1": ProposalState.KEPT},
    )

    cg = asyncio.run(r.round_driver.submit_cards("grp_1", [answer]))
    assert cg.state is CardGroupState.CONFIRMED

    # 验证：文档被写入
    blocks = r.ledger.read_blocks(doc_id)
    func_block = next(b for b in blocks if b.schema_label == "功能清单")
    assert func_block.content == "支持把讨论候选带进主闭环"
    assert func_block.version == 2

    # 验证：轮次完成
    rr = asyncio.run(r.round_driver._board.read(RegionName.ROUND))
    assert rr.phase == RoundPhase.DONE

    r.close()


def test_submit_removes_rejected_proposals(tmp_path: Path) -> None:
    """用户删掉的提案（state=removed）不该写入文档。"""
    r = build_runtime_sync(tmp_path / "test.sqlite3", clock=FixedClock())
    p = asyncio.run(r.project_service.create_project("reg_tpl_initial", "Test"))

    rid = asyncio.run(r.round_driver.start_round(
        p.project_id, RoundEntry.MAIN, "细化功能清单", Scope(),
    ))

    # 两张提案的填充卡
    card = Card(
        card_id="crd_fill",
        kind=CardKind.FILL,
        prompt="改动 2 处",
        proposals=(
            Proposal(
                proposal_id="prp_1",
                target_label="功能清单",
                op=BlockOpKind.APPEND,
                content="内容A",
            ),
            Proposal(
                proposal_id="prp_2",
                target_label="风险",
                op=BlockOpKind.APPEND,
                content="内容B",
            ),
        ),
    )
    group = CardGroup(group_id="grp_1", cards=(card,), round_id=rid)
    asyncio.run(r.round_driver._writer.write(RegionName.CARD_GROUP, group))

    # 要 prp_1，不要 prp_2
    answer = CardAnswer(
        card_id="crd_fill",
        verdict="confirm",
        status=CardStatus.ANSWERED,
        proposal_states={"prp_1": ProposalState.KEPT, "prp_2": ProposalState.REMOVED},
    )

    cg = asyncio.run(r.round_driver.submit_cards("grp_1", [answer]))
    assert cg.state is CardGroupState.CONFIRMED

    blocks = r.ledger.read_blocks(p.doc_id)
    func_block = next(b for b in blocks if b.schema_label == "功能清单")
    risk_block = next(b for b in blocks if b.schema_label == "风险")
    assert func_block.content == "内容A"
    assert risk_block.content == ""  # 被删的不该写入

    r.close()


def test_submit_rejects_proposal_outside_scope(tmp_path: Path) -> None:
    """I17：有选区时，提案 target_label 必须在选区内。"""
    r, project_id, doc_id, rid = _setup_round_with_fill_card(
        tmp_path, scope=Scope(selected_fields=("功能清单",)),
    )

    # 填充卡包含"风险"字段（不在选区内）
    card = Card(
        card_id="crd_fill",
        kind=CardKind.FILL,
        prompt="改动 1 处",
        proposals=(
            Proposal(
                proposal_id="prp_1",
                target_label="风险",
                op=BlockOpKind.APPEND,
                content="外部模型限流",
            ),
        ),
    )
    group = CardGroup(group_id="grp_1", cards=(card,), round_id=rid)
    asyncio.run(r.round_driver._writer.write(RegionName.CARD_GROUP, group))

    answer = CardAnswer(
        card_id="crd_fill",
        verdict="confirm",
        status=CardStatus.ANSWERED,
        proposal_states={"prp_1": ProposalState.KEPT},
    )

    with pytest.raises(ContractViolation):
        asyncio.run(r.round_driver.submit_cards("grp_1", [answer]))

    r.close()


def test_submit_detects_version_conflict(tmp_path: Path) -> None:
    """I10：版本对不上就不静默覆盖。"""
    r, project_id, doc_id, rid = _setup_round_with_fill_card(tmp_path)

    # 模拟冲突：直接修改块版本（模拟用户手改）
    blocks = r.ledger.read_blocks(doc_id)
    func_block = next(b for b in blocks if b.schema_label == "功能清单")
    r.ledger.write_blocks(
        doc_id,
        [BlockOp(block_id=func_block.block_id, op=BlockOpKind.APPEND, content="手改内容", expected_version=1)],
        VersionTrigger.MANUAL,
    )

    answer = CardAnswer(
        card_id="crd_fill",
        verdict="confirm",
        status=CardStatus.ANSWERED,
        proposal_states={"prp_1": ProposalState.KEPT},
    )

    with pytest.raises(ContractViolation):
        asyncio.run(r.round_driver.submit_cards("grp_1", [answer]))

    r.close()


def test_submit_understanding_card_confirm_creates_fill_card(tmp_path: Path) -> None:
    """确认理解卡 → M28 生成填充卡 → 写回 card_group。"""
    r = build_runtime_sync(tmp_path / "test.sqlite3", clock=FixedClock())
    p = asyncio.run(r.project_service.create_project("reg_tpl_initial", "Test"))

    # 开轮（会产生理解卡，停在 awaiting_user）
    rid = asyncio.run(r.round_driver.start_round(
        p.project_id, RoundEntry.MAIN, "细化功能清单", Scope(),
    ))

    # 读理解卡
    rr = asyncio.run(r.round_driver._board.read(RegionName.ROUND))
    cg = asyncio.run(r.round_driver._board.read(RegionName.CARD_GROUP))
    understanding_card = cg.cards[0]
    assert understanding_card.kind is CardKind.UNDERSTANDING

    # 确认理解卡
    answer = CardAnswer(
        card_id=understanding_card.card_id,
        verdict="confirm",
        status=CardStatus.ANSWERED,
    )

    new_cg = asyncio.run(r.round_driver.submit_cards(cg.group_id, [answer]))

    # 验证：生成了填充卡
    assert new_cg.state is CardGroupState.ANSWERING
    assert new_cg.cards[0].kind is CardKind.FILL
    assert len(new_cg.cards[0].proposals) > 0

    r.close()
