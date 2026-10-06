"""出处账本：编号全局唯一、只增、永不复用。"""

from dataclasses import replace
from datetime import datetime

from pmstudio.domain.provenance.evidence import Evidence, EvidenceKind, EvidenceSource
from pmstudio.domain.shared.errors import (
    DuplicateEvidenceId,
    EvidenceSourceShape,
    SelfDerivation,
    ShapeError,
    UnknownEvidence,
)
from pmstudio.domain.shared.ids import (
    EvidenceId,
    ProjectId,
    RoundId,
    evidence_id,
    evidence_seq,
)


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
        # 用户侧条目的材料门：主张可以引用**读过**的材料块（external/simulated），
        # 但不能引用别的判断/主张——根必须留在用户手里或材料本身。
        # 这是 `_validate_source` 开的那扇门的守卫：形状层只管"能不能带"，这里管"带的对不对"。
        if source.derived_from and kind is EvidenceKind.USER_INPUT:
            non_material = [
                str(ref)
                for ref in source.derived_from
                if self._by_id[ref].kind
                not in (EvidenceKind.EXTERNAL, EvidenceKind.SIMULATED)
            ]
            if non_material:
                raise EvidenceSourceShape(
                    "主张只能引用材料块的编号（读过的）："
                    + "、".join(non_material)
                    + "不是材料",
                    eid=str(eid),
                )
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

    def find_by_local(
        self,
        *,
        seat_id: str,
        local_id: str,
        round_id: RoundId | None = None,
        allow_legacy: bool = True,
    ) -> Evidence | None:
        """按"哪个席位、哪一轮、哪条意见"找已记的出处。

        `local_id` 存的是 `p{趟}:{O1}`，而**趟数每轮都从 1 重来**——只按 (席位, 条目)
        找会串到上一轮。真跑抓到过（P22 回归）：第二轮 `seat.permissions:O3` 解析到了
        第一轮的编号 E15，账本文本与那条意见对不上（用户点编号看到的是别的判断）。

        三条口径：

        - `round_id=None`：老行为（按 (席位, 条目) 取第一条）——只有历史调用点才该这么用；
        - `round_id` 给了：**精确命中那一轮**；
        - `allow_legacy`：精确命中不到时，允许回落到"没有轮次信息"的老行
          （P22 之前写的出处 `source.round_id` 是空的）。**写入端必须传 False**——
          否则新的一轮会复用老行的编号，正是真跑里串号的那条路。
        """

        legacy: Evidence | None = None
        for evidence in self._by_id.values():
            source = evidence.source
            if source.seat_id != seat_id or source.local_id != local_id:
                continue
            if round_id is None:
                return evidence
            if source.round_id == round_id:
                return evidence
            if allow_legacy and source.round_id is None and legacy is None:
                legacy = evidence
        return legacy

    def is_superseded(self, eid: EvidenceId) -> bool:
        return eid in self._superseded_by
