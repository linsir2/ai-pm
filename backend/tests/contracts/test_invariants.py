"""跨对象不变量：写范围、引用可定位、记忆失效、版本冲突、排序。"""

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    BlockOpKind,
    CardKind,
    CitationTargetType,
    ContextBlockSource,
    MemoryStatus,
    MemoryType,
    VersionTrigger,
)
from pmstudio.contracts.invariants import (
    check_citation_targets,
    check_citations_resolvable,
    check_proposals_within_scope,
    check_write_payload,
    detect_version_conflicts,
    is_memory_invalid,
    order_context_blocks,
)
from pmstudio.contracts.models.card import Card, Proposal
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.document import (
    BlockOp,
    ManualEditPayload,
    RollbackPayload,
    SubmitPayload,
)
from pmstudio.contracts.models.memory import Memory
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextBlock


class _Resolver:
    def __init__(self, known: set[tuple[CitationTargetType, str]]) -> None:
        self._known = known

    def knows(self, target_type: CitationTargetType, target_id: str) -> bool:
        return (target_type, target_id) in self._known


def _fill_card(target_label: str) -> Card:
    return Card(
        card_id="crd_fill",
        kind=CardKind.FILL,
        prompt="这次我打算改动 1 处",
        proposals=(
            Proposal(
                proposal_id="prp_1",
                target_label=target_label,
                op=BlockOpKind.APPEND,
                content="支持讨论候选进主闭环",
            ),
        ),
    )


def test_proposal_inside_the_scope_passes() -> None:
    check_proposals_within_scope(_fill_card("功能清单"), Scope(selected_fields=("功能清单",)))


def test_proposal_outside_the_scope_is_rejected() -> None:
    with pytest.raises(ContractViolation):
        check_proposals_within_scope(_fill_card("风险"), Scope(selected_fields=("功能清单",)))


def test_empty_scope_puts_no_limit_on_placement() -> None:
    """什么都没选也能走主闭环，由 M28 推导落位。"""
    check_proposals_within_scope(_fill_card("风险"), Scope())


def test_non_fill_cards_have_nothing_to_place() -> None:
    card = Card(card_id="crd_1", kind=CardKind.QUESTION, prompt="上线时间要求是什么")
    check_proposals_within_scope(card, Scope(selected_fields=("功能清单",)))


def test_claim_citations_are_rejected_outside_the_orchestration_layer() -> None:
    citations = [Citation(target_type=CitationTargetType.CLAIM, target_id="clm_1")]
    with pytest.raises(ContractViolation):
        check_citation_targets(citations, allow_claim=False)
    check_citation_targets(citations, allow_claim=True)


def test_unresolvable_citation_is_rejected() -> None:
    resolver = _Resolver({(CitationTargetType.BLOCK, "blk_1")})
    check_citations_resolvable(
        [Citation(target_type=CitationTargetType.BLOCK, target_id="blk_1", target_version=1)],
        resolver,
    )
    with pytest.raises(ContractViolation):
        check_citations_resolvable(
            [Citation(target_type=CitationTargetType.MEMORY, target_id="mry_404")],
            resolver,
        )


def _memory(target_version: int) -> Memory:
    return Memory(
        memory_id="mry_1",
        type=MemoryType.DECISION,
        content="先不做多人协作",
        status=MemoryStatus.ACTIVE,
        citations=(
            Citation(
                target_type=CitationTargetType.BLOCK,
                target_id="blk_1",
                target_version=target_version,
            ),
        ),
    )


def test_memory_stays_valid_while_the_block_is_untouched() -> None:
    assert is_memory_invalid(_memory(3), {"blk_1": 3}) is False


def test_memory_invalidates_when_its_block_is_rewritten_or_gone() -> None:
    assert is_memory_invalid(_memory(3), {"blk_1": 4}) is True
    assert is_memory_invalid(_memory(3), {}) is True


def test_memory_citing_material_does_not_go_invalid() -> None:
    memory = Memory(
        memory_id="mry_1",
        type=MemoryType.LESSON,
        content="外部资料说得太乐观",
        status=MemoryStatus.ACTIVE,
        citations=(Citation(target_type=CitationTargetType.MATERIAL, target_id="mat_1"),),
    )
    assert is_memory_invalid(memory, {}) is False


def test_version_conflicts_are_reported_by_block_id() -> None:
    assert detect_version_conflicts({"blk_1": 2}, {"blk_1": 2}) == ()
    assert detect_version_conflicts({"blk_1": 2}, {"blk_1": 3}) == ("blk_1",)
    assert detect_version_conflicts({"blk_1": 2}, {}) == ("blk_1",)
    assert detect_version_conflicts({"blk_1": 2, "blk_2": 1}, {"blk_1": 3, "blk_2": 1}) == ("blk_1",)


def test_write_payload_must_match_its_trigger() -> None:
    check_write_payload(VersionTrigger.SUBMIT, SubmitPayload(group_id="grp_1"))
    check_write_payload(VersionTrigger.ROLLBACK, RollbackPayload(target_version_id="ver_1"))
    check_write_payload(
        VersionTrigger.MANUAL,
        ManualEditPayload(
            block_ops=(BlockOp(block_id="blk_1", op=BlockOpKind.REPLACE, content="x", expected_version=1),)
        ),
    )
    with pytest.raises(ContractViolation):
        check_write_payload(VersionTrigger.SUBMIT, RollbackPayload(target_version_id="ver_1"))
    with pytest.raises(ContractViolation):
        check_write_payload(VersionTrigger.MANUAL, SubmitPayload(group_id="grp_1"))


def test_context_blocks_are_ordered_high_priority_first() -> None:
    low = ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_3", priority=1)
    high = ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_1", priority=9)
    mid = ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_2", priority=5)
    assert [block.ref for block in order_context_blocks([low, high, mid])] == [
        "blk_1",
        "blk_2",
        "blk_3",
    ]


def test_same_priority_is_ordered_deterministically() -> None:
    """同优先级的顺序也要固定，否则同一份上下文每次裁剪结果不同。"""
    a = ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_2", priority=5)
    b = ContextBlock(source=ContextBlockSource.MEMORY, ref="mry_1", priority=5)
    assert [block.ref for block in order_context_blocks([a, b])] == ["blk_2", "mry_1"]
