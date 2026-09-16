"""跨对象、写不进单个模型的不变量。

单个模型能表达的（字段组合、数量上限、必填）留在模型里；这里放**需要两个以上对象才能判**的：

| 不变量 | 函数 |
|---|---|
| I17 写范围由 M20 强制 | `check_proposals_within_scope` |
| I21 引用必须能定位 | `check_citations_resolvable` |
| I6 记忆会失效 | `is_memory_invalid` |
| I10 冲突不静默覆盖 | `detect_version_conflicts` |
| 引用归因：`claim` 不许出编排层 | `check_citation_targets` |
| 写入 trigger 与 payload 必须配对 | `check_write_payload` |
| C7 提案级裁决必须列全 | `check_proposal_states` |
| N2 裁剪顺序可复现 | `order_context_blocks` |
"""

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import CardKind, CardStatus, CitationTargetType, VersionTrigger
from pmstudio.contracts.models.card import Card, CardAnswer
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.document import (
    ManualEditPayload,
    RollbackPayload,
    SubmitPayload,
    WritePayload,
)
from pmstudio.contracts.models.memory import Memory
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextBlock


@runtime_checkable
class CitationResolver(Protocol):
    """回答"这个引用指的东西真的存在吗"。由运行时提供（存储 / 黑板 / 这一轮的产出）。"""

    def knows(self, target_type: CitationTargetType, target_id: str) -> bool: ...


def check_proposals_within_scope(card: Card, scope: Scope) -> None:
    """I17：有选区时，每条提案的 `target_label` 必须落在选区内。

    空选区表示不限制（由 M28 推导落位）；非填充卡没有提案，直接放行。
    检查的是**全部**提案，包括用户已经删掉的——卡片上展示的就是它们。
    """
    if card.kind is not CardKind.FILL:
        return
    for proposal in card.proposals:
        if not scope.covers(proposal.target_label):
            raise ContractViolation(f"提案落在选区外：{proposal.target_label}（I17：AI 不能写选区外的字段）")


def check_proposal_states(card: Card, answer: CardAnswer) -> None:
    """填充卡的提案级裁决必须**显式列全**（C7）。

    "列全"要同时看卡片（有哪些提案）与回应（列了哪些），所以写在跨对象这一层——
    `CardAnswer` 自己只拿得到 `card_id`。M9 收到用户回应时调它。

    为什么必须显式：`Proposal.state` 的默认值是 `kept`，"忘了传 = 默认要"会把用户没要的
    内容写进文档，那是这类流程里最贵的一种错。

    三种情形分开：非填充卡不许带裁决；填充卡**还没决定**（`pending` / `skipped`）时也不许带；
    只有 `answered` 才要求列全。
    """
    if card.kind is not CardKind.FILL:
        if answer.proposal_states:
            raise ContractViolation("只有填充卡才有提案级裁决——这张卡没有 proposals")
        return

    if answer.status is not CardStatus.ANSWERED:
        if answer.proposal_states:
            raise ContractViolation("还没回应、或者已经跳过，就不该有提案裁决")
        return

    expected = {proposal.proposal_id for proposal in card.proposals}
    listed = set(answer.proposal_states)
    missing = expected - listed
    if missing:
        raise ContractViolation(
            f"填充卡 {card.card_id} 的提案裁决不完整，缺：{'、'.join(sorted(missing))}"
        )
    extra = listed - expected
    if extra:
        raise ContractViolation(
            f"这些提案 id 不属于卡片 {card.card_id}：{'、'.join(sorted(extra))}"
        )


def check_citation_targets(citations: Sequence[Citation], *, allow_claim: bool) -> None:
    """引用归因：`claim` 只允许出现在编排层内部的对象及其留痕里。

    C7 的提案与备选、C9 的记忆都调 `allow_claim=False`；
    C4 的主张与 C11 的主张留痕调 `allow_claim=True`。
    """
    for citation in citations:
        if citation.target_type is CitationTargetType.CLAIM and not allow_claim:
            raise ContractViolation(
                "claim 引用只允许出现在编排层内部的对象与它们的留痕里——跨层的是成品，不是原材料"
            )


def check_citations_resolvable(citations: Sequence[Citation], resolver: CitationResolver) -> None:
    """I21：每条引用都要指向真实存在的对象；含糊的一句"根据项目文档"不算依据。"""
    for citation in citations:
        if not resolver.knows(citation.target_type, citation.target_id):
            raise ContractViolation(
                f"引用指不到真实对象：{citation.target_type.value}/{citation.target_id}（I21）"
            )


def is_memory_invalid(memory: Memory, current_block_versions: Mapping[str, int]) -> bool:
    """I6：记忆引用的 block 被改动（或已消失）之后，这条记忆不再作为依据。

    依据是 C15 的 `target_version`：现在版本比它大 → 内容被改过 → 失效。
    引用材料 / 思路候选的记忆不参与这条判定。
    """
    for citation in memory.citations:
        if citation.target_type is not CitationTargetType.BLOCK:
            continue
        current = current_block_versions.get(citation.target_id)
        if current is None or current > (citation.target_version or 0):
            return True
    return False


def detect_version_conflicts(
    base_versions: Mapping[str, int], current_versions: Mapping[str, int]
) -> tuple[str, ...]:
    """I10：版本对不上就不静默覆盖。

    调用方（M20）先把范围收窄到**这次要写入的那些块**，本函数只做比对。
    返回冲突的 block_id（已排序，方便测试与日志）；空元组表示可以写。
    """
    return tuple(
        sorted(
            block_id
            for block_id, base_version in base_versions.items()
            if current_versions.get(block_id) != base_version
        )
    )


def check_write_payload(trigger: VersionTrigger, payload: WritePayload) -> None:
    """三种 trigger 各有自己的 payload，配错就不是一次合法的写入。"""
    expected: dict[VersionTrigger, type] = {
        VersionTrigger.SUBMIT: SubmitPayload,
        VersionTrigger.MANUAL: ManualEditPayload,
        VersionTrigger.ROLLBACK: RollbackPayload,
    }[trigger]
    if not isinstance(payload, expected):
        raise ContractViolation(
            f"{trigger.value} 的 payload 只能是 {expected.__name__}，收到 {type(payload).__name__}"
        )


def order_context_blocks(blocks: Sequence[ContextBlock]) -> tuple[ContextBlock, ...]:
    """M24 的裁剪顺序：`priority` 大的先留，同值按 `(source, ref)` 排——保证结果可复现。

    排序规则在这里定死一次，M13 只负责给数值，M24 不做语义判断。
    """
    return tuple(sorted(blocks, key=lambda block: (-block.priority, block.source.value, block.ref)))
