"""可追溯不变式（E4）：顺着 refs → derived_from 必须落到用户输入或问卷答案。

落不到的**不是错误**，而是"提议"——它照样能进草案卡，只是默认不勾选。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.evidence_kind import TERMINAL_KINDS
from pmstudio.domain.shared.ids import EvidenceId


@dataclass(frozen=True, slots=True)
class TraceResult:
    traceable: bool
    roots: tuple[EvidenceId, ...]
    reason: str


class TraceabilityChecker:
    def check(self, refs: Sequence[EvidenceId], *, ledger: EvidenceLedger) -> TraceResult:
        if not refs:
            return TraceResult(traceable=False, roots=(), reason="没有编号")
        roots: list[EvidenceId] = []
        for eid in refs:
            for evidence in ledger.chain(eid):
                if evidence.kind in TERMINAL_KINDS and evidence.eid not in roots:
                    roots.append(evidence.eid)
        if not roots:
            return TraceResult(
                traceable=False,
                roots=(),
                reason="链上没有用户输入或问卷答案（依据全是席位自己的判断）",
            )
        return TraceResult(traceable=True, roots=tuple(roots), reason="")
