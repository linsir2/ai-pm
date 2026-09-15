"""C17 Conversation：消息与滚动摘要。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.models.conversation import Message, Summary


def _message(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "message_id": "msg_1",
        "project_id": "prj_1",
        "round_id": "rnd_1",
        "author": "user",
        "text": "细化一下功能清单",
    }
    base.update(overrides)
    return base


def test_message_field_set_matches_contract() -> None:
    assert set(Message.model_fields) == {
        "message_id",
        "project_id",
        "round_id",
        "author",
        "text",
        "packet_id",
    }


def test_user_message_has_no_packet() -> None:
    message = Message(**_message())
    assert message.packet_id is None
    assert message.author == "user"


def test_role_message_may_point_at_its_packet() -> None:
    """指回 C4 的位置只留不透明 id，不解构信息包内部。"""
    message = Message(**_message(author="reg_role_a", packet_id="pkt_1"))
    assert message.packet_id == "pkt_1"


def test_message_requires_ids_author_and_text() -> None:
    with pytest.raises(ValidationError):
        Message(**_message(message_id=""))
    with pytest.raises(ValidationError):
        Message(**_message(project_id=""))
    with pytest.raises(ValidationError):
        Message(**_message(round_id=""))
    with pytest.raises(ValidationError):
        Message(**_message(author=""))
    with pytest.raises(ValidationError):
        Message(**_message(text="  "))


def test_summary_must_cover_at_least_one_round() -> None:
    summary = Summary(
        summary_id="sum_1",
        project_id="prj_1",
        covers_round_ids=("rnd_1", "rnd_2"),
        text="前两轮定了单用户和不做协作",
    )
    assert summary.covers_round_ids == ("rnd_1", "rnd_2")
    with pytest.raises(ValidationError):
        Summary(summary_id="sum_1", project_id="prj_1", covers_round_ids=(), text="x")
    with pytest.raises(ValidationError):
        Summary(summary_id="sum_1", project_id="prj_1", covers_round_ids=("rnd_1",), text=" ")
