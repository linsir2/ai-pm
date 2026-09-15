"""C7 Card（卡片）与它的子形状。

卡片是**系统在长任务过程中向你求助的一种方式**。四种 kind 用的字段不一样，
分歧卡是"选一个"（`options`），填充卡是"要不要"（`proposals`）——两个字段不能互换。
"""

from collections.abc import Iterable

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import BlockOpKind, CardKind, CardStatus, ProposalState
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.citation import Citation


class CardOption(ContractModel):
    """分歧卡里的一个备选。**分歧卡的责任是摆明分歧**，所以每个备选各自带依据。"""

    option_id: str
    text: str
    citations: tuple[Citation, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check(self) -> "CardOption":
        if not self.option_id:
            raise ContractViolation("option_id 不能为空")
        if not self.text.strip():
            raise ContractViolation("备选方案不能用空话表达")
        return self


class Proposal(ContractModel):
    """填充卡里的一条提案：写到哪个字段、怎么写、写什么。"""

    proposal_id: str
    target_label: str
    op: BlockOpKind
    content: str
    state: ProposalState = ProposalState.KEPT
    citations: tuple[Citation, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check(self) -> "Proposal":
        if not self.proposal_id:
            raise ContractViolation("proposal_id 不能为空")
        if not self.target_label.strip():
            raise ContractViolation("提案必须说清落到哪个字段——M28 落位、M20 强制都靠它")
        if not self.content.strip():
            raise ContractViolation("提案内容不能为空")
        return self


class CardAnswer(ContractModel):
    """用户对一张卡的回应（CONTRACTS §5.3 `submitCards` 的 `answers[]` 项）。

    点按钮和打字地位相同——两种回应都落在 `answer` 里。
    """

    card_id: str
    status: CardStatus
    answer: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "CardAnswer":
        if not self.card_id:
            raise ContractViolation("回应的 card_id 不能为空")
        if self.status is CardStatus.ANSWERED and not (self.answer or "").strip():
            raise ContractViolation("标成已回应就必须有回应内容")
        return self


class Card(ContractModel):
    """一张卡。`kind` 决定它长什么样、用哪些字段。"""

    card_id: str
    kind: CardKind
    prompt: str
    options: tuple[CardOption, ...] = Field(default_factory=tuple)
    proposals: tuple[Proposal, ...] = Field(default_factory=tuple)
    answer: str | None = None
    status: CardStatus = CardStatus.PENDING

    @model_validator(mode="after")
    def _check(self) -> "Card":
        if not self.card_id:
            raise ContractViolation("card_id 不能为空")
        if not self.prompt.strip():
            raise ContractViolation("卡片必须有一句给用户看的话")

        if self.kind in (CardKind.UNDERSTANDING, CardKind.QUESTION):
            if self.options or self.proposals:
                raise ContractViolation(f"{self.kind.value} 卡不带备选也不带提案")
        elif self.kind is CardKind.CONFLICT:
            if not self.options:
                raise ContractViolation("分歧卡必须把分歧摆出来（options 不能为空）")
            if self.proposals:
                raise ContractViolation("分歧卡不能带提案——它问的是「选一个」，不是「要不要」")
        elif self.kind is CardKind.FILL:
            if not self.proposals:
                raise ContractViolation("填充卡必须带提案数组")
            if self.options:
                raise ContractViolation("填充卡不能带备选——它问的是「要不要」，不是「选一个」")

        self._reject_duplicates((option.option_id for option in self.options), "option_id")
        self._reject_duplicates((proposal.proposal_id for proposal in self.proposals), "proposal_id")
        return self

    @staticmethod
    def _reject_duplicates(ids: Iterable[str], name: str) -> None:
        seen: set[str] = set()
        for value in ids:
            if value in seen:
                raise ContractViolation(f"同一张卡里出现了重复的 {name}：{value}")
            seen.add(value)
