from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from pmstudio.application.errors import (
    ApplicationError,
    ModelCallFailed,
    PacketParseError,
    UnknownProject,
    UnknownRound,
    unknown_ref_message,
)
from pmstudio.application.events import (
    DOC_CHANGED,
    EVENT_NAMES,
    GENERATION_FAILED,
    PACKET_EMITTED,
    ROUND_UPDATED,
    Event,
)
from pmstudio.application.ports import (
    Clock,
    EventPublisher,
    EventStream,
    IdGenerator,
    ModelGateway,
    ModelMessage,
    ModelReply,
    ModelRequest,
    PromptLibrary,
    SeatCatalog,
    SeatDefinition,
)

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def test_event_vocabulary_is_exactly_four():
    assert EVENT_NAMES == {ROUND_UPDATED, PACKET_EMITTED, DOC_CHANGED, GENERATION_FAILED}
    assert ROUND_UPDATED == "round.updated"
    assert PACKET_EMITTED == "packet.emitted"
    assert DOC_CHANGED == "doc.changed"
    assert GENERATION_FAILED == "generation.failed"


def test_event_rejects_unknown_name():
    with pytest.raises(ValueError, match="未定义的事件名"):
        Event(name="doc.updated", project_id="p1", occurred_at=NOW, seq=1)


def test_event_is_frozen_and_carries_round_and_payload():
    event = Event(
        name=ROUND_UPDATED,
        project_id="p1",
        round_id="r1",
        payload={"step": "seats_running"},
        occurred_at=NOW,
        seq=7,
    )
    assert event.payload["step"] == "seats_running"
    with pytest.raises(FrozenInstanceError):
        event.seq = 8  # type: ignore[misc]


def test_model_request_carries_purpose_schema_and_metadata():
    request = ModelRequest(
        purpose="seat_review",
        messages=(ModelMessage(role="user", content="…"),),
        schema_name="SeatPacket",
        response_schema={"type": "object"},
        metadata={"seat_id": "seat.data_scale"},
    )
    assert request.metadata["seat_id"] == "seat.data_scale"
    assert request.response_schema == {"type": "object"}


def test_ports_are_structurally_checkable():
    class _Clock:
        def now(self) -> datetime:
            return NOW

    class _Ids:
        def new(self, prefix: str) -> str:
            return f"{prefix}-1"

    class _Gateway:
        async def complete(self, request: ModelRequest) -> ModelReply:
            return ModelReply(text="{}", model="test")

    class _Prompts:
        def render(self, name: str, **slots: str) -> str:
            return name

    class _Catalog:
        def all(self) -> tuple[SeatDefinition, ...]:
            return ()

        def nominate(self, *, user_input: str) -> tuple[SeatDefinition, ...]:
            return ()

        def by_id(self, seat_id: str) -> SeatDefinition:
            raise KeyError(seat_id)

    class _Publisher:
        def publish(self, event: Event) -> None:
            return None

    class _Stream:
        def subscribe(self):  # type: ignore[no-untyped-def]
            return None

    assert isinstance(_Clock(), Clock)
    assert isinstance(_Ids(), IdGenerator)
    assert isinstance(_Gateway(), ModelGateway)
    assert isinstance(_Prompts(), PromptLibrary)
    assert isinstance(_Catalog(), SeatCatalog)
    assert isinstance(_Publisher(), EventPublisher)
    assert isinstance(_Stream(), EventStream)


def test_application_errors_carry_machine_readable_codes():
    parse = PacketParseError("席位输出的 JSON 缺 opinions 字段", purpose="seat_review")
    assert isinstance(parse, ApplicationError)
    assert parse.code == "model_packet_unparsable"
    assert parse.details["purpose"] == "seat_review"
    assert ModelCallFailed("超时", purpose="claims").code == "model_call_failed"
    assert UnknownProject("p9").code == "unknown_project"
    assert UnknownRound("r9").code == "unknown_round"
    assert UnknownRound("r9").details["round_id"] == "r9"
def test_unknown_ref_message_explains_proposal_ids():
    """真跑里模型把 `r-f2a00f4e#08` 当出处编号写——提示要说清那是另一个命名空间。"""

    message = unknown_ref_message(["r-f2a00f4e#08"], what="意见引用了不存在的编号")

    assert "提案编号" in message
    assert "E" in message
