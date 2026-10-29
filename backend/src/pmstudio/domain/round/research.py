"""轮次上的来源卡：**快照**，不是对研究产物的实时引用。

为什么要快照：挂上去的是"这一张卡"，研究之后被重跑、被删都不该让这一轮手里的卡变样
——一张卡进了轮次就成了这一轮的输入（与 `MaterialRef` 记块而不是记"哪份材料的第几块"
同一条理由）。

它**不是材料**：不进 `Round.materials`、不进材料池、不发新编号（E 在研究落库那一刻
就发好了）。它只负责让来源卡出现在提示词里。
"""

from dataclasses import dataclass

from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.research import (
    RESEARCH_SOURCE_CARD_CHARS,
    source_card_text,
)

__all__ = ["RoundResearchSource"]


@dataclass(frozen=True, slots=True)
class RoundResearchSource:
    """一张来源卡（快照）：**只有卡本身**——编号是身份，标题/网址/摘要是卡面。

    没有 `exploration_id` / `material_id`：前者没有消费者（快照的全部意义就是不回读），
    后者与编号重复（编号经账本能查到那条 `external` 出处）。要展示"来自哪次研究"时再加，
    那时它才有第一个读者。
    """

    eid: EvidenceId
    title: str
    url: str
    excerpt: str

    def __post_init__(self) -> None:
        for field, value in (
            ("eid", str(self.eid)),
            ("title", self.title),
            ("url", self.url),
            ("excerpt", self.excerpt),
        ):
            if not value.strip():
                raise ShapeError("来源卡的每一项都不能为空", field=field)
        # 与研究侧**同一把尺子**（`source_card_text` + 同一个上限）：轮次这侧不校验的话，
        # 任何新调用方都能塞进无界长卡，把上下文预算悄悄撑爆。
        card = source_card_text(title=self.title, url=self.url, excerpt=self.excerpt)
        if len(card) > RESEARCH_SOURCE_CARD_CHARS:
            raise ShapeError(
                f"一张来源卡最多 {RESEARCH_SOURCE_CARD_CHARS} 字，这条 {len(card)} 字",
                eid=str(self.eid),
                chars=len(card),
                limit=RESEARCH_SOURCE_CARD_CHARS,
            )
