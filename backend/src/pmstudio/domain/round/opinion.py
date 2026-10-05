"""席位意见。席位只出一件事：疑问或判断，各带依据与理由。"""

from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.shared.errors import EmptyBasis, ShapeError
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId


class OpinionForm(StrEnum):
    QUESTION = "question"  # 我判断这里缺一个信息，而且只有用户能给
    JUDGMENT = "judgment"  # 我判断这里应该是什么，依据是什么


@dataclass(frozen=True, slots=True)
class Opinion:
    local_id: str
    form: OpinionForm
    about: str
    claim: str
    because: str
    evidence_refs: tuple[EvidenceId, ...] = ()
    basis_note: str | None = None

    def __post_init__(self) -> None:
        if not self.about.strip():
            raise ShapeError("意见必须写清是关于什么的", local_id=self.local_id)
        if not self.claim.strip():
            raise ShapeError("意见必须给出判断或疑问本身", local_id=self.local_id)
        if not self.because.strip():
            raise ShapeError("意见必须写理由（because）", local_id=self.local_id)
        if not self.evidence_refs and not (self.basis_note or "").strip():
            raise EmptyBasis(subject=f"意见 {self.local_id}")

    @property
    def kind_if_judgment(self) -> EvidenceKind:
        return EvidenceKind.AGENT_DERIVED if self.evidence_refs else EvidenceKind.AGENT_PROPOSED


@dataclass(frozen=True, slots=True)
class SeatReview:
    """一个席位交回的信息包（领域视角）。空意见允许，但必须说明为什么没有。"""

    seat_id: str
    basis: str
    opinions: tuple[Opinion, ...] = ()
    pass_index: int = 1  # 属于第几次席位轮；由 Round.attest 盖真值
    #: 这一包是谁交回的（§4.4 信封的 `packet_id`，`pkt-{轮次}-{kind}-{谁}-{槽位}`）。
    #: **可选，默认 None**：None = 修之前落库的老数据（那时没存），不是"没填"——
    #: REST 投影原样给 None，前端按缺失回退启发式，不许编一个。
    packet_id: str | None = None

    def __post_init__(self) -> None:
        if not self.basis.strip():
            raise EmptyBasis(subject=f"席位 {self.seat_id}")
        if self.pass_index < 1:
            raise ShapeError("席位轮序号从 1 开始", seat_id=self.seat_id)
