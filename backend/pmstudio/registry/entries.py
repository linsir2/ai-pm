"""M27 资产注册中心 —— 所有定义类东西的唯一写入口（L0）。

三件事：**存**（按 id 索引，`kind` 一起记）、**校验**（登记时按 kind 校验本体）、
**改状态**（改完发 `registry.updated`，生产者身份 `registry`——C13 的白名单）。

**今天不落库**：种子每次启动重新装载，结果完全一致；而"跨重启的写"（用户删 skill、
M30 晋升 / 退役）今天还没有入口（R6）。等它出现再加表——那时才知道要存什么。
代价写在这里，别让它变成惊讶：**重启后注册中心回到种子的样子**。
"""

from collections.abc import Sequence

from pydantic import ValidationError

from pmstudio.common.clock import Clock
from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    EventType,
    ProducerIdentity,
    RegistryKind,
    RegistryStatus,
)
from pmstudio.contracts.interfaces.communication import EventBus
from pmstudio.contracts.models.registry import RegistryEntry, TemplateBody
from pmstudio.contracts.skeleton.events import Event, RegistryUpdatedPayload


class InMemoryRegistry:
    """`RegistryPort` 的实现：条目按 id 索引（id 全局唯一，不按 kind 分家）。

    分工：**形状**由 C10 的模型管（三件套、白名单只对角色有意义）；**本体**按 kind 校验
    （模板 → `TemplateBody`）；**状态变化**发事件。权限判定不在这里（L6）。
    """

    def __init__(self, bus: EventBus, clock: Clock) -> None:
        self._bus = bus
        self._clock = clock
        self._entries: dict[str, RegistryEntry] = {}

    async def resolve(self, kind: RegistryKind, id: str) -> RegistryEntry:
        entry = self._entries.get(id)
        if entry is None or entry.kind is not kind:
            raise ContractViolation(f"注册中心里没有 {kind.value} 的 {id}")
        return entry

    async def list(self, kind: RegistryKind, tags: Sequence[str] = ()) -> tuple[RegistryEntry, ...]:
        wanted = set(tags)
        return tuple(
            entry
            for entry in self._entries.values()
            if entry.kind is kind and wanted <= set(entry.tags)
        )

    async def register(self, entry: RegistryEntry) -> str:
        if entry.id in self._entries:
            raise ContractViolation(f"注册条目 id 重复：{entry.id}——id 全局唯一，重号会覆盖掉别人")
        _validate_body(entry)
        self._entries[entry.id] = entry
        return entry.id

    async def update_status(self, id: str, status: RegistryStatus) -> None:
        """改状态并发 `registry.updated`。**值没变也发**——不去重是黑板那边的同一条口径（C12），
        订阅者按 `from_status` 判断这次是不是"刚变成"。"""
        entry = self._entries.get(id)
        if entry is None:
            raise ContractViolation(f"注册中心里没有 {id}")

        self._entries[id] = entry.model_copy(update={"status": status})
        await self._bus.publish(
            Event(
                type=EventType.REGISTRY_UPDATED,
                payload=RegistryUpdatedPayload(
                    id=id,
                    kind=entry.kind,
                    status=status,
                    from_status=entry.status,
                ),
                at=self._clock.now(),
            ),
            producer=ProducerIdentity.REGISTRY,
        )

    async def remove(self, id: str) -> None:
        if id not in self._entries:
            raise ContractViolation(f"注册中心里没有 {id}")
        del self._entries[id]


def _validate_body(entry: RegistryEntry) -> None:
    """按 kind 校验本体。

    工具 / skill 的三件套由 C10 的模型自己管；这里只管模板本体——它的形状不对，
    就是一份坏掉的种子，要在**登记时**炸，而不是等 M1 建项目时才发现。
    """
    if entry.kind is not RegistryKind.TEMPLATE:
        return
    try:
        TemplateBody.model_validate(entry.content)
    except ValidationError as error:
        raise ContractViolation(f"模板条目 {entry.id} 的本体不是合法的模板定义：{error}") from error
