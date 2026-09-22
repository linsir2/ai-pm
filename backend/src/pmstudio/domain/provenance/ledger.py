"""出处账本：编号全局唯一、只增、永不复用。"""

from dataclasses import replace
from datetime import datetime

from pmstudio.domain.provenance.evidence import Evidence, EvidenceKind, EvidenceSource
from pmstudio.domain.shared.errors import (
    DuplicateEvidenceId,
    SelfDerivation,
    ShapeError,
    UnknownEvidence,
)
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, evidence_id, evidence_seq


class EvidenceLedger:
    def __init__(self, *, project_id: ProjectId, evidences: tuple[Evidence, ...] = ()) -> None:
        self._project_id = project_id
        self._by_id: dict[EvidenceId, Evidence] = {}
        for evidence in evidences:
            if evidence.eid in self._by_id:
                raise DuplicateEvidenceId(eid=evidence.eid)
            self._by_id[evidence.eid] = evidence
        self._superseded_by: dict[EvidenceId, EvidenceId] = {}
        for evidence in evidences:
            if evidence.supersedes is None:
                continue
            if evidence.supersedes not in self._by_id:
                raise UnknownEvidence(eid=evidence.supersedes)
            if evidence.supersedes in self._superseded_by:
                raise ShapeError(
                    "同一旧编号被两条修订指向", supersedes=str(evidence.supersedes)
                )
            self._superseded_by[evidence.supersedes] = evidence.eid
        self._next_seq = 1 + max((evidence_seq(item.eid) for item in evidences), default=0)

    @classmethod
    def empty(cls, project_id: ProjectId) -> "EvidenceLedger":
        return cls(project_id=project_id)

    @classmethod
    def rehydrate(
        cls, *, project_id: ProjectId, evidences: tuple[Evidence, ...]
    ) -> "EvidenceLedger":
        return cls(project_id=project_id, evidences=evidences)

    @property
    def project_id(self) -> ProjectId:
        return self._project_id

    def __len__(self) -> int:
        return len(self._by_id)

    def evidences(self) -> tuple[Evidence, ...]:
        return tuple(sorted(self._by_id.values(), key=lambda item: evidence_seq(item.eid)))

    def record(
        self,
        *,
        kind: EvidenceKind,
        text: str,
        source: EvidenceSource,
        now: datetime,
        eid: EvidenceId | None = None,
        supersedes: EvidenceId | None = None,
    ) -> EvidenceId:
        if eid is None:
            eid = evidence_id(self._next_seq)
        if eid in self._by_id:
            raise DuplicateEvidenceId(eid=eid)
        for ref in source.derived_from:
            if ref == eid:
                raise SelfDerivation(eid=eid)
            if ref not in self._by_id:
                raise UnknownEvidence(eid=ref)
        if supersedes is not None:
            if supersedes not in self._by_id:
                raise UnknownEvidence(eid=supersedes)
            if supersedes in self._superseded_by:
                raise ShapeError("这条已经被修订过了", eid=str(supersedes))
        evidence = Evidence.build(
            eid=eid, kind=kind, text=text, source=source, now=now, supersedes=supersedes
        )
        self._by_id[eid] = evidence
        if supersedes is not None:
            self._superseded_by[supersedes] = eid
        self._next_seq = max(self._next_seq, evidence_seq(eid) + 1)
        return eid

    def revise(
        self,
        eid: EvidenceId,
        *,
        text: str,
        now: datetime,
        rationale: str | None = None,
    ) -> EvidenceId:
        old = self.get(eid)
        source = replace(old.source, rationale=rationale or f"修订 {eid}")
        return self.record(kind=old.kind, text=text, source=source, now=now, supersedes=eid)

    def get(self, eid: EvidenceId) -> Evidence:
        evidence = self._by_id.get(eid)
        if evidence is None:
            raise UnknownEvidence(eid=eid)
        return evidence

    def chain(self, eid: EvidenceId) -> tuple[Evidence, ...]:
        ordered: list[Evidence] = []
        seen: set[EvidenceId] = set()
        stack: list[Evidence] = [self.get(eid)]
        while stack:
            current = stack.pop()
            if current.eid in seen:
                continue
            seen.add(current.eid)
            ordered.append(current)
            stack.extend(self.get(ref) for ref in current.source.derived_from)
        return tuple(ordered)

    def superseded_by(self, eid: EvidenceId) -> EvidenceId | None:
        return self._superseded_by.get(eid)

    def is_superseded(self, eid: EvidenceId) -> bool:
        return eid in self._superseded_by
