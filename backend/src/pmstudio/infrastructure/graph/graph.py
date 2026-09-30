"""LangGraph 骨架。**它是编排的实现技术，不是真相源。**

- state 只放 `{command, round_id, view}`：走哪一步由 Round 聚合的 `step` 决定；
- 不挂 checkpointer（否则图与聚合会成为两个真相源，见计划 §3.7）；
- 每次用户动作 = 一次 `ainvoke`，入口按命令与 `step` 路由；
- 节点与路由器**必须 async**（同步版本在这个 langgraph 上会挂死）。
"""

import asyncio
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from pmstudio.application.commands import RoundCommand
from pmstudio.application.dto import RoundView
from pmstudio.application.errors import RoundBusy
from pmstudio.application.use_cases import (
    ConfirmDraft,
    DigRound,
    GetRound,
    IntegrateRound,
    RetryRound,
    RunSeatRound,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
)
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.ids import ProjectId, RoundId

__all__ = ["RESUME_ROUTES", "RoundCommand", "RoundRunner", "RoundState", "build_graph"]

CANCEL_WAIT_SECONDS = 5.0


class RoundState(TypedDict, total=False):
    """**只放引用与本次命令**（白名单由 tests/architecture/test_graph_state.py 钉住）。"""

    command: RoundCommand
    round_id: str
    view: RoundView | None


RESUME_ROUTES: dict[str, str] = {
    Step.STARTED.value: "analyze",
    Step.CLAIMS_READY.value: END,
    Step.SEATS_RUNNING.value: "seat_round",
    Step.AWAITING_ANSWERS.value: END,
    Step.AWAITING_CONFIRM.value: END,
    Step.DONE.value: END,
    # 放弃是终点：`retry` 一个已放弃的轮不该 500，也不该复活它
    Step.ABANDONED.value: END,
    # 兜底：老库里的坏行（step=failed）也得有个出口，否则路由表 KeyError → 500。
    # 正常路径进不来——`Round.resume()` 会先把坏 `at_step` 拦下来（04-未决 C9）。
    Step.FAILED.value: END,
}

COMMAND_ROUTES: dict[str, str] = {
    "start": "start",
    "submit_opening": "opening",
    "submit_answers": "answers",
    "confirm": "commit",
    "retry": "resume",
    # 草案卡上的"继续深挖"：回到席位轮再问一批（P14 C6）
    "dig": "dig",
}


