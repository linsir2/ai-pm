"""C8 Card Group（卡片组）。

**一组卡片 = 一次裁决。** 它不是输出的终点，只是中途的一道关卡：
反复几组裁决卡之后，最后一张填充卡收尾，才轮到写文档。
"""

from pydantic import model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import CardGroupState, CardKind
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.card import Card

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
