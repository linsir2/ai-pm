"""C14 Discussion Round（讨论轮）。

讨论区只产素材，不产结论（I11）。一轮的默认结束条件是"每个角色各发言一次"。
**不保存内部思考过程**，只保存每个角色的最终输出（I12）。
"""

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import DiscussionEndReason, DiscussionState
from pmstudio.contracts.models.base import ContractModel

# CONTRACTS C14：参与角色上限 4 个。
MAX_PARTICIPANTS = 4


class Utterance(ContractModel):
    """一条发言：角色 + 界面文本 + 指向结构化信息包的 id。

    `packet_id` 是不透明引用——C4 的形状是编排层自有，这里不解构。
    """

    role_id: str
    text: str
    packet_id: str

    @model_validator(mode="after")
    def _check(self) -> "Utterance":
        if not self.role_id:
            raise ContractViolation("发言必须记住是谁说的")
        if not self.text.strip():
            raise ContractViolation("发言不能是空的")
        if not self.packet_id:
            raise ContractViolation("发言必须能追回它对应的信息包")
        return self


class DiscussionRound(ContractModel):
    """一轮讨论。"""

    round_id: str
    participants: tuple[str, ...]
    utterances: tuple[Utterance, ...] = Field(default_factory=tuple)
    state: DiscussionState = DiscussionState.RUNNING
    end_reason: DiscussionEndReason | None = None
    idea_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check(self) -> "DiscussionRound":
        if not self.round_id:
            raise ContractViolation("round_id 不能为空")
        if not self.participants:
            raise ContractViolation("讨论轮至少要有一个人")
        if len(self.participants) > MAX_PARTICIPANTS:
            raise ContractViolation(f"参与角色上限 {MAX_PARTICIPANTS} 个，收到 {len(self.participants)} 个")

        seen: set[str] = set()
        for role_id in self.participants:
            if not role_id:
                raise ContractViolation("参与角色不能是空 id")
            if role_id in seen:
                raise ContractViolation(f"同一个角色被选了两次：{role_id}")
            seen.add(role_id)

        spoken: set[str] = set()
        for utterance in self.utterances:
            if utterance.role_id not in seen:
                raise ContractViolation(f"发言的角色不在这一轮的参与者里：{utterance.role_id}")
            if utterance.role_id in spoken:
                raise ContractViolation(f"一轮里每个角色只发一次言：{utterance.role_id}")
            spoken.add(utterance.role_id)

        if self.state is DiscussionState.FINISHED:
            if self.end_reason is None:
                raise ContractViolation("讨论结束了却没说为什么结束")
        elif self.end_reason is not None:
            raise ContractViolation("还没结束就不该有结束原因")
        return self
