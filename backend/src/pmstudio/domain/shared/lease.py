"""跨进程租约的值对象：纯领域，不 import 任何外部包。

租约本身不写进 `Round` / `Exploration` 聚合——聚合是业务状态，租约是谁在跑它。
持有者身份只活在 `LeaseToken` 与 `LeaseScope`（application 层的 contextvar）里，
仓储在写之前用它跟行上的 lease 三列对账。
"""

from dataclasses import dataclass
from enum import StrEnum

from pmstudio.domain.shared.errors import ShapeError


class LeaseKind(StrEnum):
    """租约挂在哪张表上。值同时是 `SqliteLeaseStore` 选表的键。"""

    ROUND = "round"
    EXPLORATION = "exploration"


@dataclass(frozen=True, slots=True)
class LeaseToken:
    """一把已经拿到的租约。

    `token` 从 1 开始、只增不减；`0` 是"从未出租"的哨兵（列默认值）。
    `expires_at_ms` 是**绝对毫秒时间戳**（UTC epoch），跨进程共享系统时钟。
    """

    kind: LeaseKind
    target_id: str
    user_id: str
    holder: str
    token: int
    expires_at_ms: int

    def __post_init__(self) -> None:
        if not self.target_id.strip():
            raise ShapeError("租约必须带 target_id")
        if not self.user_id.strip():
            raise ShapeError("租约必须带 user_id", target_id=self.target_id)
        if not self.holder.strip():
            raise ShapeError("租约必须带持有者", target_id=self.target_id)
        if self.token < 1:
            raise ShapeError("租约 token 从 1 开始", token=self.token)
        if self.expires_at_ms < 0:
            raise ShapeError("租约过期时间不能为负", expires_at_ms=self.expires_at_ms)
