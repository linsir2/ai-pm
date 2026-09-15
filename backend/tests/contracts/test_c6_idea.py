"""C6 Idea Candidate：讨论区与主闭环之间的唯一交接形态。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import IdeaStatus
from pmstudio.contracts.models.idea import IdeaCandidate


def _idea(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "idea_id": "ide_1",
        "discussion_round_id": "rnd_discussion_1",
        "claim": "先做单用户，不做协作",
        "from_packet_ids": ("pkt_1", "pkt_2"),
    }
    base.update(overrides)
    return base


def test_candidate_defaults_to_pending() -> None:
    idea = IdeaCandidate(**_idea())
    assert idea.status is IdeaStatus.PENDING
    assert idea.suggested_label is None


def test_source_packets_are_required() -> None:
    """没有出处的思路不许出现在勾选界面上。"""
    with pytest.raises(ValidationError):
        IdeaCandidate(**_idea(from_packet_ids=()))
    with pytest.raises(ValidationError):
        IdeaCandidate(**_idea(from_packet_ids=("pkt_1", "")))
    with pytest.raises(ValidationError):
        IdeaCandidate(**_idea(from_packet_ids=("pkt_1", "pkt_1")))


def test_claim_and_ids_are_required() -> None:
    with pytest.raises(ValidationError):
        IdeaCandidate(**_idea(claim="  "))
    with pytest.raises(ValidationError):
        IdeaCandidate(**_idea(discussion_round_id=""))
    with pytest.raises(ValidationError):
        IdeaCandidate(**_idea(idea_id=""))


def test_all_four_statuses_are_reachable() -> None:
    for status in IdeaStatus:
        assert IdeaCandidate(**_idea(status=status)).status is status
