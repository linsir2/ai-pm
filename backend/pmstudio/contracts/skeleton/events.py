"""C13 Event（事件）与八个 payload。

事件只做一件事：**把状态变化通知给关心它的层**。它不携带完整数据——只带"什么变了"
和最小定位信息；订阅者要看细节，回黑板或存储读。

三条规矩：

1. **一个状态对象只有一个事件类型**，变化性质写进 payload，订阅者按 payload 过滤。
2. **事件必须由状态的生产者发**，发布时带身份，`PRODUCERS` 是白名单。
3. **每条 publish 都落流水**（`EventLogEntry`），它回答"这件事到底发了没有"。
"""

from datetime import datetime

from pydantic import Field, model_validator

from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    CardGroupState,
    EventType,
    MemoryStatus,
    MemoryType,
    ProducerIdentity,
    RegistryKind,
    RegistryStatus,
    RoundEndReason,
    RoundEntry,
    RoundPhase,
    ToolInvocationStatus,
    VersionTrigger,
    assert_phase_matches_reason,
)
from pmstudio.contracts.models.base import ContractModel


def _require(**values: str) -> None:
    for name, value in values.items():
        if not value.strip():
            raise ContractViolation(f"{name} 不能为空")


class RoundUpdatedPayload(ContractModel):
    """`round.updated`：开轮、每次 phase 变化、收尾。开轮就是第一次 phase 变化。"""

    round_id: str
    project_id: str
    entry: RoundEntry
    phase: RoundPhase
    from_phase: RoundPhase | None = None
    end_reason: RoundEndReason | None = None

    @model_validator(mode="after")
    def _check(self) -> "RoundUpdatedPayload":
        _require(round_id=self.round_id, project_id=self.project_id)
        finished = self.phase in (RoundPhase.DONE, RoundPhase.FAILED)
        if finished:
            if self.end_reason is None:
                raise ContractViolation("这一轮结束了却没说为什么结束")
            # 与 `RoundRegion` 用同一张表：两头不一致就会出现"库里写了、广播构造不出来"
            assert_phase_matches_reason(self.phase, self.end_reason)
        elif self.end_reason is not None:
            raise ContractViolation("还没结束就不该有结束原因")
        return self


class CardGroupUpdatedPayload(ContractModel):
    """`card_group.updated`：卡片组出现 / 更新 / 冻结。确认就是 `state = confirmed` 那一次。"""

    group_id: str
    round_id: str
    state: CardGroupState
    from_state: CardGroupState | None = None
    card_count: int = Field(ge=0)
    has_fill_card: bool

    @model_validator(mode="after")
    def _check(self) -> "CardGroupUpdatedPayload":
        _require(group_id=self.group_id, round_id=self.round_id)
        return self


class DocChangedPayload(ContractModel):
    """`doc.changed`：AI 写入、用户手改、回退走同一个事件。"""

    doc_id: str
    version_id: str
    seq: int = Field(ge=1)
    trigger: VersionTrigger
    block_ids: tuple[str, ...] = Field(default_factory=tuple)

    @model_validator(mode="after")
    def _check(self) -> "DocChangedPayload":
        _require(doc_id=self.doc_id, version_id=self.version_id)
        return self


class DiscussionUtteranceAddedPayload(ContractModel):
    """`discussion.utterance_added`：讨论区一条一条冒出来的发言。"""

    round_id: str
    role_id: str
    packet_id: str
    index: int = Field(ge=0)

    @model_validator(mode="after")
    def _check(self) -> "DiscussionUtteranceAddedPayload":
        _require(round_id=self.round_id, role_id=self.role_id, packet_id=self.packet_id)
        return self


class MemoryUpdatedPayload(ContractModel):
    """`memory.updated`：候选产生、晋升、失效、退役都走它。"""

    memory_id: str
    project_id: str
    type: MemoryType
    status: MemoryStatus
    from_status: MemoryStatus | None = None

    @model_validator(mode="after")
    def _check(self) -> "MemoryUpdatedPayload":
        _require(memory_id=self.memory_id, project_id=self.project_id)
        return self


class RegistryUpdatedPayload(ContractModel):
    """`registry.updated`：注册条目状态变化——它不绑轮次。"""

    id: str
    kind: RegistryKind
    status: RegistryStatus
    from_status: RegistryStatus | None = None

    @model_validator(mode="after")
    def _check(self) -> "RegistryUpdatedPayload":
        _require(id=self.id)
        return self


