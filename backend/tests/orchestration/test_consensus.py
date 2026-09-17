"""M8 共识生成器（最简版）：复述理解 → 出一张确认理解卡。"""

import asyncio
import os
from datetime import UTC, datetime

import pytest

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

from pmstudio.common.ids import IdGenerator, TimestampIdGenerator
from pmstudio.contracts.enums import CardKind, ContextBlockSource, RoundEntry, RoundPhase
from pmstudio.contracts.models.card import Card
from pmstudio.contracts.models.prompt import PromptMessage
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import ContextBlock, RoundRegion
from pmstudio.harness.model_gateway import GenerationFailure
from pmstudio.orchestration.consensus import ConsensusGenerator

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


class _FakeHarness:
    """M26 替身：返回脚本化文本，记录收到的 messages。"""

    def __init__(self, reply: str) -> None:
        self._reply = reply
        self.last_messages: list[PromptMessage] = []

    async def complete(self, model_ref: str, messages: list[PromptMessage]) -> str:
        self.last_messages = list(messages)
        if not self._reply.strip():
            raise GenerationFailure("模型返回了空文本", retryable=True)
        return self._reply


def _round_region() -> RoundRegion:
    return RoundRegion(
        round_id="rnd_1",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input="细化功能清单",
        scope=Scope(),
        phase=RoundPhase.RESTATING,
    )


def _blocks() -> list[ContextBlock]:
    return [
        ContextBlock(
            source=ContextBlockSource.DOCUMENT,
            ref="blk_1",
            content="功能清单：当前为空",
            priority=80,
        ),
        ContextBlock(
            source=ContextBlockSource.USER_INPUT,
            ref="rnd_1",
            content="细化功能清单",
            priority=100,
        ),
    ]


def test_prompt_carries_the_document_and_the_input() -> None:
    """system prompt 必须含文档正文与本轮输入（I14 的前提）。"""
    harness = _FakeHarness("复述文本")
    gen = ConsensusGenerator(harness, TimestampIdGenerator())
    asyncio.run(gen.produce_understanding_card(_round_region(), _blocks()))

    system = harness.last_messages[0]
    assert "功能清单" in system.text
    assert "细化功能清单" in system.text


def test_prompt_states_the_repetition_rule() -> None:
    """提示词含"只有读了文档才知道"的硬要求（AC10）。"""
    harness = _FakeHarness("复述")
    gen = ConsensusGenerator(harness, TimestampIdGenerator())
    asyncio.run(gen.produce_understanding_card(_round_region(), _blocks()))

    system = harness.last_messages[0]
    assert "只有读了文档才知道" in system.text


def test_it_returns_an_understanding_card() -> None:
    """kind=understanding, prompt=模型文本, status=pending。"""
    harness = _FakeHarness("我理解你要细化功能清单，当前文档中该字段为空。")
    gen = ConsensusGenerator(harness, TimestampIdGenerator())
    card = asyncio.run(gen.produce_understanding_card(_round_region(), _blocks()))

    assert isinstance(card, Card)
    assert card.kind is CardKind.UNDERSTANDING
    assert card.prompt == "我理解你要细化功能清单，当前文档中该字段为空。"
    assert card.options == ()
    assert card.proposals == ()


def test_blank_reply_is_refused() -> None:
    """空复述 → GenerationFailure（由 harness 层抛出）。"""
    harness = _FakeHarness("   ")
    gen = ConsensusGenerator(harness, TimestampIdGenerator())

    with pytest.raises(GenerationFailure):
        asyncio.run(gen.produce_understanding_card(_round_region(), _blocks()))
