"""草案：提案数组，按来源分三组展示。整合者产出，用户逐条要/不要。"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.shared.errors import (
    DuplicateProposalId,
    ProposalWithoutRefs,
    ShapeError,
)
from pmstudio.domain.shared.ids import DraftId, EntryId, EvidenceId, ProposalId, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp


class OriginType(StrEnum):
    USER = "user"  # 你说过的
    DERIVED = "derived"  # 它替你判断的（带依据）
    PROPOSED = "proposed"  # 它提议的（无依据，默认不勾）
    SYSTEM_DEFAULT = "system_default"  # 系统按结构规则补的（如跳过的题）


@dataclass(frozen=True, slots=True)
class Origin:
    """提案的来源。值对象：空 refs 允许存在，R5 的拦截点在 Proposal。"""

    type: OriginType
    refs: tuple[EvidenceId, ...]
    rationale: str | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    proposal_id: ProposalId
    entry_id: EntryId
    op: WriteOp
    target: Target
    text: str
    origin: Origin
    kind: BlockKind | None = None
    #: 模拟评测的"减"提案（M11）：用户勾选确认后允许改动被手改过的块（D5 例外，
    #: `02` §11.0.48）。它不是"手改"——确认落笔**不会**给块盖手改章，只放行这一次。
    allow_manual_override: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ShapeError("提案文本不能为空", proposal_id=str(self.proposal_id))
        if not self.origin.refs:
            raise ProposalWithoutRefs(proposal_id=self.proposal_id)


@dataclass(frozen=True, slots=True)
class Draft:
    draft_id: DraftId
    round_id: RoundId
    proposals: tuple[Proposal, ...]
    gaps: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.proposals:
            raise ShapeError("草案至少一条提案")
        ids = [item.proposal_id for item in self.proposals]
        if len(set(ids)) != len(ids):
            repeated = next(item for item in ids if ids.count(item) > 1)
            raise DuplicateProposalId(proposal_id=repeated)

    def grouped(self) -> tuple[tuple[str, tuple[Proposal, ...]], ...]:
        """卡片上的三组。system_default 归入"它替你判断的"，但保留自己的类型以便打标记。"""

        groups: list[tuple[str, tuple[Proposal, ...]]] = []
        for origin_type, title in (
            (OriginType.USER, "你说过的"),
            (OriginType.DERIVED, "它替你判断的"),
            (OriginType.PROPOSED, "它提议的"),
        ):
            types = (
                (OriginType.DERIVED, OriginType.SYSTEM_DEFAULT)
                if origin_type is OriginType.DERIVED
                else (origin_type,)
            )
            items = tuple(item for item in self.proposals if item.origin.type in types)
            if items:
                groups.append((title, items))
        return tuple(groups)


def merge_duplicate_removes(
    proposals: Sequence[Proposal],
) -> tuple[tuple[Proposal, ...], tuple[ProposalId, ...]]:
    """同一 `entry_id` 的多条 `remove` 是**同一条指令**：留第一条，其余合并。

    为什么要合并：`remove` 撤下哪一条**只由 `entry_id` 决定**（`02` §11.0.2a），
    文本只是卡片上的展示、不入库（快照记的是结果文档）。所以两条 `remove` 指向同一条目时，
    第二条执行时那条目已经没了——真跑里它直接把整批确认打成 422（P30 · F28：
    整轮接受的 45 条一条都没写进文档）。

    为什么只合并 `remove`：

    - `replace` 两条是**顺序语义**（后一条覆盖前一条），合并会把用户勾的第二条改法吃掉；
    - `add` 每条都有新编号（`{round_id}#{index:02d}`），天然不会撞。

    保留哪一条 = 参数序列里**最先出现**的那条。两个调用点各按自己的顺序传：
    装配层传草案顺序（整合者那批在前，它的文本是"被撤下那条的正文"，卡片上更好看）；
    写入端传**用户接受的顺序**。

    **被合并掉的那条不进任何留痕通道**：它不是内容，是同一件事的第二次陈述——条目照样
    会被撤下，`unaccounted` / `dropped` 说的都是"内容没进文档"，塞进来只会污染它们。
    要可断言的地方在写入端（`CommitResult.merged_removes`）。
    """

    from dataclasses import replace

    kept: list[Proposal] = []
    merged: list[ProposalId] = []
    removed_entries: dict[EntryId, int] = {}
    for proposal in proposals:
        if proposal.op is WriteOp.REMOVE:
            index = removed_entries.get(proposal.entry_id)
            if index is not None:
                merged.append(proposal.proposal_id)
                # 被合并的这条带着 D5 例外位（用户勾过评测的那次删除）→ 并给保留的那条，
                # 否则"两条都勾"反而写不进去（M11；权限是用户的确认，不该被合并吃掉）。
                if proposal.allow_manual_override and not kept[index].allow_manual_override:
                    kept[index] = replace(kept[index], allow_manual_override=True)
                continue
            removed_entries[proposal.entry_id] = len(kept)
        kept.append(proposal)
    return tuple(kept), tuple(merged)
