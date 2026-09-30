"""测试替身。**只许在测试里 import**（生产路径由 test_no_fakes_in_production 守卫）。"""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pmstudio.application.errors import ModelCallFailed
from pmstudio.application.events import Event
from pmstudio.application.ports import ModelReply, ModelRequest, SeatDefinition
from pmstudio.domain.shared.ids import ProjectId, RoundId

if TYPE_CHECKING:
    from pmstudio.application.dto import ParsedMaterial

SCRIPTED_MODEL = "scripted"


class ScriptedModelGateway:
    """按 purpose（席位再看 metadata.seat_id）返回预置 JSON 的假网关。

    脚本里没有对应回复时**直接失败**——不返回空串。理由见 learnings L2：
    空返回会被下游当成"该专家没有意见"，静默缺掉一整个专业面。

    同一个 purpose 可以给一串回复（`{"integration": [第一次, 第二次]}`），
    用来测"第一次过不了校验 → 重装一次"这类路径；用完就一直用最后一条。
    """

    def __init__(
        self,
        *,
        replies: Mapping[str, str | Sequence[str]] = {},
        seat_replies: Mapping[str, str | Sequence[str]] | None = None,
    ) -> None:
        self._replies = {key: _script(value) for key, value in replies.items()}
        self._seat_replies = {key: _script(value) for key, value in (seat_replies or {}).items()}
        self.requests: list[ModelRequest] = []

    @property
    def purposes(self) -> list[str]:
        return [request.purpose for request in self.requests]

    async def complete(self, request: ModelRequest) -> ModelReply:
        self.requests.append(request)
        if request.purpose == "seat_review":
            seat_id = request.metadata.get("seat_id", "")
            script = self._seat_replies.get(seat_id)
            if script is None:
                raise ModelCallFailed(f"脚本里没有席位 {seat_id} 的回复", purpose="seat_review")
            return ModelReply(text=_next(script), model=SCRIPTED_MODEL)
        script = self._replies.get(request.purpose)
        if script is None:
            raise ModelCallFailed(f"脚本里没有 {request.purpose} 的回复", purpose=request.purpose)
        return ModelReply(text=_next(script), model=SCRIPTED_MODEL)


