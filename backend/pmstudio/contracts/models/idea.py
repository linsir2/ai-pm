"""C6 Idea Candidate（思路候选）。

讨论区和主闭环之间的**唯一交接形态**。用户挑的是结构化后的候选，不是一屏聊天记录。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import IdeaStatus
from pmstudio.contracts.models.base import ContractModel


class IdeaCandidate(ContractModel):
    """一条可以勾选的思路。勾选后由 M13 在组装上下文时整批标成 `consumed`。"""

    idea_id: str
    discussion_round_id: str
    claim: str
    from_packet_ids: tuple[str, ...]
    suggested_label: str | None = None
    status: IdeaStatus = IdeaStatus.PENDING

    @model_validator(mode="after")
    def _check(self) -> "IdeaCandidate":
        if not self.idea_id:
            raise ContractViolation("idea_id 不能为空")
        if not self.discussion_round_id:
            raise ContractViolation("候选必须记住来自哪一轮讨论")
        if not self.claim.strip():
            raise ContractViolation("候选的 claim 不能为空——用户挑的就是这句话")
        if not self.from_packet_ids:
            raise ContractViolation("候选必须能追溯到 ≥1 条信息包（from_packet_ids）")

        seen: set[str] = set()
        for packet_id in self.from_packet_ids:
            if not packet_id:
                raise ContractViolation("from_packet_ids 里不能有空 id")
            if packet_id in seen:
                raise ContractViolation(f"同一条发言被记了两次：{packet_id}")
            seen.add(packet_id)
        return self
