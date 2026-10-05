"""调研产物：假设与盲区。

假设必须**可证伪**（怎么被推翻 + 需要什么真实证据），盲区必须给出下一步——
这是"模拟推演不是用户数据"的口径在领域层的落点（`02` §11 探索口径）。
"""

from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.shared.errors import (
    ShapeError,
    UnfalsifiableHypothesis,
)
from pmstudio.domain.shared.ids import EvidenceId

MAX_INQUIRY_HYPOTHESES = 6
MAX_INQUIRY_BLIND_SPOTS = 3


class Grounding(StrEnum):
    """这条假设从哪来。`invented` = 凭空推演（允许，但不能伪装成有据）。"""

    DOCUMENT = "document"
    MATERIAL = "material"  # M8 暂无生产者（材料进调研在 M9）；形状先立着，免得以后改词
    INVENTED = "invented"


@dataclass(frozen=True, slots=True)
class Hypothesis:
    local_id: str
    statement: str
    grounding: Grounding
    based_on: tuple[EvidenceId, ...]
    falsification: str
    evidence_needed: str

    def __post_init__(self) -> None:
        if not self.local_id.strip():
            raise ShapeError("假设必须有 local_id")
        if not self.statement.strip():
            raise ShapeError("假设必须写清内容", local_id=self.local_id)
        if not self.falsification.strip():
            raise UnfalsifiableHypothesis(local_id=self.local_id, missing="怎么被推翻")
        if not self.evidence_needed.strip():
            raise UnfalsifiableHypothesis(
                local_id=self.local_id, missing="需要什么真实证据"
            )
        if self.grounding is Grounding.INVENTED:
            if self.based_on:
                raise ShapeError(
                    "凭空推演的假设不能带依据编号", local_id=self.local_id
                )
        elif not self.based_on:
            raise ShapeError(
                "从文档/材料推的假设必须带依据编号", local_id=self.local_id
            )


@dataclass(frozen=True, slots=True)
class BlindSpot:
    local_id: str
    statement: str
    next_step: str

    def __post_init__(self) -> None:
        if not self.local_id.strip():
            raise ShapeError("盲区必须有 local_id")
        if not self.statement.strip():
            raise ShapeError("盲区必须写清内容", local_id=self.local_id)
        if not self.next_step.strip():
            raise ShapeError("盲区必须写清下一步去哪问", local_id=self.local_id)


class CandidateState(StrEnum):
    NEW = "new"
    KEPT = "kept"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class Candidate:
    """过目台看到的一条候选（假设/盲区的统一投影，**不是第二份存储**）。

    假设的 `grounding/falsification/evidence_needed` 与盲区的 `next_step` 两个专有形状
    都用可选字段表达——它只服务投影，不做业务判断。
    """

    candidate_id: str
    kind: str  # "hypothesis" | "blind_spot"
    statement: str
    state: CandidateState
    kept_as: str | None = None  # "seed" | "material"
    kept_ref: str | None = None  # material_id（收下为种子时为空）
    grounding: Grounding | None = None
    based_on: tuple[EvidenceId, ...] = ()
    falsification: str | None = None
    evidence_needed: str | None = None
    next_step: str | None = None
