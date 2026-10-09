"""L2 轮次观察：纯数据类型，无跨 context 导入。

提取逻辑在 `application/context.py`（应用层可以跨 context 导入）。
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ObservationType(StrEnum):
    """观察类型——只留 PM 场景有用的。"""

    SEAT_INSIGHT = "seat_insight"
    GAP_IDENTIFIED = "gap_identified"


@dataclass(frozen=True, slots=True)
class RoundObservation:
    """L2: 单条观察——记录一轮中发生的具体事件。"""

    obs_id: str
    round_id: str
    project_id: str
    obs_type: ObservationType
    title: str
    detail: str
    source_ref: str
    created_at: datetime = field(default_factory=datetime.now)
    related_sections: tuple[str, ...] = ()
    related_features: tuple[str, ...] = ()