def _script(value: str | Sequence[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def _next(script: list[str]) -> str:
    return script.pop(0) if len(script) > 1 else script[0]


class HangingSeatGateway(ScriptedModelGateway):
    """claims 照常返回，席位调用**挂住**（测取消与超时用）。

    它模拟的是"provider 一直不返回"：那一刻整轮停在 `await` 上，
    `RoundRunner.cancel()` 要能把这一轮从那儿拉回来。
    """

    def __init__(
        self,
        *,
        replies: Mapping[str, str | Sequence[str]] = {},
        hang_seconds: float = 30.0,
    ) -> None:
        super().__init__(replies=replies, seat_replies={})
        self.seat_calls = 0
        self._hang_seconds = hang_seconds

    async def complete(self, request: ModelRequest) -> ModelReply:
        if request.purpose == "seat_review":
            self.requests.append(request)
            self.seat_calls += 1
            await asyncio.sleep(self._hang_seconds)
            raise AssertionError("挂了这么久还被叫醒：取消没生效")
        return await super().complete(request)


class RecordingPrompts:
    """提示词库替身：记录每次 render 的槽位，返回可读的拼接文本。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def render(self, name: str, **slots: str) -> str:
        self.calls.append((name, dict(slots)))
        body = "\n".join(f"{key}={value}" for key, value in sorted(slots.items()))
        return f"{name}\n{body}"


class FixedClock:
    def __init__(self, moment: datetime | None = None) -> None:
        self._moment = moment or datetime(2026, 9, 22, 10, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._moment


class SeqIds:
    """可预期的 id 生成器：r1 / r2 / f1 …"""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}{self._counters[prefix]}"


class RecordingPublisher:
    """记录事件的事件发布器替身（同时可当 EventStream 用）。"""

    def __init__(self) -> None:
        self.published: list[Event] = []
        self._subscribers: list[asyncio.Queue[Event]] = []

    @property
    def names(self) -> list[str]:
        return [event.name for event in self.published]

    def publish(self, event: Event) -> None:
        self.published.append(event)
        for queue in list(self._subscribers):
            queue.put_nowait(event)

    async def subscribe(self):  # type: ignore[no-untyped-def]
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.remove(queue)


class StaticSeatCatalog:
    """固定席位目录替身（真实现从 TOML 读，见 infrastructure/seats）。"""

    def __init__(self, seats: Sequence[SeatDefinition]) -> None:
        self._seats = tuple(seats)

    def all(self) -> tuple[SeatDefinition, ...]:
        return self._seats

    def nominate(self, *, user_input: str) -> tuple[SeatDefinition, ...]:
        return self._seats

    def by_id(self, seat_id: str) -> SeatDefinition:
        for seat in self._seats:
            if seat.seat_id == seat_id:
                return seat
        raise KeyError(seat_id)

    def titles(self) -> dict[str, str]:
        return {seat.seat_id: seat.title for seat in self._seats}


class RecordingWebSearch:
    """联网检索替身：记录每一次调用，按配置返回结果或抛错（**只在测试里**）。

    它不是"假实现"——生产路径的实现是 `infrastructure/tools/tavily.py`；
    这里的作用是让"没勾就不联网"这条口径**可被断言**（`calls == []`）。
    """

    def __init__(
        self,
        *,
        returns: Sequence["ParsedMaterial"] = (),
        failure: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, int]] = []
        self.returns = tuple(returns)
        self.failure = failure

    async def search(self, *, query: str, limit: int) -> tuple["ParsedMaterial", ...]:
        self.calls.append((query, limit))
        if self.failure is not None:
            raise self.failure
        return self.returns


class StaticWorkspaceIndex:
    """查询投影替身：表里只有本夹具造过的项目（真实现见 infrastructure/sqlite）。"""

    def __init__(self) -> None:
        from pmstudio.application.dto import ProjectSummaryView

        self._summaries: dict[str, ProjectSummaryView] = {}
        self._latest: dict[str, RoundId] = {}
        self._outcomes: dict[str, list] = {}
        self._heads: dict[str, list] = {}
        self._opinions: dict[str, list] = {}

    def set_summary(self, summary) -> None:  # type: ignore[no-untyped-def]
        self._summaries[summary.project_id] = summary

    def set_latest_round(self, project_id: str, round_id: RoundId) -> None:
        self._latest[project_id] = round_id

    def summary(self, project_id: ProjectId):  # type: ignore[no-untyped-def]
        return self._summaries[str(project_id)]

    def list_summaries(self):  # type: ignore[no-untyped-def]
        return tuple(self._summaries.values())

    def latest_round_id(self, project_id: ProjectId) -> RoundId | None:
        return self._latest.get(str(project_id))

    def set_round_outcomes(self, project_id: str, outcomes) -> None:  # type: ignore[no-untyped-def]
        """测试注入口：把最近几轮的结果投影塞进来（**最新在前**，真实现是查 `rounds` 表）。"""

        self._outcomes[project_id] = list(outcomes)

    def recent_rounds(self, project_id: ProjectId, *, limit: int = 5):  # type: ignore[no-untyped-def]
        return tuple(self._outcomes.get(str(project_id), ())[:limit])

    def set_round_heads(self, project_id: str, heads) -> None:  # type: ignore[no-untyped-def]
        """测试注入口：轮次列表（**最新在前**；真实现是查 `rounds` 表）。"""

        self._heads[project_id] = list(heads)

    def list_rounds(self, project_id: ProjectId, *, limit: int = 20):  # type: ignore[no-untyped-def]
        return tuple(self._heads.get(str(project_id), ())[:limit])

    def set_opinions(self, project_id: str, opinions) -> None:  # type: ignore[no-untyped-def]
        """测试注入口：最近几轮的席位发言（真实现是读 `rounds.payload.seat_reviews`）。"""

        self._opinions[project_id] = list(opinions)

    def recent_opinions(self, project_id: ProjectId, *, limit_rounds: int = 10):  # type: ignore[no-untyped-def]
        return tuple(self._opinions.get(str(project_id), ()))
