"""统一联网申请：谁想查什么、为什么、批没批、查成没有。

它只是**申请**：模型（分析者 / 席位 / 整合者）提名，用户逐条批准或拒绝；
批准之后才由执行层联网。状态机只有两条路：

    proposed ──批准──> approved ──成功/失败──> done / failed
        └────拒绝────> rejected

每方最多 `MAX_WEB_QUERIES` 条**提案**。执行名额（每轮 3 条）由应用层算，不在这个对象上。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pmstudio.domain.round.web_query import MAX_WEB_QUERIES
from pmstudio.domain.shared.errors import ShapeError

#: 旧版复述卡只记"勾了哪条字符串"，没有逐条理由；迁移时补这一句，不编假理由。
LEGACY_REQUEST_WHY = "旧版复述卡勾选，无逐条理由"

WebOrigin = Literal["analyzer", "seat", "integrator"]


class WebRequestState(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    DONE = "done"
    FAILED = "failed"


def normalized_query(query: str) -> str:
    """去重用的检索词身份：空白折叠、大小写不敏感。

    只用于**判重**；展示与执行仍是 `WebRequest.query` 的原文。
    """

    return " ".join(query.split()).casefold()


#: 占执行名额的三态：批准即占位，执行完换成 done/failed 仍然占。
#: `proposed` 只是提名、`rejected` 是用户不要了，都不占。
USED_WEB_STATES = frozenset(
    (WebRequestState.APPROVED, WebRequestState.DONE, WebRequestState.FAILED)
)


def used_web_request_count(requests: Iterable[WebRequest]) -> int:
    """这一轮已经占了多少条执行名额（`approved + done + failed`）。"""

    return sum(1 for item in requests if item.state in USED_WEB_STATES)


@dataclass(frozen=True, slots=True)
class WebRequest:
    """一条联网申请：查什么、为什么、谁提的、现在到哪一步了。"""

    request_id: str
    query: str
    why: str
    origin: WebOrigin
    seat_id: str | None = None
    pass_index: int | None = None
    state: WebRequestState = WebRequestState.PROPOSED
    result_note: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ShapeError("联网申请必须有 id")
        if not self.query.strip():
            raise ShapeError("检索词不能为空")
        if not self.why.strip():
            raise ShapeError("检索词必须写清为什么要查外部事实", query=self.query)
        if self.origin not in ("analyzer", "seat", "integrator"):
            raise ShapeError(f"联网申请只能来自三方：{self.origin}")


__all__ = [
    "LEGACY_REQUEST_WHY",
    "MAX_WEB_QUERIES",
    "USED_WEB_STATES",
    "WebOrigin",
    "WebRequest",
    "WebRequestState",
    "normalized_query",
    "used_web_request_count",
]
