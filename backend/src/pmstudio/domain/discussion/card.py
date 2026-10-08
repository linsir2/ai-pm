"""灵感卡片：讨论总结成的可收下条目。

只有两种来源：文档里有据的（document，必须带 E 编号）与凭空推演的
（invented，必须留空、收下时默认不勾——由上层决定）。
注意：没有 material——v1 讨论不读材料（M8 item.py:23 的反例）。
"""

from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId

MAX_CARDS_PER_THREAD = 6


class Grounding(StrEnum):
    DOCUMENT = "document"
    INVENTED = "invented"


class CardState(StrEnum):
    """卡片的过目状态（T5）：过一次目，单向——与候选的 `CandidateState` 同构。"""

    NEW = "new"
    KEPT = "kept"
    DISMISSED = "dismissed"


@dataclass(frozen=True, slots=True)
class Card:
    card_id: str  # 服务端 stamp C1..，模型不管 id
    statement: str
    grounding: Grounding
    based_on: tuple[EvidenceId, ...] = ()
    kept_ref: str | None = None  # material_id（收下为种子/忽略时为空）
    state: CardState = CardState.NEW
    kept_as: str | None = None  # "seed" | "material"

    def __post_init__(self) -> None:
        if not self.card_id.strip():
            raise ShapeError("卡片必须有 card_id")
        if not self.statement.strip():
            raise ShapeError("卡片必须写清内容", card_id=self.card_id)
        if self.grounding is Grounding.INVENTED:
            if self.based_on:
                raise ShapeError("凭空推演的卡片不能带依据编号", card_id=self.card_id)
        elif not self.based_on:
            raise ShapeError("从文档推的卡片必须带依据编号", card_id=self.card_id)
