"""L3 灵感档案：纯数据类型，无跨 context 导入。

存储逻辑在 `application/use_cases.py`（应用层可以跨 context 导入）。
"""

from dataclasses import dataclass
from enum import StrEnum


class InspirationOrigin(StrEnum):
    """灵感来源类型。"""

    DISCUSSION = "discussion"
    EXPLORATION = "exploration"
    SIM_INQUIRY = "sim_inquiry"


@dataclass(frozen=True, slots=True)
class InspirationItem:
    """L3: 灵感条目 — 来自讨论/探索/模拟的知识。"""

    item_id: str
    project_id: str
    origin: InspirationOrigin
    statement: str
