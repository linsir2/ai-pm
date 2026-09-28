"""应用端口：只声明能力，实现在 infrastructure，由 bootstrap 注入。"""

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pmstudio.application.events import Event
from pmstudio.domain.shared.ids import DocumentId, ProjectId, RoundId

if TYPE_CHECKING:
    from pmstudio.application.commands import RoundCommand
    from pmstudio.application.dto import ProjectSummaryView, RoundView


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelRequest:
    purpose: str
    messages: tuple[ModelMessage, ...]
    schema_name: str
    response_schema: Mapping[str, object]
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelReply:
    text: str
    model: str
    usage: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SeatDefinition:
    seat_id: str
    title: str
    focus: str
    trigger_keywords: tuple[str, ...] = ()
    always_on: bool = False


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class IdGenerator(Protocol):
    def new(self, prefix: str) -> str: ...


@runtime_checkable
class ModelGateway(Protocol):
    async def complete(self, request: ModelRequest) -> ModelReply: ...


@runtime_checkable
class ModelCallRecorder(Protocol):
    """留样的旁路出口（`02` §5.3）：记下每一次模型调用，给排查用，不参与业务。

    实现是 `infrastructure/sqlite/model_calls.py`；网关默认不接（`recorder=None` 时什么都不记）。
    提示词给的是**原文**——只存哈希复现不了，而复现是这张表唯一的目的。
    """

    def record(
        self,
        *,
        round_id: str | None,
        purpose: str,
        model: str,
        prompt_sha: str,
        prompt_text: str,
        reply_text: str,
        usage: Mapping[str, object],
        duration_ms: int,
        created_at: datetime,
    ) -> None: ...


@runtime_checkable
class PromptLibrary(Protocol):
    def render(self, name: str, **slots: str) -> str: ...


@runtime_checkable
class SeatCatalog(Protocol):
    def all(self) -> tuple[SeatDefinition, ...]: ...
    def nominate(self, *, user_input: str) -> tuple[SeatDefinition, ...]: ...
    def by_id(self, seat_id: str) -> SeatDefinition: ...


@runtime_checkable
class EventPublisher(Protocol):
    def publish(self, event: Event) -> None: ...


@runtime_checkable
class EventStream(Protocol):
    def subscribe(self) -> AsyncIterator[Event]: ...


@runtime_checkable
class WorkspaceIndex(Protocol):
    """只读查询投影：项目摘要、项目列表、某项目最新一轮。

    它不是第二份真相源——数据仍来自 documents / rounds 两张表，这里只是查询入口，
    因为 `DocumentReader` / `RoundReader` 是聚合入口，不该长出"列全部项目"这种查询。
    """

    def summary(self, project_id: ProjectId) -> "ProjectSummaryView": ...
    def list_summaries(self) -> tuple["ProjectSummaryView", ...]: ...
    def latest_round_id(self, project_id: ProjectId) -> RoundId | None: ...


@runtime_checkable
class ProjectFactory(Protocol):
    """建项目：造一份 13 章空骨架文档 + 空账本并落库。

    它与 `DocumentWriter` 是两回事：它**只能造空文档**，改已有文档仍然只有
    `ProposalCommitter` 一条路（"AI 不写文档"这条不变式靠这个划分保住）。
    """

    def create(self, *, project_id: ProjectId, title: str, now: datetime) -> DocumentId: ...


@runtime_checkable
class RoundOrchestrator(Protocol):
    """图的入口（实现是 infrastructure 里的 `RoundRunner`）。

    `api` 只认这个协议，所以它不必 import infrastructure——分层守卫因此能保持全绿。
    """

    async def run(self, command: "RoundCommand") -> "RoundView": ...


@runtime_checkable
class RoundCanceller(Protocol):
    """把正在跑的那一轮停下来（实现是 infrastructure 里的 `RoundRunner`）。

    只有"放弃这一轮"用它。它不是写能力——取消只负责让正在等模型的那次调用停下来；
    磁盘上的状态由 `AbandonRound` 写（写法只有一处）。
    """

    async def cancel(self, round_id: RoundId) -> None: ...
