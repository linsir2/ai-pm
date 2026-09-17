"""M9 卡片组装器：把卡片打包为 CardGroup。"""

import asyncio
from datetime import UTC, datetime

import pytest

from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.contracts.enums import CardGroupState, CardKind
from pmstudio.contracts.models.card import Card
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.orchestration.cards import CardAssembler

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


def _card() -> Card:
    return Card(
        card_id="crd_1",
        kind=CardKind.UNDERSTANDING,
        prompt="我理解你要细化功能清单。",
    )


def test_group_carries_the_round_and_is_answering() -> None:
    """round_id 对、state=answering、cards 顺序不变。"""
    assembler = CardAssembler(TimestampIdGenerator())
    group = assembler.assemble((_card(),), round_id="rnd_1")

    assert isinstance(group, CardGroup)
    assert group.round_id == "rnd_1"
    assert group.state is CardGroupState.ANSWERING
    assert len(group.cards) == 1
    assert group.cards[0].card_id == "crd_1"