class ToolInvokedPayload(ContractModel):
    """`tool.invoked`：一次工具调用的两端。

    `role_id` 可以为空——检索是记忆层在组装上下文时发起的，那种调用没有角色。
    """

    round_id: str
    tool_id: str
    status: ToolInvocationStatus
    role_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ToolInvokedPayload":
        _require(round_id=self.round_id, tool_id=self.tool_id)
        return self


class GenerationFailedPayload(ContractModel):
    """`generation.failed`：失败在哪一步、人话的原因、能不能重试——三样必须都在。"""

    round_id: str
    step: RoundPhase
    reason: str
    retryable: bool

    @model_validator(mode="after")
    def _check(self) -> "GenerationFailedPayload":
        _require(round_id=self.round_id, reason=self.reason)
        return self


EventPayload = (
    RoundUpdatedPayload
    | CardGroupUpdatedPayload
    | DocChangedPayload
    | DiscussionUtteranceAddedPayload
    | MemoryUpdatedPayload
    | RegistryUpdatedPayload
    | ToolInvokedPayload
    | GenerationFailedPayload
)
"""payload 的判别联合。哪个事件配哪个 payload，由 `PAYLOAD_FOR` 钉住。"""


PAYLOAD_FOR = {
    EventType.ROUND_UPDATED: RoundUpdatedPayload,
    EventType.CARD_GROUP_UPDATED: CardGroupUpdatedPayload,
    EventType.DOC_CHANGED: DocChangedPayload,
    EventType.DISCUSSION_UTTERANCE_ADDED: DiscussionUtteranceAddedPayload,
    EventType.MEMORY_UPDATED: MemoryUpdatedPayload,
    EventType.REGISTRY_UPDATED: RegistryUpdatedPayload,
    EventType.TOOL_INVOKED: ToolInvokedPayload,
    EventType.GENERATION_FAILED: GenerationFailedPayload,
}
"""八个事件，八个 payload，一个不多一个不少。"""


PRODUCERS = {
    EventType.ROUND_UPDATED: frozenset({ProducerIdentity.ORCHESTRATION}),
    EventType.CARD_GROUP_UPDATED: frozenset({ProducerIdentity.ORCHESTRATION}),
    EventType.DOC_CHANGED: frozenset({ProducerIdentity.DOCUMENT_WRITER}),
    EventType.DISCUSSION_UTTERANCE_ADDED: frozenset({ProducerIdentity.ORCHESTRATION}),
    EventType.MEMORY_UPDATED: frozenset({ProducerIdentity.MEMORY}),
    EventType.REGISTRY_UPDATED: frozenset({ProducerIdentity.REGISTRY}),
    EventType.TOOL_INVOKED: frozenset({ProducerIdentity.TOOLS}),
    EventType.GENERATION_FAILED: frozenset({ProducerIdentity.HARNESS}),
}
"""窄发布的依据：只有状态的生产者能发这个事件。"""


class Event(ContractModel):
    """一条事件。字段只有三个：类型、最小定位信息、时间。"""

    type: EventType
    payload: EventPayload
    at: datetime

    @model_validator(mode="after")
    def _check(self) -> "Event":
        expected = PAYLOAD_FOR[self.type]
        if type(self.payload) is not expected:
            raise ContractViolation(
                f"{self.type.value} 的 payload 只能是 {expected.__name__}，收到 {type(self.payload).__name__}"
            )
        return self


class EventLogEntry(ContractModel):
    """事件流水的一行（append-only）。它回答"这件事到底发了没有"。"""

    event_id: str
    at: datetime
    type: EventType
    producer: ProducerIdentity
    payload: EventPayload
    round_id: str | None = None
    project_id: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "EventLogEntry":
        _require(event_id=self.event_id)
        expected = PAYLOAD_FOR[self.type]
        if type(self.payload) is not expected:
            raise ContractViolation(
                f"{self.type.value} 的 payload 只能是 {expected.__name__}，收到 {type(self.payload).__name__}"
            )
        if self.producer not in PRODUCERS[self.type]:
            raise ContractViolation(f"{self.producer.value} 不是 {self.type.value} 的生产者")
        return self
