"""主张：从用户输入切出来的原子条目。标签决定处理方式。"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import (
    DecisionWithoutDecider,
    GuessWithoutAssumption,
    LabelZoneViolation,
    ShapeError,
)
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.label import ClaimLabel, zone_for
from pmstudio.domain.shared.target import Target


@dataclass(frozen=True, slots=True)
class Assumption:
    """猜测升格成的假设：必须写清失败后果与怎么验证。"""

    failure_consequence: str
    verification: str

    def __post_init__(self) -> None:
        if not self.failure_consequence.strip():
            raise ShapeError("假设必须写失败后果")
        if not self.verification.strip():
            raise ShapeError("假设必须写验证方式")


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    text: str
    label: ClaimLabel
    target: Target
    raw_span: str
    eid: EvidenceId | None = None
    assumption: Assumption | None = None
    decided_by: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ShapeError("主张内容不能为空", claim_id=self.claim_id)
        if not self.raw_span.strip():
            raise ShapeError(
                "主张必须带用户原话片段（raw_span），否则复述无法跳回原文",
                claim_id=self.claim_id,
            )
        expected = zone_for(self.label)
        if self.target.zone is not expected:
            raise LabelZoneViolation(
                label=self.label, expected=expected, actual=self.target.label()
            )
        if self.label is ClaimLabel.GUESS and self.assumption is None:
            raise GuessWithoutAssumption(claim_id=self.claim_id)
        if self.label is not ClaimLabel.GUESS and self.assumption is not None:
            raise ShapeError("只有猜测才带假设", claim_id=self.claim_id)
        if self.label is ClaimLabel.DECISION and not (self.decided_by or "").strip():
            raise DecisionWithoutDecider(claim_id=self.claim_id)
