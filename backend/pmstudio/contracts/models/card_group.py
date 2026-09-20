"""C8 Card Group（卡片组）。

**一组卡片 = 一次裁决。** 它不是输出的终点，只是中途的一道关卡：
反复几组裁决卡之后，最后一张填充卡收尾，才轮到写文档。

**v2（DDD 重构）**：裁决行为收进聚合——`apply_answer` 把用户回应合并进
卡片组（更新卡片的 answer/status 与提案的 kept/removed），`kept_proposals`
给出 M20 写入时要写的提案。M20 从黑板读到卡片组后，直接问聚合要答案，
不再由调用方把 group / answers / scope 全传进来（D1 修正）。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import CardGroupState, CardKind, ProposalState
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.card import Card, CardAnswer, Proposal

# CONTRACTS C8：一批最多 5 张，**没有例外**。
MAX_CARDS_PER_GROUP = 5


class CardGroup(ContractModel):
    """一批卡片，连同用户的裁决状态。"""

    group_id: str
    cards: tuple[Card, ...]
    round_id: str
    state: CardGroupState = CardGroupState.ANSWERING
    result_version_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "CardGroup":
        if not self.group_id:
            raise ContractViolation("group_id 不能为空")
        if not self.round_id:
            raise ContractViolation("卡片组必须记住属于哪一轮——写范围从这一轮取")
        if not self.cards:
            raise ContractViolation("空卡片组没有意义")
        if len(self.cards) > MAX_CARDS_PER_GROUP:
            raise ContractViolation(
                f"一批最多 {MAX_CARDS_PER_GROUP} 张，收到 {len(self.cards)} 张——说明问题没拆干净"
            )

        seen: set[str] = set()
        for card in self.cards:
            if card.card_id in seen:
                raise ContractViolation(f"同一组里出现了重复的 card_id：{card.card_id}")
            seen.add(card.card_id)

        has_fill_card = any(card.kind is CardKind.FILL for card in self.cards)
        if has_fill_card and len(self.cards) != 1:
            raise ContractViolation("填充卡单独成批——它的批里只有 1 张")

        if self.result_version_id is not None:
            if not has_fill_card:
                raise ContractViolation("不含填充卡的卡片组不产生文档版本（I16）")
            if self.state is not CardGroupState.CONFIRMED:
                raise ContractViolation("还没确认就没有写入结果")
        return self

    def apply_answer(self, answer: CardAnswer) -> "CardGroup":
        """把用户对一张卡的回应合并进卡片组（v2 领域行为）。

        - 卡片本身的 answer / status 更新；
        - 填充卡：`proposal_states` 逐条覆盖提案的 kept/removed；
          没提到的提案保持原状态（未裁决 = 默认 kept，与旧行为一致）。
        返回新实例，不原地改（ContractModel 是不可变值对象）。
        """
        updated_cards = list(self.cards)
        for idx, card in enumerate(updated_cards):
            if card.card_id != answer.card_id:
                continue
            if card.kind is CardKind.FILL and answer.proposal_states:
                proposals = tuple(
                    proposal.model_copy(
                        update={
                            "state": answer.proposal_states.get(
                                proposal.proposal_id, proposal.state
                            )
                        }
                    )
                    for proposal in card.proposals
                )
                card = card.model_copy(update={"proposals": proposals})
            updated_cards[idx] = card.model_copy(update={
                "answer": answer.answer,
                "status": answer.status,
            })
            break
        return self.model_copy(update={"cards": tuple(updated_cards)})

    def kept_proposals(self) -> tuple[Proposal, ...]:
        """填充卡里 `state = kept` 的提案——M20 写入时取它（v2 领域行为）。

        只统计填充卡；没有填充卡的组返回空元组（I16：不产生文档版本）。
        """
        kept: list[Proposal] = []
        for card in self.cards:
            if card.kind is not CardKind.FILL:
                continue
            for proposal in card.proposals:
                if proposal.state is ProposalState.KEPT:
                    kept.append(proposal)
        return tuple(kept)