class RoundRunner:
    def __init__(
        self,
        *,
        start_round: StartRound,
        submit_opening: SubmitOpening,
        run_seat_round: RunSeatRound,
        integrate_round: IntegrateRound,
        submit_answers: SubmitAnswers,
        confirm_draft: ConfirmDraft,
        retry_round: RetryRound,
        dig_round: DigRound,
        get_round: GetRound,
    ) -> None:
        self._start_round = start_round
        self._submit_opening = submit_opening
        self._run_seat_round = run_seat_round
        self._integrate_round = integrate_round
        self._submit_answers = submit_answers
        self._confirm_draft = confirm_draft
        self._retry_round = retry_round
        self._dig_round = dig_round
        self._get_round = get_round
        self._running_rounds: set[str] = set()
        #: 这一轮正在跑的那个 asyncio 任务（`cancel()` 要按 round_id 找到它）
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._graph = build_graph(self)

    @property
    def graph(self) -> Any:
        """编译后的图。给测试与排查用（守卫要断言它没挂 checkpointer）。"""

        return self._graph

    async def run(self, command: RoundCommand) -> RoundView:
        """跑一次用户动作。**同一个 round 同时只允许一个动作**。

        双击"继续/重试"、两个标签页、前端重发都会撞上；真撞过一次，代价是那一轮永久锁死
        （`04-未决.md` C9）。`start` 没有 round_id，不参与串行化——两轮新输入本来就互不相干。
        """

        key = command.round_id or ""
        if key and key in self._running_rounds:
            raise RoundBusy(round_id=key)
        if key:
            self._running_rounds.add(key)
            current = asyncio.current_task()
            if current is not None:
                self._tasks[key] = current
        try:
            final: dict[str, Any] = await self._graph.ainvoke(
                {"command": command, "round_id": key, "view": None}
            )
        except asyncio.CancelledError:
            # 取消**不吞**：谁取消的谁负责收尾（`AbandonRound` 先写 abandoned 再调 cancel），
            # 吞掉会掩盖"进程正在关"这类外部取消。
            raise
        finally:
            self._running_rounds.discard(key)
            self._tasks.pop(key, None)
        view = final.get("view")
        if view is None:
            # 图停在"等人"的边上（例如 retry 后本来就是 claims_ready）——
            # 把这一轮现在的投影读回来给界面，而不是报错。
            round_id = final.get("round_id") or command.round_id
            if not round_id:
                raise RuntimeError("这次调用既没有 round_id，也没有产生新的一轮")
            view = await self._get_round(round_id=RoundId(round_id))
        return view

    async def cancel(self, round_id: RoundId) -> None:
        """取消这一轮正在跑的任务。没有在跑的直接返回（它可能停在等人，或进程崩过）。"""

        task = self._tasks.get(str(round_id))
        if task is None or task.done():
            return
        task.cancel()
        # 等它退出（让它把 CancelledError 传出去）。用 wait 不用 wait_for：
        # wait_for 超时会**再取消一次**，那不是帮忙。
        await asyncio.wait({task}, timeout=CANCEL_WAIT_SECONDS)

    # —— 节点与路由器：一律 async def ——
    async def node_start(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        round_id = await self._start_round.create(
            project_id=ProjectId(command.project_id or ""),
            user_input=command.user_input,
            material_ids=command.material_ids,
        )
        return {"round_id": str(round_id)}

    async def node_analyze(self, state: RoundState) -> dict[str, Any]:
        round_id = RoundId(_round_id(state))
        await self._start_round.analyze(round_id=round_id)
        return {"view": await self._get_round(round_id=round_id)}

    async def node_opening(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        await self._submit_opening(
            round_id=RoundId(_round_id(state)),
            seats=command.seats,
            claim_edits=command.claim_edits,
        )
        return {}

    async def node_seat_round(self, state: RoundState) -> dict[str, Any]:
        await self._run_seat_round(round_id=RoundId(_round_id(state)))
        return {}

    async def node_integrate(self, state: RoundState) -> dict[str, Any]:
        round_id = RoundId(_round_id(state))
        await self._integrate_round(round_id=round_id)
        return {"view": await self._get_round(round_id=round_id)}

    async def node_answers(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        await self._submit_answers(
            round_id=RoundId(_round_id(state)),
            form_id=command.form_id,
            answers=command.answers,
            skipped=command.skipped,
        )
        return {}

    async def node_commit(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        round_id = RoundId(_round_id(state))
        await self._confirm_draft(round_id=round_id, accepted=command.accepted)
        return {"view": await self._get_round(round_id=round_id)}

    async def node_resume(self, state: RoundState) -> dict[str, Any]:
        await self._retry_round(round_id=RoundId(_round_id(state)))
        return {}

    async def node_dig(self, state: RoundState) -> dict[str, Any]:
        await self._dig_round(round_id=RoundId(_round_id(state)))
        return {}

    async def route_by_command(self, state: RoundState) -> str:
        """返回 path_map 的**键**（命令种类），由 `COMMAND_ROUTES` 再映射到节点。"""

        kind = state["command"].kind
        if kind not in COMMAND_ROUTES:
            raise ValueError(f"未知命令：{kind}")
        return kind

    async def route_after_resume(self, state: RoundState) -> str:
        """返回 path_map 的**键**（恢复后的 step）。"""

        view = await self._get_round(round_id=RoundId(_round_id(state)))
        return view.step


def _round_id(state: RoundState) -> str:
    round_id = state.get("round_id") or state["command"].round_id
    if not round_id:
        raise ValueError("这次调用没有 round_id")
    return round_id


def build_graph(runner: RoundRunner) -> Any:
    graph = StateGraph(RoundState)
    graph.add_node("start", runner.node_start)
    graph.add_node("analyze", runner.node_analyze)
    graph.add_node("opening", runner.node_opening)
    graph.add_node("seat_round", runner.node_seat_round)
    graph.add_node("integrate", runner.node_integrate)
    graph.add_node("answers", runner.node_answers)
    graph.add_node("commit", runner.node_commit)
    graph.add_node("resume", runner.node_resume)
    graph.add_node("dig", runner.node_dig)

    graph.set_conditional_entry_point(runner.route_by_command, COMMAND_ROUTES)
    graph.add_edge("start", "analyze")
    graph.add_edge("analyze", END)
    graph.add_edge("opening", "seat_round")
    graph.add_edge("seat_round", "integrate")
    graph.add_edge("integrate", END)
    graph.add_edge("answers", "seat_round")
    graph.add_edge("commit", END)
    graph.add_conditional_edges("resume", runner.route_after_resume, RESUME_ROUTES)
    # dig 之后按 step 继续走：seats_running → seat_round → integrate（与 resume 同一张表）
    graph.add_conditional_edges("dig", runner.route_after_resume, RESUME_ROUTES)
    return graph.compile()
