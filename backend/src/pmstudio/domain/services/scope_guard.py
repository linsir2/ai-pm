"""写范围的两级设计（见 P1 计划 §3 决策 1）：

硬约束不在这里——不得新增未授权 feature 子树由 Document.apply 把关，
不得离开 13 章由 Target 构造把关。这里只负责一件事：让"跑出本轮去处"
在草案卡上看得见，而不是被系统静默拒绝。

若哪天要改成严格版：把下面 append 的地方换成 raise 就够了，写入端不用动。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.domain.round.draft import OriginType, Proposal
from pmstudio.domain.shared.ids import ProposalId
from pmstudio.domain.shared.target import Target

OUTSIDE_ROUND_SCOPE = "outside_round_scope"


@dataclass(frozen=True, slots=True)
class ScopeViolation:
    proposal_id: ProposalId
    target: Target
    code: str
    message: str


class ScopeGuard:
    def check(
        self, proposals: Sequence[Proposal], *, write_scope: frozenset[Target]
    ) -> tuple[ScopeViolation, ...]:
        violations: list[ScopeViolation] = []
        for proposal in proposals:
            if proposal.origin.type is OriginType.SYSTEM_DEFAULT:
                continue
            if any(proposal.target.is_within(scope) for scope in write_scope):
                continue
            violations.append(
                ScopeViolation(
                    proposal_id=proposal.proposal_id,
                    target=proposal.target,
                    code=OUTSIDE_ROUND_SCOPE,
                    message=(
                        f"这条落在 {proposal.target.label()}，不在本轮去处里"
                        "（席位替你判断的，确认前请看一眼）"
                    ),
                )
            )
        return tuple(violations)
