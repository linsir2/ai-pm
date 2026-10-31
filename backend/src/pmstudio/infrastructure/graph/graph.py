"""LangGraph 骨架。**它是编排的实现技术，不是真相源。**

- state 只放 `{command, round_id, view}`：走哪一步由 Round 聚合的 `step` 决定；
- 不挂 checkpointer（否则图与聚合会成为两个真相源，见计划 §3.7）；
- 每次用户动作 = 一次 `ainvoke`，入口按命令与 `step` 路由；
- 节点与路由器**必须 async**（同步版本在这个 langgraph 上会挂死）。
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Any, TypedDict, TypeVar

from langgraph.graph import END, StateGraph

from pmstudio.application.commands import RoundCommand
from pmstudio.application.dto import RoundView
from pmstudio.application.errors import RoundBusy, RoundLeaseLost
from pmstudio.application.lease import (
    LEASE_HEARTBEAT_SECONDS,
    LEASE_TTL_SECONDS,
    LeaseHeartbeat,
    LeaseScope,
    clear_lease_scope,
    set_lease_scope,
)
from pmstudio.application.ports import LeaseStore
from pmstudio.application.use_cases import (
    AttachMaterials,
    ConfirmDraft,
    DecideWebRequest,
    DigRound,
    GetRound,
    IntegrateRound,
    RecoverWebRequest,
    RetryRound,
    RunSeatRound,
    RunSimInquiry,
    RunSimReview,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
)
from pmstudio.domain.round.step import Step
from pmstudio.domain.shared.ids import ProjectId, RoundId, UserId
from pmstudio.domain.shared.lease import LeaseKind
from pmstudio.infrastructure.postgres.owner_scope import require_owner

__all__ = ["RESUME_ROUTES", "RoundCommand", "RoundRunner", "RoundState", "build_graph"]

CANCEL_WAIT_SECONDS = 5.0
_T = TypeVar("_T")


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
    # 草案卡上的"去模拟调研"：轮内动作，不碰 step，跑完直接给界面读回最新投影
    "sim_inquiry": "sim_inquiry",
    # 草案卡上的"模拟评测"（M11）：同一条纪律——轮内动作，不碰 step
    "sim_review": "sim_review",
    # 「带进本轮」：轮内动作，不碰 step，跑完把最新投影读回界面
    "attach_materials": "attach_materials",
    # 逐条批准/拒绝联网申请（P3a）：批准即联网，轮内动作，不碰 step
    "decide_web_request": "decide_web_request",
    # 超额 approved 的逐条恢复（P3b）：执行一次或放弃，轮内动作，不碰 step
    "recover_web_request": "recover_web_request",
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
        run_sim_inquiry: RunSimInquiry,
        run_sim_review: RunSimReview,
        attach_materials: AttachMaterials,
        decide_web_request: DecideWebRequest,
        recover_web_request: RecoverWebRequest,
        leases: LeaseStore | None = None,
        lease_ttl_seconds: float = LEASE_TTL_SECONDS,
        lease_heartbeat_seconds: float = LEASE_HEARTBEAT_SECONDS,
    ) -> None:
        self._start_round = start_round
        self._submit_opening = submit_opening
        self._run_seat_round = run_seat_round
        self._integrate_round = integrate_round
        self._submit_answers = submit_answers
        self._confirm_draft = confirm_draft
        self._retry_round = retry_round
        self._dig_round = dig_round
        self._run_sim_inquiry = run_sim_inquiry
        self._run_sim_review = run_sim_review
        self._attach_materials = attach_materials
        self._decide_web_request = decide_web_request
        self._recover_web_request = recover_web_request
        self._get_round = get_round
        self._leases = leases
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_heartbeat_seconds = lease_heartbeat_seconds
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

        两道闸：`_running_rounds` 只保护本进程（快路径）；真正的互斥是 PG 行上的
        DB lease——两个 worker 各跑一个进程时，第二条请求在 `acquire` 处拿不到锁，
        直接 409，不会进图、不会重复调模型。

        `start` 没有 round_id，不参与串行化——两轮新输入本来就互不相干；
        它的租约由 `StartRound.create` 在 INSERT 的同一个 UoW 里拿。
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
            async with self._holding_round(key) as scope:
                try:
                    final: dict[str, Any] = await self._graph.ainvoke(
                        {"command": command, "round_id": key, "view": None}
                    )
                except asyncio.CancelledError:
                    if scope.lost:
                        # 心跳先发现锁被接管，主动把在途模型调用停下：
                        # 对调用方这是 409，不是"用户放弃了"。
                        target = scope.token.target_id if scope.token is not None else key
                        raise RoundLeaseLost(target) from None
                    # 外部取消**不吞**：谁取消的谁负责收尾（`AbandonRound` 先写 abandoned
                    # 再调 cancel），吞掉会掩盖"进程正在关"这类外部取消。
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

    async def short_action(
        self, round_id: RoundId, action: Callable[[], Awaitable[_T]]
    ) -> _T:
        """非图的轮内动作：同一把 round lease 内跑 `action`，跑完释放。

        与 `run` 的区别只在不经过 LangGraph、不把结果塞进 `RoundState`——
        返回什么由调用方定（`KeepSimResult` / `RoundView`）。取消语义与 `run` 相同：
        失租翻 409，外部取消原样抛。
        """

        key = str(round_id)
        if key in self._running_rounds:
            raise RoundBusy(round_id=key)
        self._running_rounds.add(key)
        current = asyncio.current_task()
        if current is not None:
            self._tasks[key] = current
        try:
            async with self._holding_round(key) as scope:
                try:
                    return await action()
                except asyncio.CancelledError:
                    if scope.lost:
                        raise RoundLeaseLost(key) from None
                    raise
        finally:
            self._running_rounds.discard(key)
            self._tasks.pop(key, None)

    @asynccontextmanager
    async def _holding_round(self, round_id: str) -> AsyncIterator[LeaseScope]:
        """把**同一个可变 scope** 放进 contextvar，并维护它的心跳与释放。

        为什么是可变对象：LangGraph 节点会复制 context，但复制的是引用；
        `StartRound.create` 在节点里 `bind` 的 token，`node_analyze` 的 save 必须看得见。
        每一层重新 `set` 一个新 scope 会让节点拿到旧值。

        没有注入 `leases` 时（Harness / 内存夹具）只放 scope，不做 DB 操作——
        生产装配点永远注入真 store。
        """

        if self._leases is None:
            scope = LeaseScope()
            context_token = set_lease_scope(scope)
            try:
                yield scope
            finally:
                clear_lease_scope(context_token)
            return

        owner = require_owner(None)
        holder = f"{os.getpid()}:{uuid.uuid4().hex}"
        scope = LeaseScope(holder=holder, owner=owner)
        ttl_ms = int(self._lease_ttl_seconds * 1000)
        if round_id:
            token = self._leases.acquire(
                LeaseKind.ROUND,
                round_id,
                owner,
                holder,
                ttl_ms,
            )
            if token is None:
                raise RoundBusy(round_id=round_id)
            scope.bind(token)
        context_token = set_lease_scope(scope)
        main = asyncio.current_task()

        def _stop_main() -> None:
            if main is not None and not main.done():
                main.cancel()

        heartbeat = asyncio.create_task(
            LeaseHeartbeat(
                scope=scope,
                renew=lambda token, _ttl: self._leases.renew(
                    token.kind,
                    token.target_id,
                    UserId(token.user_id),
                    token.holder,
                    token.token,
                    _ttl,
                ),
                interval_seconds=self._lease_heartbeat_seconds,
                ttl_ms=ttl_ms,
                on_lost=_stop_main,
            ).run()
        )
        try:
            yield scope
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await heartbeat
            lease = scope.token
            if lease is not None:
                # 尽力而为：0 行（被接管/已过期）不是错误，也绝不报 409。
                try:
                    self._leases.release(
                        lease.kind,
                        lease.target_id,
                        UserId(lease.user_id),
                        lease.holder,
                        lease.token,
                    )
                except Exception:  # noqa: BLE001 —— 收尾不许掩盖原异常
                    pass
            clear_lease_scope(context_token)

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
            seed_ids=command.seed_ids,
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
            web_queries=command.web_queries,
            allow_web=command.allow_web,
            approved_request_ids=command.approved_request_ids,
        )
        return {}

    async def node_decide_web_request(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        round_id = RoundId(_round_id(state))
        await self._decide_web_request(
            round_id=round_id,
            request_id=command.request_id,
            decision=command.decision,
        )
        return {"view": await self._get_round(round_id=round_id)}

    async def node_recover_web_request(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        round_id = RoundId(_round_id(state))
        await self._recover_web_request(
            round_id=round_id,
            request_id=command.request_id,
            action=command.recovery_action,
        )
        return {"view": await self._get_round(round_id=round_id)}

    async def node_attach_materials(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        round_id = RoundId(_round_id(state))
        await self._attach_materials(
            round_id=round_id,
            material_id=command.material_id,
            chunk_ids=command.chunk_ids,
        )
        return {"view": await self._get_round(round_id=round_id)}

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
        await self._confirm_draft(
            round_id=round_id,
            accepted=command.accepted,
            draft_id=command.draft_id,
            accepted_memory=command.accepted_memory,
        )
        return {"view": await self._get_round(round_id=round_id)}

    async def node_resume(self, state: RoundState) -> dict[str, Any]:
        await self._retry_round(round_id=RoundId(_round_id(state)))
        return {}

    async def node_dig(self, state: RoundState) -> dict[str, Any]:
        await self._dig_round(round_id=RoundId(_round_id(state)))
        return {}

    async def node_sim_inquiry(self, state: RoundState) -> dict[str, Any]:
        await self._run_sim_inquiry(round_id=RoundId(_round_id(state)))
        return {}

    async def node_sim_review(self, state: RoundState) -> dict[str, Any]:
        command = state["command"]
        await self._run_sim_review(
            round_id=RoundId(_round_id(state)),
            persona_ids=command.persona_ids,
            feature_slugs=command.feature_slugs,
        )
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
    graph.add_node("sim_inquiry", runner.node_sim_inquiry)
    graph.add_node("sim_review", runner.node_sim_review)
    graph.add_node("attach_materials", runner.node_attach_materials)
    graph.add_node("decide_web_request", runner.node_decide_web_request)
    graph.add_node("recover_web_request", runner.node_recover_web_request)

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
    # sim_inquiry / sim_review 都不碰 step：动作做完直接结束，界面读回最新投影（run() 的 view 回落）
    graph.add_edge("sim_inquiry", END)
    graph.add_edge("sim_review", END)
    graph.add_edge("attach_materials", END)
    # 决策是轮内动作：做完直接结束，界面读回最新投影（run() 的 view 回落）
    graph.add_edge("decide_web_request", END)
    # 逐条恢复同理：不碰 step，做完直接结束
    graph.add_edge("recover_web_request", END)
    return graph.compile()
