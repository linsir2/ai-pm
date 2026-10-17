from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from pmstudio.application.errors import (
    ExplorationBusy,
    UnknownExploration,
    UnknownProject,
    UnknownRound,
    unknown_ref_message,
)
from pmstudio.application.events import (
    DISCUSSION_UPDATED,
    DOC_CHANGED,
    EVENT_NAMES,
    EXPLORATION_UPDATED,
    GENERATION_FAILED,
    PACKET_EMITTED,
    ROUND_UPDATED,
)
from pmstudio.application.ports import (
    Clock,
    IdGenerator,
    SeatCatalog,
    SeatDefinition,
)
from pmstudio.harness.errors import (
    ApplicationError,
    ModelCallFailed,
    PacketParseError,
)
from pmstudio.harness.events import Event, EventPublisher, EventStream
from pmstudio.harness.model.contracts import (
    ModelGateway,
    ModelMessage,
    ModelReply,
    ModelRequest,
    PromptLibrary,
)

NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)


def test_event_vocabulary_is_exactly_five():
    """M9 起是**六**个：讨论线程状态变化加了一个（前端订阅清单必须同步）。"""

    assert EVENT_NAMES == {
        ROUND_UPDATED,
        PACKET_EMITTED,
        DOC_CHANGED,
        GENERATION_FAILED,
        EXPLORATION_UPDATED,
        DISCUSSION_UPDATED,
    }
    assert ROUND_UPDATED == "round.updated"
    assert PACKET_EMITTED == "packet.emitted"
    assert DOC_CHANGED == "doc.changed"
    assert GENERATION_FAILED == "generation.failed"
    assert EXPLORATION_UPDATED == "exploration.updated"
    assert DISCUSSION_UPDATED == "discussion.updated"


def test_event_no_longer_rejects_names_it_does_not_know():
    """**这一条原先是反的**（2026-10-04 更正）。

    原来它断言"事件名不在四件套里就抛'未定义的事件名'"——那是通用信封反向依赖业务词汇：
    `Event` 住在 harness，不该知道产品有哪些事件。事件名的权威是上面那份清单
    加前端的订阅清单，不是信封自己。

    删掉这条校验之后，`Event` 仍然拒收负序号（见 `tests/harness/test_events.py`）。
    """

    event = Event(name="doc.updated", project_id="p1", occurred_at=NOW)
    assert event.name == "doc.updated"


def test_event_is_frozen_and_carries_round_and_payload():
    event = Event(
        name=ROUND_UPDATED,
        project_id="p1",
        round_id="r1",
        payload={"step": "seats_running"},
        occurred_at=NOW,
    )
    assert event.payload["step"] == "seats_running"
    assert event.owner_id is None  # 由总线盖，业务侧留空
    with pytest.raises(FrozenInstanceError):
        event.owner_id = "u1"  # type: ignore[misc]


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
    assert UnknownExploration("x9").code == "unknown_exploration"
    assert UnknownExploration("x9").details["exploration_id"] == "x9"
    assert ExplorationBusy("p9").code == "exploration_busy"
def test_unknown_ref_message_explains_proposal_ids():
    """真跑里模型把 `r-f2a00f4e#08` 当出处编号写——提示要说清那是另一个命名空间。"""

    message = unknown_ref_message(["r-f2a00f4e#08"], what="意见引用了不存在的编号")

    assert "提案编号" in message
    assert "E" in message


def test_unknown_ref_message_explains_section_ids():
    """真跑里模型把章节号 `s5` 当出处编号（2026-10-10 讨论区实测）。

    提示词里同时可见五套编号：`s1..s13` 章节、`r-xxx#01` 提案、`feature#slug` 功能、
    `t0..tN` 对话轮次、`E1..` 出处——只有最后一种合法。漏掉任何一套，模型就只能
    收到"编号不存在"，而重装只有一次机会，这条文案是它唯一一次指引。
    """

    message = unknown_ref_message(["s5", "s12"], what="回答引用了不存在的编号")

    assert "章节" in message
    assert "E" in message


def test_unknown_ref_message_explains_feature_slugs():
    message = unknown_ref_message(["feature#order-export"], what="回答引用了不存在的编号")

    assert "功能" in message
    assert "E" in message


def test_unknown_ref_message_explains_turn_indices():
    """`t5` / `t6` 来自 `render_thread_history` 的 `[t5 用户]` 行号（讨论区实测）。"""

    message = unknown_ref_message(["t5", "t6"], what="回答引用了不存在的编号")

    assert "轮次" in message or "对话" in message
    assert "E" in message


def test_unknown_ref_message_still_answers_for_a_bare_number():
    """不认识的形态不许吞掉——兜底仍是"哪个编号不存在"，只是没有额外指引。"""

    message = unknown_ref_message(["E9999"], what="回答引用了不存在的编号")

    assert "E9999" in message
    assert "不存在" in message
