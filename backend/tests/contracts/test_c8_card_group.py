"""C8 Card Group：一批最多 5 张，填充卡单独成批，确认即冻结。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import BlockOpKind, CardGroupState, CardKind, CardStatus
from pmstudio.contracts.models.card import Card, Proposal
from pmstudio.contracts.models.card_group import MAX_CARDS_PER_GROUP, CardGroup


def _question(index: int) -> Card:
    return Card(card_id=f"crd_{index}", kind=CardKind.QUESTION, prompt=f"问题 {index}")


def _fill(card_id: str = "crd_fill") -> Card:
    return Card(
        card_id=card_id,
        kind=CardKind.FILL,
        prompt="这次我打算改动 1 处",
        proposals=(
            Proposal(
                proposal_id="prp_1",
                target_label="功能清单",
                op=BlockOpKind.APPEND,
                content="支持讨论候选进主闭环",
            ),
        ),
    )


def _group(cards: tuple[Card, ...], **overrides: object) -> CardGroup:
    base: dict[str, object] = {"group_id": "grp_1", "round_id": "rnd_1", "cards": cards}
    base.update(overrides)
    return CardGroup(**base)


def test_five_cards_is_the_ceiling() -> None:
    assert MAX_CARDS_PER_GROUP == 5
    group = _group(tuple(_question(index) for index in range(5)))
    assert len(group.cards) == 5
    with pytest.raises(ValidationError):
        _group(tuple(_question(index) for index in range(6)))


def test_fill_card_must_be_a_batch_of_its_own() -> None:
    """填充卡不占裁决卡的名额——它是不共处，不是例外。"""
    group = _group((_fill(),))
    assert len(group.cards) == 1
    with pytest.raises(ValidationError):
        _group((_fill(), _question(1)))
    with pytest.raises(ValidationError):
        _group((_question(1), _fill("crd_fill_2")))


def test_group_defaults() -> None:
    group = _group((_question(1),))
    assert group.state is CardGroupState.ANSWERING
    assert group.result_version_id is None


def test_group_without_fill_card_never_produces_a_version() -> None:
    """不含填充卡的组提交后不写文档（I16）。"""
    group = _group((_question(1),), state=CardGroupState.CONFIRMED)
    assert group.result_version_id is None
    with pytest.raises(ValidationError):
        _group((_question(1),), state=CardGroupState.CONFIRMED, result_version_id="ver_1")


def test_confirmed_fill_group_records_result_version() -> None:
    group = _group((_fill(),), state=CardGroupState.CONFIRMED, result_version_id="ver_1")
    assert group.result_version_id == "ver_1"
    with pytest.raises(ValidationError):
        _group((_fill(),), result_version_id="ver_1")


def test_empty_group_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _group(())


def test_duplicate_card_ids_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _group((_question(1), _question(1)))


def test_group_ids_are_required() -> None:
    with pytest.raises(ValidationError):
        _group((_question(1),), group_id="")
    with pytest.raises(ValidationError):
        _group((_question(1),), round_id="")


def test_confirmed_group_is_immutable() -> None:
    """I2 确认即冻结：确认后整组不可变，要改只能开新一轮。"""
    group = _group((_question(1),), state=CardGroupState.CONFIRMED)
    with pytest.raises(ValidationError):
        group.state = CardGroupState.ANSWERING
    card = group.cards[0]
    with pytest.raises(ValidationError):
        card.status = CardStatus.ANSWERED
