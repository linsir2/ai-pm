"""席位并行，编号必须由汇合点统一分配——这个汇合点就是它。

两条规则：只有"判断"进账本（"疑问"会变成题目，不占编号）；
同一席位内的 local_id 必须唯一，跨席位重名（都叫 O1）是正常的。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.opinion import OpinionForm, SeatReview
from pmstudio.domain.shared.errors import DuplicateLocalId
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId, RoundId


@dataclass(frozen=True, slots=True)
class OpinionAssignment:
    seat_id: str
    local_id: str
    eid: EvidenceId
    kind: EvidenceKind


class EvidenceAssigner:
    def assign_claims(
        self,
        claims: Sequence[Claim],
        *,
        ledger: EvidenceLedger,
        round_id: RoundId,
        now: datetime,
    ) -> tuple[Claim, ...]:
        assigned: list[Claim] = []
        for claim in claims:
            eid = ledger.record(
                kind=EvidenceKind.USER_INPUT,
                text=claim.text,
                source=EvidenceSource(
                    round_id=round_id,
                    raw_span=claim.raw_span,
                    # 主张读过的材料块：落编号时写进 derived_from，"材料→主张"才可查。
                    # 空元组＝老行为；非材料编号由 ledger.record 拦（形状见 evidence 法）。
                    derived_from=tuple(claim.material_eids),
                ),
                now=now,
            )
            assigned.append(replace(claim, eid=eid))
        return tuple(assigned)

    def assign_opinions(
        self,
        reviews: Sequence[SeatReview],
        *,
        ledger: EvidenceLedger,
        round_id: RoundId,
        now: datetime,
    ) -> tuple[OpinionAssignment, ...]:
        assignments: list[OpinionAssignment] = []
        for review in reviews:
            seen: set[str] = set()
            for opinion in review.opinions:
                if opinion.local_id in seen:
                    raise DuplicateLocalId(seat_id=review.seat_id, local_id=opinion.local_id)
                seen.add(opinion.local_id)
                if opinion.form is OpinionForm.QUESTION:
                    continue
                key = f"p{review.pass_index}:{opinion.local_id}"
                # 幂等只在**同一轮**内成立：趟数每轮从 1 重来，不带轮次找会把上一轮的编号
                # 当成"这条已经记过"（真跑抓到过：第二轮复用了第一轮的编号，账本文本对不上）。
                # 所以这里**不许**回落到老行（allow_legacy=False）——新的一轮该拿新号。
                existing = ledger.find_by_local(
                    seat_id=review.seat_id,
                    local_id=key,
                    round_id=round_id,
                    allow_legacy=False,
                )
                if existing is not None:  # 幂等：重试时复用第一次的编号，不占新号
                    assignments.append(
                        OpinionAssignment(
                            seat_id=review.seat_id,
                            local_id=opinion.local_id,
                            eid=existing.eid,
                            kind=existing.kind,
                        )
                    )
                    continue
                kind = opinion.kind_if_judgment
                eid = ledger.record(
                    kind=kind,
                    text=opinion.claim,
                    source=EvidenceSource(
                        round_id=round_id,
                        seat_id=review.seat_id,
                        local_id=key,
                        derived_from=opinion.evidence_refs,
                        rationale=opinion.because,
                    ),
                    now=now,
                )
                assignments.append(
                    OpinionAssignment(
                        seat_id=review.seat_id,
                        local_id=opinion.local_id,
                        eid=eid,
                        kind=kind,
                    )
                )
        return tuple(assignments)
