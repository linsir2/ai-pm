"""WriteInstruction：authoring 侧的写入指令。

Proposal（round 上下文，带来源与理由）在 ProposalCommitter 里翻译成这个形状——
两个上下文因此不必互相 import。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import ContentWithoutEvidence, ShapeError
from pmstudio.domain.shared.ids import EntryId, EvidenceId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp


@dataclass(frozen=True, slots=True)
class WriteInstruction:
    instruction_id: str
    entry_id: EntryId
    op: WriteOp
    target: Target
    text: str
    refs: tuple[EvidenceId, ...]
    kind: BlockKind | None = None
    # 这条指令是人写的（手改文档）：落到的块要留痕，之后 AI 不能静默覆盖它。
    manual: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ShapeError("写入指令的文本不能为空", instruction_id=self.instruction_id)
        if not self.refs:
            raise ContentWithoutEvidence(what=f"写入指令 {self.instruction_id}")
