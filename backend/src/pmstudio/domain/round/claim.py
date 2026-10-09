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
    #: 这条主张读过的材料块编号（落编号时写进 `derived_from`，让"材料→主张"可查）。
    #: 空＝没读过，老构造一字不动。
    material_eids: tuple[EvidenceId, ...] = ()

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


@dataclass(frozen=True, slots=True)
class Contradiction:
    """一对**立场互斥**的主张（analyzer 读用户原话那一步判出来的）。

    判据：两侧立场互斥才算（"记录不许改" vs "点错了要能改"）；"只是还没定""还没想清楚"
    不算互斥——那是缺信息，该走缺信息那条路。

    给装配层用：题目同时覆盖两侧 ⇒ 系统盖章 `kind=conflict`，不再指望整合者记得标
    （P10 真跑：54 道题 0 条 conflict，全靠模型自觉）。
    """

    claim_id_a: str
    claim_id_b: str
    why: str

    def __post_init__(self) -> None:
        for name, value in (("claim_id_a", self.claim_id_a), ("claim_id_b", self.claim_id_b)):
            if not value.strip():
                raise ShapeError("互斥对子必须写明是哪两条主张", field=name)
        if self.claim_id_a == self.claim_id_b:
            raise ShapeError("互斥对子不能是同一条主张自己", claim_id=self.claim_id_a)
        if not self.why.strip():
            raise ShapeError("互斥对子必须写清为什么互斥", claim_id=self.claim_id_a)


@dataclass(frozen=True, slots=True)
class SeatConflict:
    """两个席位对同一命题持相反判断（整合层机械检测）。

    判据：同 about + 一条含肯定词、一条含否定词。
    保守检测——宁可漏报不可误报。

    `handle_*` 是那条判断的**账本句柄** `p{趟}:{条目}`（与 `assigner` 落编号、`_own_eid`
    反查同一个口径）——盖章时要靠它把两条判断换成出处编号 `E…`。
    """

    about: str                              # 冲突针对的目标
    seat_a: str                             # 席位 A 的 id
    claim_a: str                            # 席位 A 的判断文本
    handle_a: str                           # 席位 A 那条判断的账本句柄 `p{趟}:{条目}`
    seat_b: str                             # 席位 B 的 id
    claim_b: str                            # 席位 B 的判断文本
    handle_b: str                           # 席位 B 那条判断的账本句柄 `p{趟}:{条目}`
