"""重构后的 ClaimAnalyzer 验证：继承 Agent 基类，使用统一的 _call() 路径。

这些测试验证重构没有破坏原有功能。
"""

import json

import pytest

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.definitions import ALL_AGENTS
from pmstudio.harness.agents.agent import Agent, AgentRegistry
from tests.support.builders import SEAT, STD, make_seat
from tests.support.doubles import RecordingPrompts, ScriptedModelGateway, StaticSeatCatalog


def make_analyzer(gateway=None, prompts=None) -> ClaimAnalyzer:
    claims_reply = json.dumps({
        "basis": "测试",
        "claims": [{
            "text": "测试主张", "raw_span": "测试",
            "label": "constraint", "zone": "body", "section_id": "s5",
        }],
        "seats": [{"seat_id": SEAT, "why": "测试"}],
    })
    gateway = gateway or ScriptedModelGateway(replies={"claims": claims_reply})
    prompts = prompts or RecordingPrompts()
    return ClaimAnalyzer(model=gateway, prompts=prompts, catalog=StaticSeatCatalog((make_seat(),)))


class TestClaimAnalyzerInheritance:
    """ClaimAnalyzer 必须继承 Agent 基类。"""

    def test_is_agent_subclass(self):
        assert issubclass(ClaimAnalyzer, Agent)

    def test_has_definition(self):
        analyzer = make_analyzer()
        assert analyzer.definition.agent_id == "analyzer"
        assert analyzer.definition.purpose == "claims"
        assert analyzer.definition.kind == "claims"
        assert analyzer.definition.prompt == "analyzer"


class TestAnalyzerDefinition:
    """ANALYZER 声明必须在 ALL_AGENTS 里。"""

    def test_analyzer_in_all_agents(self):
        ids = [d.agent_id for d in ALL_AGENTS]
        assert "analyzer" in ids

    def test_can_lookup_via_registry(self):
        registry = AgentRegistry(definitions=ALL_AGENTS)
        defn = registry.get("analyzer")
        assert defn.agent_id == "analyzer"
        assert defn.purpose == "claims"


@pytest.mark.asyncio
class TestAnalyzerCallUsesBaseClass:
    """ClaimAnalyzer.analyze() 必须走 self._call()（基类的统一路径）。"""

    async def test_analyze_returns_analysis(self):
        """端到端验证：analyze() 返回正确的 Analysis 对象。"""
        analyzer = make_analyzer()
        result = await analyzer.analyze(
            adapter=STD,
            user_input="测试输入",
            document_state="",
            round_id="r-test",
        )
        assert result is not None
        assert len(result.claims) > 0
        assert result.envelope is not None
