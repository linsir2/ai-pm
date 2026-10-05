"""类型化标识符。运行时都是 str/int，靠标注防止把 block id 当 evidence id 用。"""

from typing import NewType

ProjectId = NewType("ProjectId", str)
DocumentId = NewType("DocumentId", str)
SectionId = NewType("SectionId", str)
BlockId = NewType("BlockId", str)
EntryId = NewType("EntryId", str)
FeatureSlug = NewType("FeatureSlug", str)
EvidenceId = NewType("EvidenceId", str)
RoundId = NewType("RoundId", str)
ExplorationId = NewType("ExplorationId", str)
FormId = NewType("FormId", str)
QuestionId = NewType("QuestionId", str)
ProposalId = NewType("ProposalId", str)
DraftId = NewType("DraftId", str)

EVIDENCE_PREFIX = "E"
PREFERENCE_AREA_ID = BlockId("preference-area")


def evidence_id(seq: int) -> EvidenceId:
    if seq < 1:
        raise ValueError(f"出处编号从 1 开始，收到 {seq}")
    return EvidenceId(f"{EVIDENCE_PREFIX}{seq}")


def evidence_seq(eid: EvidenceId | str) -> int:
    text = str(eid)
    if not text.startswith(EVIDENCE_PREFIX):
        raise ValueError(f"出处编号必须以 {EVIDENCE_PREFIX} 开头：{text}")
    tail = text[len(EVIDENCE_PREFIX) :]
    if not tail.isdigit():
        raise ValueError(f"出处编号格式非法：{text}")
    return int(tail)
