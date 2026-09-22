"""四个 v1 事件。事件只做通知与投影：摘掉全部订阅者，流程照样跑完。"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

ROUND_UPDATED = "round.updated"
PACKET_EMITTED = "packet.emitted"
DOC_CHANGED = "doc.changed"
GENERATION_FAILED = "generation.failed"

EVENT_NAMES = frozenset({ROUND_UPDATED, PACKET_EMITTED, DOC_CHANGED, GENERATION_FAILED})


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    project_id: str
    occurred_at: datetime
    seq: int = 0  # 由事件总线在 publish 时盖真序号；业务侧一律用默认值
    round_id: str | None = None
    payload: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.name not in EVENT_NAMES:
            raise ValueError(f"未定义的事件名：{self.name}")
        if self.seq < 0:
            raise ValueError(f"事件序号不能是负数：{self.seq}")
