"""C7 Card：四种卡片各自的字段组合，以及用户的回应。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import BlockOpKind, CardKind, CardStatus, ProposalState
from pmstudio.contracts.models.card import Card, CardAnswer, CardOption, Proposal


def _option() -> CardOption:
    return CardOption(option_id="opt_1", text="先做单用户")


def _proposal() -> Proposal:
    return Proposal(
        proposal_id="prp_1",
        target_label="功能清单",
        op=BlockOpKind.APPEND,
        content="支持把讨论候选带进主闭环",
    )


def test_card_field_set_matches_contract() -> None:
    assert set(Card.model_fields) == {
        "card_id",
        "kind",
        "prompt",
        "options",
        "proposals",
        "answer",
        "status",
    }


def test_understanding_card_carries_neither_options_nor_proposals() -> None:
    card = Card(card_id="crd_1", kind=CardKind.UNDERSTANDING, prompt="你要我细化功能清单")
    assert card.status is CardStatus.PENDING
    assert card.answer is None


def test_question_card_carries_neither_options_nor_proposals() -> None:
    Card(card_id="crd_1", kind=CardKind.QUESTION, prompt="上线时间要求是什么")


@pytest.mark.parametrize("kind", [CardKind.UNDERSTANDING, CardKind.QUESTION])
def test_cards_without_options_reject_them(kind: CardKind) -> None:
    with pytest.raises(ValidationError):
        Card(card_id="crd_1", kind=kind, prompt="x", options=(_option(),))
    with pytest.raises(ValidationError):
        Card(card_id="crd_1", kind=kind, prompt="x", proposals=(_proposal(),))


def test_conflict_card_requires_options_and_no_proposals() -> None:
    card = Card(card_id="crd_1", kind=CardKind.CONFLICT, prompt="分歧在哪", options=(_option(),))
    assert card.options[0].option_id == "opt_1"
    with pytest.raises(ValidationError):
        Card(card_id="crd_1", kind=CardKind.CONFLICT, prompt="x")
    with pytest.raises(ValidationError):
        Card(
            card_id="crd_1",
            kind=CardKind.CONFLICT,
            prompt="x",
            options=(_option(),),
            proposals=(_proposal(),),
        )


def test_fill_card_requires_proposals_and_no_options() -> None:
    card = Card(card_id="crd_1", kind=CardKind.FILL, prompt="这次我打算改动 1 处", proposals=(_proposal(),))
    assert card.proposals[0].state is ProposalState.KEPT
    with pytest.raises(ValidationError):
        Card(card_id="crd_1", kind=CardKind.FILL, prompt="x")
    with pytest.raises(ValidationError):
        Card(card_id="crd_1", kind=CardKind.FILL, prompt="x", proposals=(_proposal(),), options=(_option(),))


def test_prompt_and_ids_are_required() -> None:
    with pytest.raises(ValidationError):
        Card(card_id="", kind=CardKind.QUESTION, prompt="x")
    with pytest.raises(ValidationError):
        Card(card_id="crd_1", kind=CardKind.QUESTION, prompt="   ")


def test_proposal_field_set_and_values() -> None:
    assert set(Proposal.model_fields) == {
        "proposal_id",
        "target_label",
        "op",
        "content",
        "state",
        "citations",
    }
    with pytest.raises(ValidationError):
        Proposal(
            proposal_id="prp_1",
            target_label="",
            op=BlockOpKind.APPEND,
            content="x",
        )
    with pytest.raises(ValidationError):
        Proposal(
            proposal_id="prp_1",
            target_label="功能清单",
            op=BlockOpKind.APPEND,
            content="",
        )


def test_option_field_set_and_text_required() -> None:
    assert set(CardOption.model_fields) == {"option_id", "text", "citations"}
    with pytest.raises(ValidationError):
        CardOption(option_id="opt_1", text=" ")


def test_card_answer_field_set() -> None:
    assert set(CardAnswer.model_fields) == {"card_id", "verdict", "answer", "status", "proposal_states"}
    answer = CardAnswer(
        card_id="crd_1", verdict="confirm", answer="确认", status=CardStatus.ANSWERED,
    )
    assert answer.verdict == "confirm"
    assert answer.answer == "确认"
    assert answer.proposal_states == {}
    skipped = CardAnswer(card_id="crd_1", verdict="confirm", status=CardStatus.SKIPPED)
    assert skipped.answer is None


def test_card_answer_verdict_values() -> None:
    """verdict 只能是 confirm 或 correct。"""
    confirm = CardAnswer(card_id="crd_1", verdict="confirm", status=CardStatus.ANSWERED)
    assert confirm.verdict == "confirm"
    correct = CardAnswer(
        card_id="crd_1", verdict="correct", answer="改一下", status=CardStatus.ANSWERED,
    )
    assert correct.verdict == "correct"
    with pytest.raises(ValidationError):
        CardAnswer(card_id="crd_1", verdict="maybe", status=CardStatus.ANSWERED)


def test_verdict_correct_requires_answer() -> None:
    """verdict=correct 时 answer 必填。"""
    with pytest.raises(ValidationError):
        CardAnswer(card_id="crd_1", verdict="correct", status=CardStatus.ANSWERED)


def test_verdict_confirm_allows_empty_answer() -> None:
    """verdict=confirm 时 answer 可空。"""
    answer = CardAnswer(card_id="crd_1", verdict="confirm", status=CardStatus.ANSWERED)
    assert answer.answer is None


def test_card_answer_carries_per_proposal_decisions() -> None:
    """填充卡的"要 / 不要"逐条传回来——M20 只写 `kept` 的那些（C7）。"""
    answer = CardAnswer(
        card_id="crd_fill",
        verdict="confirm",
        status=CardStatus.ANSWERED,
        proposal_states={"prp_1": ProposalState.KEPT, "prp_2": ProposalState.REMOVED},
    )
    assert answer.proposal_states["prp_2"] is ProposalState.REMOVED


def test_card_answer_rejects_an_unknown_proposal_state() -> None:
    with pytest.raises(ValidationError):
        CardAnswer(
            card_id="crd_fill",
            verdict="confirm",
            status=CardStatus.ANSWERED,
            proposal_states={"prp_1": "maybe"},
        )


def test_answered_card_answer_without_verdict_is_rejected() -> None:
    """verdict 是必填的——不允许隐式推断。"""
    with pytest.raises(ValidationError):
        CardAnswer(card_id="crd_1", answer="确认", status=CardStatus.ANSWERED)
