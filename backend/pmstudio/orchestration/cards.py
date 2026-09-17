"""M9 卡片组装器：把 M8 与 M28 的产出组装成卡片组。

这一轮它几乎只是"加 id + 打包"：≤5 张、一张卡一个议题、填充卡单独成批
这些规则已经在 C8 的模型里（test_c8_card_group.py 钉着），M9 不重复实现一遍。
"""

from collections.abc import Sequence

from pmstudio.common.ids import IdGenerator
from pmstudio.contracts.enums import CardGroupState
from pmstudio.contracts.models.card import Card
from pmstudio.contracts.models.card_group import CardGroup


class CardAssembler:
    """M9：卡片打包。"""

    def __init__(self, ids: IdGenerator) -> None:
        self._ids = ids

    def assemble(self, cards: Sequence[Card], *, round_id: str) -> CardGroup:
        """把一组卡片打包成 CardGroup。"""
        return CardGroup(
            group_id=self._ids.new_id("grp"),  # type: ignore[attr-defined]
            cards=tuple(cards),
            round_id=round_id,
            state=CardGroupState.ANSWERING,
        )
