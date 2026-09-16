"""C12 Blackboard（黑板）：五个区块的形状。

黑板是"这一轮任务的共享状态"。它按区块划分，**每个区块只有一个直接写者**（编排层，I19）。

三条容易混的规则，这里是它们的唯一出处：

1. **活对象放在区块里，冻结出来的东西落账本。** `card_group` 区块装当前轮的 C8 实例
   （含用户回应）——它还没冻结，账本里没有它的位置；一旦 `state = confirmed`，整组作为
   卡片历史落 L7，`confirmed` 区块只留 id。
2. **写入即广播**：只有 `round` 和 `card_group` 两个区块的变更会发事件（`REGION_BROADCAST`）。
3. **读权限**：C12 各区块的"谁读"是用途说明，不是权限；权限上只有 `claims` 是限的
   （只有编排层能读），其余区块 §5.3 的 `read` 对所有层开放。

`claims` 区块**只声明存在**：它装的是 C4 数组，而 C4 是编排层的内部通用语，形状归 L2 自有，
等 C4 定义后回填（directory.md §4）。在那之前，契约层只声明这块地方的存在、单写者与不可读性。
"""

from datetime import datetime
from typing import Final

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    ContextBlockSource,
    Credibility,
    EventType,
    ProducerIdentity,
    RegionName,
    RoundEndReason,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.models.base import ContractModel
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.scope import Scope


class ContextBlock(ContractModel):
    """上下文块：这一轮按优先级带上来的东西。

    它**不是新契约**——`content` 来自某个已有契约的对象，`ref` 指回那个对象。
    """

    source: ContextBlockSource
    ref: str
    content: str = ""
    priority: int = Field(ge=0)
    credibility: Credibility | None = None

    @model_validator(mode="after")
    def _check(self) -> "ContextBlock":
        if not self.ref.strip():
            raise ContractViolation("上下文块必须指回一个真实对象（ref 不能为空）")

        if self.source is ContextBlockSource.MATERIAL:
            if self.credibility is None:
                raise ContractViolation("材料块的 credibility 继承自 C5，不能缺")
        elif self.credibility is not None:
            raise ContractViolation("credibility 只有材料块有")
        return self


class ContextRegion(ContractModel):
    """`context` 区块：这一轮组装出来的上下文，含被裁掉的。"""

    blocks: tuple[ContextBlock, ...] = Field(default_factory=tuple)
    dropped: tuple[ContextBlock, ...] = Field(default_factory=tuple)
    assembled_at: datetime


class RoundRegion(ContractModel):
    """`round` 区块：这一轮在干什么。它的字段同时就是 `rounds` 表的列。"""

    round_id: str
    project_id: str
    entry: RoundEntry
    user_input: str
    scope: Scope
    phase: RoundPhase
    ended_at: datetime | None = None
    end_reason: RoundEndReason | None = None

    @model_validator(mode="after")
    def _check(self) -> "RoundRegion":
        if not self.round_id:
            raise ContractViolation("round_id 不能为空")
        if not self.project_id:
            raise ContractViolation("轮次必须记住属于哪个项目")
        if not self.user_input.strip():
            raise ContractViolation("每一轮都有一句用户输入")

        finished = self.phase in (RoundPhase.DONE, RoundPhase.FAILED)
        if finished:
            if self.ended_at is None or self.end_reason is None:
                raise ContractViolation("轮次结束必须记下什么时候结束、为什么结束")
        elif self.ended_at is not None or self.end_reason is not None:
            raise ContractViolation("还没结束就不该有结束时间和结束原因")
        return self


class ConfirmedRegion(ContractModel):
    """`confirmed` 区块：这一轮确认下来的东西，**只放 id**。"""

    card_group_ids: tuple[str, ...] = Field(default_factory=tuple)
    memory_ids: tuple[str, ...] = Field(default_factory=tuple)


ClaimsValue = tuple[object, ...]
"""`claims` 区块的值：**形状归编排层自有**，这里只声明"存在"。

等 C4 定义后回填；契约层不 import 编排层（directory.md §3.1）。
"""

RegionValue = RoundRegion | ContextRegion | ClaimsValue | CardGroup | ConfirmedRegion
"""任一区块的值。类型不匹配由黑板在写入时拒（R0.4）。"""


REGION_VALUE_TYPES: Final = {
    RegionName.ROUND: RoundRegion,
    RegionName.CONTEXT: ContextRegion,
    RegionName.CLAIMS: ClaimsValue,
    RegionName.CARD_GROUP: CardGroup,
    RegionName.CONFIRMED: ConfirmedRegion,
}
"""每个区块装什么。五个区块一个不少。"""


REGION_WRITERS: Final = {
    RegionName.ROUND: frozenset({ProducerIdentity.ORCHESTRATION}),
    RegionName.CONTEXT: frozenset({ProducerIdentity.ORCHESTRATION}),
    RegionName.CLAIMS: frozenset({ProducerIdentity.ORCHESTRATION}),
    RegionName.CARD_GROUP: frozenset({ProducerIdentity.ORCHESTRATION}),
    RegionName.CONFIRMED: frozenset({ProducerIdentity.ORCHESTRATION}),
}
"""I19 区块单写者：黑板是"这一轮的工作台"，维护它的人就是唯一写者。"""


REGION_READERS: Final = {
    RegionName.ROUND: None,
    RegionName.CONTEXT: None,
    RegionName.CLAIMS: frozenset({ProducerIdentity.ORCHESTRATION}),
    RegionName.CARD_GROUP: None,
    RegionName.CONFIRMED: None,
}
"""读权限：`None` = 所有层可读（§5.3）；只有 `claims` 是限的（C12）。"""


REGION_BROADCAST: Final = {
    RegionName.ROUND: EventType.ROUND_UPDATED,
    RegionName.CARD_GROUP: EventType.CARD_GROUP_UPDATED,
}
"""写入即广播的映射：写这两个区块会自动发事件；`context` / `claims` / `confirmed` 不发。"""


def assert_region_value(region: RegionName, value: object) -> None:
    """区块值必须与区块对得上。黑板写入时调它。

    单独一个函数，是因为 `REGION_VALUE_TYPES` 里 `claims` 是类型别名——`isinstance` 用不了
    参数化的泛型（`isinstance(x, tuple[object, ...])` 直接抛 TypeError），只能在这里显式分开判。
    """
    if region is RegionName.CLAIMS:
        if not isinstance(value, tuple):
            raise ContractViolation("claims 区块装的是编排层自有的主张数组（不透明序列）")
        return

    expected = REGION_VALUE_TYPES[region]
    if not isinstance(value, expected):
        raise ContractViolation(f"{region.value} 区块只能装 {expected.__name__}，收到 {type(value).__name__}")
