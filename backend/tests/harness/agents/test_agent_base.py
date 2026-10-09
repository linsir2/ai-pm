"""Agent 基类 + AgentDefinition + AgentRegistry 的测试。

TDD 先行：先写断言，再写实现。
"""

import pytest
from pydantic import BaseModel

from pmstudio.harness.agents.agent import (
    Agent,
    AgentDefinition,
    AgentRegistry,
    DuplicateAgent,
    UnknownAgent,
)
from pmstudio.harness.model.contracts import ModelReply, ModelRequest


class _Packet(BaseModel):
    text: str


class _ConcreteAgent(Agent):
    definition = AgentDefinition(
        agent_id="test_agent",
        purpose="test_purpose",
        kind="test_kind",
        prompt="test_prompt",
        packet=_Packet,
        slots=("document_state", "user_input"),
    )


class _ScriptedGateway:
    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        return ModelReply(text=self._reply, model="scripted", usage={"total_tokens": 7})


class TestAgentDefinition:
    """AgentDefinition 是纯数据容器。"""

    def test_fields_are_accessible(self):
        defn = AgentDefinition(
            agent_id="analyzer",
            purpose="claims",
            kind="claims",
            prompt="analyzer",
            packet=_Packet,
            slots=("document_state", "user_input"),
        )
        assert defn.agent_id == "analyzer"
        assert defn.purpose == "claims"
        assert defn.prompt == "analyzer"
        assert defn.packet is _Packet
        assert "document_state" in defn.slots


class TestAgentRegistry:
    """AgentRegistry 统一管理所有 agent 声明。"""

    def test_get_by_id(self):
        defn = AgentDefinition(
            agent_id="test", purpose="p", kind="k", prompt="pr", packet=_Packet, slots=()
        )
        registry = AgentRegistry(definitions=(defn,))
        assert registry.get("test") is defn

    def test_get_unknown_raises(self):
        registry = AgentRegistry(definitions=())
        with pytest.raises(UnknownAgent) as exc:
            registry.get("nonexistent")
        assert "nonexistent" in str(exc.value)

    def test_all_returns_all(self):
        a = AgentDefinition(
            agent_id="a", purpose="p", kind="k", prompt="pr", packet=_Packet, slots=()
        )
        b = AgentDefinition(
            agent_id="b", purpose="p", kind="k", prompt="pr", packet=_Packet, slots=()
        )
        registry = AgentRegistry(definitions=(a, b))
        assert registry.all() == (a, b)

    def test_duplicate_id_raises(self):
        a = AgentDefinition(
            agent_id="dup", purpose="p", kind="k", prompt="pr", packet=_Packet, slots=()
        )
        b = AgentDefinition(
            agent_id="dup", purpose="p2", kind="k2", prompt="pr2", packet=_Packet, slots=()
        )
        with pytest.raises(DuplicateAgent):
            AgentRegistry(definitions=(a, b))


@pytest.mark.asyncio
class TestAgentCall:
    """Agent._call() 统一的模型调用路径。"""

    async def test_call_invokes_gateway_and_returns_envelope(self):
        gateway = _ScriptedGateway(reply='{"text": "hello"}')
        from tests.support.doubles import RecordingPrompts

        agent = _ConcreteAgent(model=gateway, prompts=RecordingPrompts())

        envelope = await agent._call(
            slots={"document_state": "doc", "user_input": "input"},
            metadata={"round_id": "r-1"},
            packet_id="pid-1",
            run_id="run-1",
            produced_by="test",
        )

        # 网关被调到了
        assert len(gateway.requests) == 1
        # 信封里有解析后的 packet
        assert envelope.packet.text == "hello"
        # 用的是 definition 里的 purpose/kind
        assert envelope.kind == "test_kind"
