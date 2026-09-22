"""草案：提案数组，按来源分三组展示。整合者产出，用户逐条要/不要。"""

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
