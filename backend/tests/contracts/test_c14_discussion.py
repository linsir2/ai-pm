"""C14 Discussion Round：一轮讨论，每角色一条发言。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import DiscussionEndReason, DiscussionState
from pmstudio.contracts.models.discussion import MAX_PARTICIPANTS, DiscussionRound, Utterance


def _round(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "round_id": "rnd_discussion_1",
        "participants": ("reg_role_a", "reg_role_b"),
    }
    base.update(overrides)
    return base


def test_field_set_matches_contract() -> None:
    assert set(DiscussionRound.model_fields) == {
        "round_id",
        "participants",
        "utterances",
        "state",
        "end_reason",
        "idea_ids",
    }


def test_four_participants_is_the_ceiling() -> None:
    assert MAX_PARTICIPANTS == 4
    DiscussionRound(**_round(participants=("r1", "r2", "r3", "r4")))
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(participants=("r1", "r2", "r3", "r4", "r5")))


def test_participants_must_be_distinct_and_non_empty() -> None:
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(participants=()))
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(participants=("r1", "r1")))
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(participants=("r1", "")))


def test_running_round_has_no_end_reason() -> None:
    round_ = DiscussionRound(**_round())
    assert round_.state is DiscussionState.RUNNING
    assert round_.end_reason is None
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(end_reason=DiscussionEndReason.ALL_SPOKE))


def test_finished_round_must_say_why_it_stopped() -> None:
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(state=DiscussionState.FINISHED))
    round_ = DiscussionRound(
        **_round(state=DiscussionState.FINISHED, end_reason=DiscussionEndReason.ALL_SPOKE)
    )
    assert round_.end_reason is DiscussionEndReason.ALL_SPOKE


def test_each_participant_speaks_at_most_once() -> None:
    """默认一轮 = 每个角色各说一次。"""
    utterance = Utterance(role_id="reg_role_a", text="先做单用户", packet_id="pkt_1")
    round_ = DiscussionRound(**_round(utterances=(utterance,)))
    assert round_.utterances[0].packet_id == "pkt_1"
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(utterances=(utterance, utterance)))


def test_utterance_must_come_from_a_participant() -> None:
    stranger = Utterance(role_id="reg_role_c", text="我插一句", packet_id="pkt_9")
    with pytest.raises(ValidationError):
        DiscussionRound(**_round(utterances=(stranger,)))


def test_utterance_field_set_and_text_required() -> None:
    assert set(Utterance.model_fields) == {"role_id", "text", "packet_id"}
    with pytest.raises(ValidationError):
        Utterance(role_id="reg_role_a", text="  ", packet_id="pkt_1")
