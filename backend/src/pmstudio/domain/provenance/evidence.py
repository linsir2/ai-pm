"""出处条目。六类来源，形状随 kind 变，形状不对直接拒收。"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pmstudio.domain.shared.errors import (
    DerivedFromRequired,
    EvidenceSourceShape,
    ShapeError,
)
from pmstudio.domain.shared.ids import EvidenceId, QuestionId, RoundId


class EvidenceKind(StrEnum):
    USER_INPUT = "user_input"  # 用户在哪一轮说的（带原话片段）
    CARD_ANSWER = "card_answer"  # 用户答的哪道题
    AGENT_DERIVED = "agent_derived"  # 席位判断，有依据
    AGENT_PROPOSED = "agent_proposed"  # 席位判断，无依据
    SYSTEM_DEFAULT = "system_default"  # 系统按结构规则补的（如跳过的题落开放问题）
    EXTERNAL = "external"  # 外部材料（v1 留位，暂无生产者）


TERMINAL_KINDS: frozenset[EvidenceKind] = frozenset(
    {EvidenceKind.USER_INPUT, EvidenceKind.CARD_ANSWER}
)


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    round_id: RoundId | None = None
    raw_span: str | None = None
    question_id: QuestionId | None = None
    seat_id: str | None = None
    derived_from: tuple[EvidenceId, ...] = ()
    rationale: str | None = None
    external_ref: str | None = None


def _require(value: object, message: str) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise EvidenceSourceShape(message)


@dataclass(frozen=True, slots=True)
class Evidence:
    eid: EvidenceId
    kind: EvidenceKind
    text: str
    source: EvidenceSource
    created_at: datetime
    supersedes: EvidenceId | None = None

    @classmethod
    def build(
        cls,
        *,
        eid: EvidenceId,
        kind: EvidenceKind,
        text: str,
        source: EvidenceSource,
        now: datetime,
        supersedes: EvidenceId | None = None,
    ) -> "Evidence":
        if not text.strip():
            raise ShapeError("出处条目的文本不能为空", eid=str(eid))
        _validate_source(kind, source)
        return cls(
            eid=eid, kind=kind, text=text, source=source, created_at=now, supersedes=supersedes
        )


def _validate_source(kind: EvidenceKind, source: EvidenceSource) -> None:
    if kind is EvidenceKind.USER_INPUT:
        _require(source.round_id, "user_input 必须带 round_id")
        _require(source.raw_span, "user_input 必须带 raw_span（原话片段，供跳回）")
    elif kind is EvidenceKind.CARD_ANSWER:
        _require(source.round_id, "card_answer 必须带 round_id")
        _require(source.question_id, "card_answer 必须带 question_id")
    elif kind is EvidenceKind.AGENT_DERIVED:
        _require(source.seat_id, "agent_derived 必须带 seat_id")
        _require(source.rationale, "agent_derived 必须带 rationale（为什么这么判断）")
        if not source.derived_from:
            raise DerivedFromRequired()
    elif kind is EvidenceKind.AGENT_PROPOSED:
        _require(source.seat_id, "agent_proposed 必须带 seat_id")
        _require(source.rationale, "agent_proposed 必须写清这是纯专业判断")
    elif kind is EvidenceKind.SYSTEM_DEFAULT:
        _require(source.round_id, "system_default 必须带 round_id")
        _require(source.rationale, "system_default 必须写清依据的是哪条结构规则")
    else:
        _require(source.external_ref, "external 必须带 external_ref")
    if source.derived_from and kind is not EvidenceKind.AGENT_DERIVED:
        raise EvidenceSourceShape(f"{kind} 不能带 derived_from，只有 agent_derived 才带")
