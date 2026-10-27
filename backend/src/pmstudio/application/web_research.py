"""联网申请的执行层：批准之后才联网，一条结果一个编号。

职责只有三段：

1. **查**（事务外）：按请求并发检索，失败/空结果/预算挤压都留痕，不判死整轮；
2. **选**（事务外）：已有 web 结果只复用**已覆盖块**，新结果按选中块发号；
   已有 + 新结果共用 `WEB_BUDGET`，并把本步选中的块记成 `Round.web_selection`；
3. **落**（一个 `UoW`）：材料、出处、请求状态、留痕一起写。

它不读模型、不拼提示词。`SubmitOpening` 与 `RunSeatRound` / `IntegrateRound`
共用同一个实例；后两者在进入模型步之前调 `execute_approved`。
"""

import asyncio
import hashlib
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pmstudio.application.context import (
    WEB_BUDGET,
    WEB_SECTION_OVERHEAD,
    select_chunks,
    web_block_cost,
)
from pmstudio.application.dto import (
    NO_SOURCE_REF,
    MaterialChunk,
    MaterialPacket,
    ParsedMaterial,
)
from pmstudio.application.errors import (
    UnknownWebRequest,
    WebExecutionLimitReached,
    WebRequestNotPending,
)
from pmstudio.application.material_refs import external_ref as _external_ref
from pmstudio.application.material_refs import material_kind as _material_kind
from pmstudio.application.ports import (
    Clock,
    MaterialStore,
    MaterialVectorIndex,
    WebSearchGateway,
    WorkspaceIndex,
)
from pmstudio.domain.null_repos import NO_VECTORS
from pmstudio.domain.ports import (
    EvidenceReader,
    EvidenceWriter,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.round import (
    DroppedItem,
    DropSource,
    MaterialRef,
    Round,
)
from pmstudio.domain.round.step import Step
from pmstudio.domain.round.web import (
    WebRequest,
    WebRequestState,
    used_web_request_count,
)
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import EvidenceId, ProjectId, RoundId
from pmstudio.harness.errors import ToolCallFailed
from pmstudio.harness.model import failure
from pmstudio.harness.tools.recorder import ToolCallRecorder
from pmstudio.harness.tools.registry import ToolCatalog, ToolDefinition

#: 工具目录里"联网搜索"的 kind（`web_search.toml` 写的就是它）
WEB_SEARCH_KIND = "web_search"
#: 联网结果在信息包里的 origin（`TavilySearchGateway` 盖的就是它）
WEB_ORIGIN = "web_search"

#: 每轮最多执行几条联网申请（`approved/done/failed` 计数；`proposed/rejected` 不占）。
MAX_WEB_EXECUTIONS = 3

def web_requests_used(round_: Round) -> int:
    """这一轮已占用的联网执行名额（`approved + done + failed`）。"""

    return used_web_request_count(round_.web_requests)


def approved_within_limit(round_: Round, limit: int) -> tuple[WebRequest, ...]:
    """超额时还能执行的 `approved`：先让 done/failed 占位，剩下的才轮到 approved。

    顺序只认申请挂载顺序（没有单独的批准时间戳）；超出部分留给逐条恢复。
    """

    finished = sum(
        1
        for item in round_.web_requests
        if item.state in (WebRequestState.DONE, WebRequestState.FAILED)
    )
    slots = max(0, limit - finished)
    approved = tuple(
        item for item in round_.web_requests if item.state is WebRequestState.APPROVED
    )
    return approved[:slots]


@dataclass(frozen=True, slots=True)
class WebPick:
    """这一趟选中并要落库的一组块：`eid` 有值表示复用已有编号。"""

    packet: MaterialPacket
    chunks: tuple[MaterialChunk, ...]
    eid: EvidenceId | None


@dataclass(frozen=True, slots=True)
class WebRequestResult:
    """一条申请执行完之后该记的状态。"""

    request_id: str
    state: WebRequestState
    result_note: str | None = None


@dataclass(frozen=True, slots=True)
class WebExecution:
    """事务外查完、选完的结果；调用方拿它一个 `UoW` 落库。"""

    picks: tuple[WebPick, ...]
    requests: tuple[WebRequestResult, ...]
    drops: tuple[DroppedItem, ...]
    selection: tuple[tuple[str, str], ...]


def tool_limit(tool: ToolDefinition, field: str) -> int:
    """读联网工具的上限，**目录必须声明**。

    缺声明时报"声明坏了"，不是"你超了 0 条"：后者会让人以为是自己勾多了。
    """

    value = getattr(tool, field)
    if value <= 0:
        raise ShapeError(
            f"工具声明 {tool.tool_id} 缺 {field}：联网工具必须声明这一轮的上限",
            tool_id=tool.tool_id,
            field=field,
        )
    return value


def web_search_tool(tools: ToolCatalog, *, keys: frozenset[str]) -> ToolDefinition | None:
    """当前配置下**真的能用**的联网工具；没配密钥（或目录里没有）就是 None。"""

    for item in tools.available(has=keys):
        if item.kind == WEB_SEARCH_KIND:
            return item
    return None


def web_execution_limit(tools: ToolCatalog, *, keys: frozenset[str]) -> int:
    """本进程**生效**的每轮联网执行名额：`min(tool.max_queries, 3)`；没工具是 0。

    唯一的算法：决策、执行点与 `/schema.limits.max_web_executions` 都读它，
    前端不抄数字。工具声明坏了（`max_queries <= 0`）时与执行点同样当场报。
    """

    tool = web_search_tool(tools, keys=keys)
    if tool is None:
        return 0
    return min(tool_limit(tool, "max_queries"), MAX_WEB_EXECUTIONS)


def claim_web_request(
    *,
    rounds: RoundReader,
    round_writer: RoundWriter,
    uow: UnitOfWork,
    round_id: RoundId,
    request_id: str,
    limit: int,
) -> WebRequest:
    """在**一个 UoW 内**把 `proposed → approved`：这就是 claim（P3b）。

    名额必须在这里 fresh read 重算：另一个决策 / 开场可能刚把 used 推到上限，
    事务外读到的旧值不能用来放行。`used >= limit` 409，且**不留 approved**；
    `approved/done/failed` 都占名额，`proposed/rejected` 不占。
    """

    with uow:
        target = rounds.get_for_update(round_id)
        current = next(
            (item for item in target.web_requests if item.request_id == request_id),
            None,
        )
        if current is None:
            raise UnknownWebRequest(request_id, round_id=round_id)
        if target.step in (Step.DONE, Step.FAILED, Step.ABANDONED):
            raise WebRequestNotPending(
                "这一轮已经结束，不能再决策联网申请",
                round_id=str(round_id),
                step=str(target.step),
                request_id=request_id,
            )
        if current.state is not WebRequestState.PROPOSED:
            raise WebRequestNotPending(
                "这条联网申请已经决策过了",
                request_id=request_id,
                state=str(current.state),
            )
        used = used_web_request_count(target.web_requests)
        if used >= limit:
            raise WebExecutionLimitReached(
                round_id=round_id,
                limit=limit,
                used=used,
                tool_available=True,
            )
        target.decide_web_request(request_id, approve=True)
        round_writer.save(target)
        return next(
            item for item in target.web_requests if item.request_id == request_id
        )


async def search_one(
    web_search: WebSearchGateway, *, query: str, limit: int
) -> tuple[object, int]:
    """一条 query 的检索：**异常当值 + 单独量耗时**（毫秒）。"""

    started = time.monotonic()
    try:
        result = await web_search.search(query=query, limit=limit)
    except BaseException as error:  # noqa: BLE001 —— 异常当值，调用方按类型分派
        # **`BaseException` 是刻意的，别顺手改成 `Exception`**：`CancelledError` 也要
        # 当值返回、由调用方 `raise` 传播（与 `gather(return_exceptions=True)` 同口径）。
        return error, int((time.monotonic() - started) * 1000)
    return result, int((time.monotonic() - started) * 1000)


class WebResearch:
    """联网申请的执行点：查 → 选 → 一个 UoW 落库。"""

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        materials: MaterialStore,
        tools: ToolCatalog,
        available_tool_keys: frozenset[str],
        web_search: WebSearchGateway | None,
        index: WorkspaceIndex,
        uow: UnitOfWork,
        clock: Clock,
        vectors: MaterialVectorIndex = NO_VECTORS,
        tool_recorder: ToolCallRecorder | None = None,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._materials = materials
        self._tools = tools
        self._available_tool_keys = available_tool_keys
        self._web_search = web_search
        self._index = index
        self._uow = uow
        self._clock = clock
        self._vectors = vectors
        self._tool_recorder = tool_recorder

    # —— 对外：执行 ——

    def ensure_ready(self, action: str) -> int:
        """就绪检查（不联网、不写库）：工具声明与网关实例都在时返回生效名额。

        决策在 claim **之前**调它：目录说工具可用、实例却没接上网关属于装配错，
        必须当场抛出，不能先落一条 `approved` 把执行名额永久占住。
        """

        self._require_tool(action)
        return web_execution_limit(self._tools, keys=self._available_tool_keys)

    async def execute(
        self,
        round_id: RoundId,
        requests: Sequence[WebRequest],
        pass_index: int = 1,
    ) -> WebExecution | None:
        """只执行传进来的（已批准的）申请；网络在 `UoW` 外。

        返回 `None` 表示没有要执行的东西；否则调用方必须在一个 `UoW` 里
        调 `apply`，把材料/出处/请求状态/留痕一次写完。

        非工具异常（含取消）会先把本次请求标 `FAILED` + TOOL 留痕，再原样抛出：
        `approved` 不回退、名额照样消耗，下一次执行点不会再自动重跑网络。
        """

        requests = tuple(requests)
        if not requests:
            return None
        not_approved = [
            item.request_id
            for item in requests
            if item.state is not WebRequestState.APPROVED
        ]
        if not_approved:
            raise ShapeError(
                "只有已批准的联网申请能被执行", request_ids=not_approved
            )
        tool = self._require_tool("执行联网申请")
        limit = web_execution_limit(
            self._tools, keys=self._available_tool_keys
        )
        if len(requests) > limit:
            raise ShapeError(
                f"这一轮最多执行 {limit} 条联网申请，这次要执行 {len(requests)} 条",
                limit=limit,
                requested=len(requests),
            )
        try:
            return await self._run(round_id, requests, tool, pass_index)
        except BaseException as error:
            self._fail_requests(round_id, requests, error, pass_index)
            raise

    async def _run(
        self,
        round_id: RoundId,
        requests: Sequence[WebRequest],
        tool: ToolDefinition,
        pass_index: int,
    ) -> WebExecution:
        round_ = self._rounds.get(round_id)
        project_id = round_.project_id
        result_limit = tool_limit(tool, "max_results")
        web_search = self._web_search
        assert web_search is not None  # `_require_tool` 已经拦过
        outcomes = await asyncio.gather(
            *(
                search_one(web_search, query=request.query, limit=result_limit)
                for request in requests
            )
        )

        drops: list[DroppedItem] = []
        request_results: list[WebRequestResult] = []
        returned: list[MaterialPacket] = []
        seen_returned: set[str] = set()
        for request, (result, duration_ms) in zip(requests, outcomes, strict=True):
            if isinstance(result, ToolCallFailed):
                self._record_tool_call(
                    round_id,
                    query=request.query,
                    limit=result_limit,
                    duration_ms=duration_ms,
                    ok=False,
                    result_note=result.message,
                    error=result.message,
                    error_kind=failure.classify(result).value,
                )
                drops.append(
                    self._drop(
                        f"联网检索 {request.query}",
                        f"检索失败：{result.message}",
                        pass_index,
                    )
                )
                request_results.append(
                    WebRequestResult(
                        request_id=request.request_id,
                        state=WebRequestState.FAILED,
                        result_note=result.message,
                    )
                )
                continue
            if isinstance(result, BaseException):
                # 非工具异常照旧不吞：先记一笔，再原样抛给调用方。
                self._record_tool_call(
                    round_id,
                    query=request.query,
                    limit=result_limit,
                    duration_ms=duration_ms,
                    ok=False,
                    result_note=str(result),
                    error=str(result),
                )
                raise result
            self._record_tool_call(
                round_id,
                query=request.query,
                limit=result_limit,
                duration_ms=duration_ms,
                ok=True,
                result_note=f"{len(result)} 条",
            )
            if not result:
                drops.append(
                    self._drop(
                        f"联网检索 {request.query}",
                        "没有搜到结果",
                        pass_index,
                    )
                )
                request_results.append(
                    WebRequestResult(
                        request_id=request.request_id,
                        state=WebRequestState.DONE,
                        result_note="没有搜到结果",
                    )
                )
                continue
            request_results.append(
                WebRequestResult(
                    request_id=request.request_id,
                    state=WebRequestState.DONE,
                    result_note=f"搜到 {len(result)} 条结果",
                )
            )
            for draft in result:
                packet = self.keep(project_id, draft)
                if packet.material_id not in seen_returned:
                    seen_returned.add(packet.material_id)
                    returned.append(packet)

        covered, covered_order = self._covered(round_)
        packets, candidates = self._candidates(covered, covered_order, returned)
        by_id = {packet.material_id: packet for packet in packets}
        drops.extend(
            self._new_chunks_drops(returned, covered, pass_index)
        )
        picked, _ = select_chunks(
            packets,
            budget=WEB_BUDGET,
            cost=web_block_cost,
            section_overhead=WEB_SECTION_OVERHEAD,
            candidates=candidates,
        )
        selected_ids = {material_id for material_id, _ in picked}
        missing = [packet for packet in packets if packet.material_id not in selected_ids]
        if missing:
            drops.append(
                self._drop(
                    f"联网检索结果 {len(packets)} 条",
                    f"预算只带得下 {len(selected_ids)} 条，"
                    f"还有 {len(missing)} 条没带进去"
                    "（材料表里留着，搜得到）",
                    pass_index,
                )
            )

        grouped: dict[str, list[MaterialChunk]] = {}
        for material_id, chunk in picked:
            grouped.setdefault(material_id, []).append(chunk)
        picks = tuple(
            WebPick(
                packet=by_id[material_id],
                chunks=tuple(chunks),
                eid=(
                    covered[material_id][chunks[0].chunk_id]
                    if material_id in covered
                    else None
                ),
            )
            for material_id, chunks in grouped.items()
        )
        return WebExecution(
            picks=picks,
            requests=tuple(request_results),
            drops=tuple(drops),
            selection=tuple(
                dict.fromkeys((material_id, chunk.chunk_id) for material_id, chunk in picked)
            ),
        )

    def _fail_requests(
        self,
        round_id: RoundId,
        requests: Sequence[WebRequest],
        error: BaseException,
        pass_index: int,
    ) -> None:
        """本次执行被异常打断：已批准的请求标 `FAILED`，并留一条 TOOL 痕迹。

        **不回退 approved**：名额已经花掉，用户要重试就重新提申请。
        若请求在真实轮次里还没批准（opening 的联网前预演），这里没有可标的状态，
        只跳过——网络崩溃时轮次仍停在 opening。

        **不在这里发事件**：调用方拿到异常后，客户端要主动 refetch（与 B3 执行点
        同一口径）；只有正常返回的执行才由调用方发 `round.updated`。
        """

        note = f"执行中断：{type(error).__name__}: {error}"
        with self._uow:
            target = self._rounds.get_for_update(round_id)
            wanted = {request.request_id for request in requests}
            known = {
                item.request_id: item
                for item in target.web_requests
                if item.request_id in wanted
            }
            drops: list[DroppedItem] = []
            for request in requests:
                current = known.get(request.request_id)
                if current is None or current.state is not WebRequestState.APPROVED:
                    continue
                target.mark_web_request(
                    request.request_id,
                    WebRequestState.FAILED,
                    result_note=note,
                )
                drops.append(
                    self._drop(
                        f"联网检索 {request.query}",
                        note,
                        pass_index,
                    )
                )
            if drops:
                target.note_dropped(drops)
                self._round_writer.save(target)

    async def execute_approved(
        self, round_id: RoundId, pass_index: int = 1
    ) -> WebExecution | None:
        """执行当前已批准的申请；`allow_web` 且有名额时先自动批准 seat/integrator。

        `approved` 就是 claim（P3b：同一 round 只有一个 lease 持有者）：
        - `used < limit`：自动批准 seat/integrator 到名额满，再执行 approved；
        - `used == limit`：不自动批准，仍执行 already approved（它们已计入 used）；
        - `used > limit`：不自动批准、不抛 422，只执行仍在名额内的 approved；
          超额的那几条留给逐条恢复（execute / abandon）。没有可执行的就零 web 继续。
        """

        round_ = self._rounds.get(round_id)
        limit = web_execution_limit(self._tools, keys=self._available_tool_keys)
        used = web_requests_used(round_)
        approved = tuple(
            item for item in round_.web_requests if item.state is WebRequestState.APPROVED
        )
        auto_candidates = tuple(
            item
            for item in round_.web_requests
            if item.state is WebRequestState.PROPOSED
            and item.origin in ("seat", "integrator")
        )
        # `used == limit` 时没有名额可自动批准；别为了一个不存在的执行去
        # `ensure_ready`——网关缺失会把后续每个模型步都打成 422（M1）。
        if not approved and (
            used >= limit or not (round_.allow_web and auto_candidates)
        ):
            return None
        if used > limit:
            approved = approved_within_limit(round_, limit)
            if not approved:
                return None
            # 超额恢复态下，装配错误不能把后续每个模型步都打成 422；
            # 无网关就零 web 继续，把执行留给逐条恢复。
            if self._web_search is None:
                return None
        else:
            self.ensure_ready("执行联网申请")
        if round_.allow_web and auto_candidates and used < limit:
            with self._uow:
                target = self._rounds.get_for_update(round_id)
                slots = max(0, limit - web_requests_used(target))
                for item in target.web_requests:
                    if slots <= 0:
                        break
                    if (
                        item.state is WebRequestState.PROPOSED
                        and item.origin in ("seat", "integrator")
                    ):
                        target.decide_web_request(item.request_id, approve=True)
                        slots -= 1
                self._round_writer.save(target)
            round_ = self._rounds.get(round_id)
            approved = tuple(
                item
                for item in round_.web_requests
                if item.state is WebRequestState.APPROVED
            )
        if not approved:
            return None
        execution = await self.execute(round_id, approved, pass_index)
        if execution is None:
            return None
        with self._uow:
            target = self._rounds.get_for_update(round_id)
            ledger = self._evidence.get(target.project_id)
            self.apply(target, ledger, execution)
            self._evidence_writer.save(ledger)
            self._round_writer.save(target)
        return execution

    def apply(
        self, round_: Round, ledger: EvidenceLedger, execution: WebExecution
    ) -> None:
        """把一次执行的结果落进聚合：**必须在调用方的 UoW 里**。"""

        existing = {(ref.material_id, ref.chunk_id) for ref in round_.materials}
        refs: list[MaterialRef] = []
        for pick in execution.picks:
            eid = pick.eid
            if eid is None:
                eid = ledger.record(
                    kind=_material_kind(pick.packet),
                    text="\n".join(chunk.text for chunk in pick.chunks),
                    source=EvidenceSource(
                        external_ref=_external_ref(pick.packet, pick.chunks[0])
                    ),
                    now=self._clock.now(),
                )
            for chunk in pick.chunks:
                key = (pick.packet.material_id, chunk.chunk_id)
                if key in existing:
                    continue
                existing.add(key)
                refs.append(
                    MaterialRef(
                        material_id=pick.packet.material_id,
                        chunk_id=chunk.chunk_id,
                        eid=eid,
                    )
                )
        if refs:
            round_.add_materials(refs)
        for result in execution.requests:
            round_.mark_web_request(
                result.request_id, result.state, result_note=result.result_note
            )
        round_.note_web_selection(execution.selection)
        round_.note_dropped(execution.drops)

    # —— 材料入库（原 `SubmitOpening._keep`）——

    def keep(self, project_id: ProjectId, draft: ParsedMaterial) -> MaterialPacket:
        """一条检索结果入库：**去重键是 URL**，老档位的材料就地升级。

        规则与 `SubmitOpening._keep` 完全一致（兼容面：老测试直接调它）。
        """

        content = "\n".join(chunk.text for chunk in draft.chunks).encode("utf-8")
        digest = _web_key(draft.source_ref, content)
        existing = self._materials.find_by_sha(project_id, digest)
        if existing is None:
            packet = self._materials.put(
                project_id=project_id,
                parsed=draft,
                sha256=digest,
                content=content,
                now=self._clock.now(),
            )
            self._vectors.write(packet.material_id)
            return packet
        if not _is_other_parser_version(existing.parsed, draft):
            return existing
        if self._index.material_references(project_id).get(existing.material_id, ()):
            packet = self._materials.put(
                project_id=project_id,
                parsed=draft,
                sha256=_web_variant_key(draft.source_ref, content),
                content=content,
                now=self._clock.now(),
            )
        else:
            packet = self._materials.refresh(
                existing.material_id, parsed=draft, content=content
            )
            self._vectors.drop(packet.material_id)
        self._vectors.write(packet.material_id)
        return packet

    # —— 内部 ——

    def _require_tool(self, action: str) -> ToolDefinition:
        tool = web_search_tool(self._tools, keys=self._available_tool_keys)
        if tool is None:
            raise ShapeError(
                f"{action}需要联网工具，但服务端没有配好"
                "（没配密钥，或工具被停用）",
                tool_ids=[item.tool_id for item in self._tools.all()],
            )
        if self._web_search is None:
            raise ShapeError(
                "联网工具声明可用，但组装点没有接上实现（这一轮先别勾联网）",
                tool_id=WEB_SEARCH_KIND,
            )
        return tool

    def _covered(
        self, round_: Round
    ) -> tuple[dict[str, dict[str, EvidenceId]], list[str]]:
        """已有 web 结果的 `(material_id, chunk_id) → eid`，按首次引用顺序。"""

        covered: dict[str, dict[str, EvidenceId]] = {}
        order: list[str] = []
        for ref in round_.materials:
            packet = self._materials.get(ref.material_id)
            if (
                packet is None
                or packet.project_id != str(round_.project_id)
                or packet.parsed.origin != WEB_ORIGIN
            ):
                continue
            if ref.material_id not in covered:
                covered[ref.material_id] = {}
                order.append(ref.material_id)
            covered[ref.material_id].setdefault(ref.chunk_id, ref.eid)
        return covered, order

    def _candidates(
        self,
        covered: dict[str, dict[str, EvidenceId]],
        covered_order: Sequence[str],
        returned: Sequence[MaterialPacket],
    ) -> tuple[list[MaterialPacket], dict[str, frozenset[str]]]:
        """选块候选：已有的只允许已覆盖块；新结果允许全部块。"""

        packets: list[MaterialPacket] = []
        candidates: dict[str, frozenset[str]] = {}
        for material_id in covered_order:
            packet = self._materials.get(material_id)
            if packet is None:
                continue
            actual = {chunk.chunk_id for chunk in packet.parsed.chunks}
            allowed = frozenset(
                chunk_id for chunk_id in covered[material_id] if chunk_id in actual
            )
            if not allowed:
                continue
            packets.append(packet)
            candidates[material_id] = allowed
        for packet in returned:
            if packet.material_id in candidates:
                continue
            packets.append(packet)
            candidates[packet.material_id] = frozenset(
                chunk.chunk_id for chunk in packet.parsed.chunks
            )
        return packets, candidates

    def _new_chunks_drops(
        self,
        returned: Sequence[MaterialPacket],
        covered: dict[str, dict[str, EvidenceId]],
        pass_index: int,
    ) -> list[DroppedItem]:
        """同一条结果再次出现时，没发过号的新块**不发号**，但要说清。"""

        drops: list[DroppedItem] = []
        for packet in returned:
            known = covered.get(packet.material_id)
            if known is None:
                continue
            fresh = [
                chunk for chunk in packet.parsed.chunks if chunk.chunk_id not in known
            ]
            if not fresh:
                continue
            drops.append(
                self._drop(
                    f"联网检索结果 {packet.parsed.title or packet.parsed.source_ref}",
                    f"这条结果已有编号，只复用已覆盖的 {len(known)} 块；"
                    f"新出现的 {len(fresh)} 块不再发号",
                    pass_index,
                )
            )
        return drops

    def _drop(self, handle: str, reason: str, pass_index: int) -> DroppedItem:
        return DroppedItem(
            handle=handle,
            reason=reason,
            source=DropSource.TOOL,
            seat_id=None,
            pass_index=max(1, int(pass_index)),
        )

    def _record_tool_call(
        self,
        round_id: RoundId,
        *,
        query: str,
        limit: int,
        duration_ms: int,
        ok: bool,
        result_note: str,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> None:
        """工具这一趟的留样（旁路）：recorder 为 None 时零写入。"""

        if self._tool_recorder is None:
            return
        self._tool_recorder.record(
            trace_id=str(round_id),
            tool_id=WEB_SEARCH_KIND,
            arguments={"query": query, "limit": limit},
            result_note=result_note,
            duration_ms=duration_ms,
            created_at=self._clock.now(),
            ok=ok,
            error=error,
            error_kind=error_kind,
        )


def _web_key(source_ref: str, content: bytes) -> str:
    """联网材料的去重键：有 URL 按 URL，没有则按内容指纹。"""

    if source_ref and source_ref != NO_SOURCE_REF:
        return hashlib.sha256(f"web:{source_ref}".encode()).hexdigest()
    fingerprint = hashlib.sha256(content).hexdigest()
    return hashlib.sha256(f"web-nourl:{fingerprint}".encode()).hexdigest()


def _web_variant_key(source_ref: str, content: bytes) -> str:
    """已被编号引用的老材料换新内容时的变体键（带内容指纹保幂等）。"""

    fingerprint = hashlib.sha256(content).hexdigest()[:12]
    return hashlib.sha256(f"web:{source_ref}|{fingerprint}".encode()).hexdigest()


def _is_other_parser_version(existing: ParsedMaterial, draft: ParsedMaterial) -> bool:
    """已入库的那份是另一个解析器档位读的吗（同一 parser、版本不同）。"""

    return (
        existing.parser_id == draft.parser_id
        and existing.parser_version != draft.parser_version
    )
