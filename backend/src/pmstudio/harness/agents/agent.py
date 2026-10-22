"""agent 的抽象：声明 + 基类 + 注册表。

`StructuredCall` 已经是"调模型"的统一执行器（`harness/model/structured.py`），
这里不再造第二套。只补三层：

1. `AgentDefinition` —— 把散落在各 agent 类属性里的元数据（purpose / kind / prompt /
   packet / slots）收成一份纯数据。
2. `Agent` —— 把每个 agent 都写的"构造 StructuredCall → 调 call_structured"收成基类的
   `_call()`，子类只管解析（信封 → 领域产出物）。
3. `AgentRegistry` —— 统一管理所有 agent 的声明，是唯一的真相源。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope, make_packet_id
from pmstudio.harness.model.structured import StructuredCall, call_structured

P = TypeVar("P", bound=BaseModel)
Output = TypeVar("Output")


@dataclass(frozen=True, slots=True)
class AgentDefinition[P: BaseModel]:
    """一个 agent 的声明：角色身份 + 调用契约。

    纯数据，不含运行时依赖。所有字段都是元数据，不引 domain / application 层。
    """

    agent_id: str
    purpose: str
    kind: str
    prompt: str
    packet: type[P]
    slots: tuple[str, ...]


class UnknownAgent(Exception):
    """按 id 查 agent 声明，找不到。"""

    def __init__(self, agent_id: str, known: Sequence[str]) -> None:
        super().__init__(
            f"未知的 agent id：{agent_id!r}；"
            f"已有的 agent：{', '.join(known) or '(无)'}"
        )
        self.agent_id = agent_id
        self.known = tuple(known)


class DuplicateAgent(Exception):
    """注册时撞了 agent_id。"""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent_id 重复注册：{agent_id!r}")
        self.agent_id = agent_id


class AgentRegistry:
    """统一管理所有 agent 的声明。

    是唯一的真相源："系统有哪些 agent"、"某个 id 对应哪个声明"全走这里。
    """

    def __init__(self, definitions: Sequence[AgentDefinition]) -> None:
        by_id: dict[str, AgentDefinition] = {}
        for defn in definitions:
            if defn.agent_id in by_id:
                raise DuplicateAgent(agent_id=defn.agent_id)
            by_id[defn.agent_id] = defn
        self._by_id: tuple[tuple[str, AgentDefinition], ...] = tuple(by_id.items())

    def get(self, agent_id: str) -> AgentDefinition:
        """按 id 查声明；找不到明确报错（不静默回退）。"""
        for known_id, defn in self._by_id:
            if known_id == agent_id:
                return defn
        raise UnknownAgent(agent_id=agent_id, known=[k for k, _ in self._by_id])

    def all(self) -> tuple[AgentDefinition, ...]:
        """列出全部声明。"""
        return tuple(defn for _, defn in self._by_id)


class Agent[Output, P: BaseModel]:
    """agent 基类。

    子类只需：
    1. 设置 `definition` 类属性（`AgentDefinition[P]` 实例）。
    2. 在公共方法里调 `self._call()` 跑模型，再自己解析信封。

    基类提供 `_call()`：统一的"构造 StructuredCall → 调 call_structured"路径。
    """

    definition: AgentDefinition[P]

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def _call(
        self,
        *,
        slots: Mapping[str, str],
        metadata: Mapping[str, str],
        packet_id: str,
        run_id: str,
        produced_by: str,
        max_tokens: int | None = None,
    ) -> Envelope:
        """统一的模型调用路径。从 self.definition 构造 StructuredCall，走 call_structured。

        `max_tokens` 是**输出 token 上限**（`None` = 不设，保持供应商默认）——
        只有需要"按预算限条数"的调用方才给（2026-10-22，批 14）。
        """
        defn = self.definition
        call = StructuredCall(
            purpose=defn.purpose,
            kind=defn.kind,
            prompt=defn.prompt,
            packet=defn.packet,
        )
        return await call_structured(
            call,
            gateway=self._model,
            prompts=self._prompts,
            slots=slots,
            metadata=metadata,
            packet_id=packet_id,
            run_id=run_id,
            produced_by=produced_by,
            max_tokens=max_tokens,
        )

    def _make_packet_id(self, run_id: str, kind_suffix: str, slot: str = "") -> str:
        """构造 packet_id 的薄封装，省去每个子类重复 import make_packet_id。"""
        return make_packet_id(run_id, self.definition.kind, kind_suffix, slot=slot)
