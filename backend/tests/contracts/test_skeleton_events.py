"""C13 骨架：八个事件的 payload、发布者白名单、流水行。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import (
    CardGroupState,
    EventType,
    MemoryStatus,
    MemoryType,
    ProducerIdentity,
    RegistryKind,
    RegistryStatus,
    RoundEntry,
    RoundPhase,
    ToolInvocationStatus,
    VersionTrigger,
)
from pmstudio.contracts.skeleton.events import (
    PAYLOAD_FOR,
    PRODUCERS,
    CardGroupUpdatedPayload,
    DiscussionUtteranceAddedPayload,
    DocChangedPayload,
    Event,
    EventLogEntry,
    GenerationFailedPayload,
    MemoryUpdatedPayload,
    RegistryUpdatedPayload,
    RoundUpdatedPayload,
    ToolInvokedPayload,
)

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)

EXPECTED_PAYLOAD_FIELDS: dict[type, set[str]] = {
    RoundUpdatedPayload: {
        "round_id",
        "project_id",
        "entry",
        "phase",
        "from_phase",
        "end_reason",
    },
    CardGroupUpdatedPayload: {"group_id", "round_id", "state", "card_count", "has_fill_card"},
    DocChangedPayload: {"doc_id", "version_id", "seq", "trigger", "block_ids"},
    DiscussionUtteranceAddedPayload: {"round_id", "role_id", "packet_id", "index"},
    MemoryUpdatedPayload: {"memory_id", "project_id", "type", "status", "from_status"},
    RegistryUpdatedPayload: {"id", "kind", "status", "from_status"},
    ToolInvokedPayload: {"round_id", "tool_id", "status", "role_id"},
    GenerationFailedPayload: {"round_id", "step", "reason", "retryable"},
}


@pytest.mark.parametrize(
    ("payload_cls", "expected"),
    list(EXPECTED_PAYLOAD_FIELDS.items()),
    ids=[cls.__name__ for cls in EXPECTED_PAYLOAD_FIELDS],
)
def test_payload_field_set_matches_contract(payload_cls: type, expected: set[str]) -> None:
    assert set(payload_cls.model_fields) == expected


def test_every_event_has_a_payload_type() -> None:
    assert set(PAYLOAD_FOR) == set(EventType)


def test_payload_goes_with_its_event_type() -> None:
    event = Event(
        type=EventType.ROUND_UPDATED,
        payload=RoundUpdatedPayload(
            round_id="rnd_1",
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            phase=RoundPhase.WORKING,
        ),
        at=NOW,
    )
    assert event.type is EventType.ROUND_UPDATED
    with pytest.raises(ValidationError):
        Event(
            type=EventType.ROUND_UPDATED,
            payload=DocChangedPayload(
                doc_id="doc_1",
                version_id="ver_1",
                seq=1,
                trigger=VersionTrigger.SUBMIT,
            ),
            at=NOW,
        )


def test_event_has_exactly_three_fields() -> None:
    assert set(Event.model_fields) == {"type", "payload", "at"}


def test_opening_round_has_no_from_phase() -> None:
    payload = RoundUpdatedPayload(
        round_id="rnd_1",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        phase=RoundPhase.ASSEMBLING,
    )
    assert payload.from_phase is None
    with pytest.raises(ValidationError):
        RoundUpdatedPayload(
            round_id="rnd_1",
            project_id="prj_1",
            entry=RoundEntry.MAIN,
            phase=RoundPhase.DONE,
        )


def test_tool_invocation_may_have_no_role() -> None:
    payload = ToolInvokedPayload(
        round_id="rnd_1",
        tool_id="reg_tool_retrieval",
        status=ToolInvocationStatus.STARTED,
    )
    assert payload.role_id is None


def test_registry_event_needs_no_round() -> None:
    """注册条目状态变化不绑在某一轮上。"""
    entry = EventLogEntry(
        event_id="evt_1",
        at=NOW,
        type=EventType.REGISTRY_UPDATED,
        producer=ProducerIdentity.REGISTRY,
        payload=RegistryUpdatedPayload(
            id="reg_1",
            kind=RegistryKind.SKILL,
            status=RegistryStatus.RETIRED,
            from_status=RegistryStatus.ACTIVE,
        ),
    )
    assert entry.round_id is None
    assert entry.project_id is None


def test_producer_whitelist_matches_the_contract() -> None:
    assert {
        EventType.ROUND_UPDATED: frozenset({ProducerIdentity.ORCHESTRATION}),
        EventType.CARD_GROUP_UPDATED: frozenset({ProducerIdentity.ORCHESTRATION}),
        EventType.DOC_CHANGED: frozenset({ProducerIdentity.DOCUMENT_WRITER}),
        EventType.DISCUSSION_UTTERANCE_ADDED: frozenset({ProducerIdentity.ORCHESTRATION}),
        EventType.MEMORY_UPDATED: frozenset({ProducerIdentity.MEMORY}),
        EventType.REGISTRY_UPDATED: frozenset({ProducerIdentity.REGISTRY}),
        EventType.TOOL_INVOKED: frozenset({ProducerIdentity.TOOLS}),
        EventType.GENERATION_FAILED: frozenset({ProducerIdentity.HARNESS}),
    } == PRODUCERS


def test_log_row_rejects_a_foreign_producer() -> None:
    """窄发布：文档写入不能替编排层发轮次事件。"""
    with pytest.raises(ValidationError):
        EventLogEntry(
            event_id="evt_1",
            at=NOW,
            type=EventType.ROUND_UPDATED,
            producer=ProducerIdentity.DOCUMENT_WRITER,
            payload=RoundUpdatedPayload(
                round_id="rnd_1",
                project_id="prj_1",
                entry=RoundEntry.MAIN,
                phase=RoundPhase.WORKING,
            ),
        )


def test_log_row_has_the_seven_columns() -> None:
    assert set(EventLogEntry.model_fields) == {
        "event_id",
        "at",
        "type",
        "producer",
        "payload",
        "round_id",
        "project_id",
    }


def test_payload_carries_locators_not_full_state() -> None:
    """事件不带完整状态：卡片组变化只报数量，不背整组卡片。"""
    fields = set(CardGroupUpdatedPayload.model_fields)
    assert "card_count" in fields
    assert "cards" not in fields
    memory_fields = set(MemoryUpdatedPayload.model_fields)
    assert memory_fields == {"memory_id", "project_id", "type", "status", "from_status"}
    assert MemoryStatus.ACTIVE in set(MemoryStatus)
    assert MemoryType.DECISION in set(MemoryType)
    assert CardGroupState.CONFIRMED in set(CardGroupState)
