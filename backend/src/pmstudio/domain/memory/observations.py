"""L2 轮次观察：纯数据类型，无跨 context 导入。

提取逻辑在 `application/context.py`（应用层可以跨 context 导入）。
"""

from dataclasses import dataclass
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
    #: 归属轮次的时刻（ISO 文本），不是入库时刻：重试重跑要落在同一个时间点上，
    #: 用提取当时的时间会让同一轮重跑出两条"新"观察，去重判据也就跟着漂。
    #: `''` = 迁移前的老行，排最后（仓储 `recent()` 兜着，不当它是最新）。
    created_at: str
