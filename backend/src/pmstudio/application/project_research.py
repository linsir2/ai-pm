"""项目级研究用例（P3b-1）：工具循环 → 综合 → **一个** UoW 落库。

它与 `application/web_research.py`（P3a 的联网申请执行层）不是一回事：
那个是"轮内批准后查一次、一条结果一个号"；这个是"项目级自己搜、自己抓、自己总结"，
产物是只读的 `ResearchReport`，唯一能写进轮次的动作是"挂来源卡"（Task 11）。

三段职责，与 `WebResearch` 同一条切法：

1. **跑**（UoW 外）：`run_tool_loop` 让模型自己搜、自己抓；工具不碰 DB；
2. **综合**（UoW 外）：拿来源卡做一次结构化调用，产出结论 + 开放问题；
3. **落**（一个 UoW）：每来源一个 E、每来源一份材料、报告挂到探索上。

**全有或全无**：零来源、综合失败、落库中途形状错，都不留"有材料没 E"的孤儿来源
——材料与账本在同一个 UoW 里，回滚就是都没有。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Sequence

from pmstudio.application.agents import schemas
from pmstudio.application.agents.researcher import ResearchSynthesizer
from pmstudio.application.dto import (
    NO_SOURCE_REF,
    ExplorationView,
    MaterialPacket,
    ResearchHit,
    exploration_view,
)
from pmstudio.application.errors import MaterialTooLarge
from pmstudio.application.events import publish_exploration
from pmstudio.application.lease import (
    LEASE_TTL_SECONDS,
    StartedExploration,
    acquire_exploration_lease,
)
from pmstudio.application.ports import (
    Clock,
    IdGenerator,
    LeaseStore,
    MaterialStore,
    ResearchToolbox,
    ResearchToolboxFactory,
)
from pmstudio.application.use_cases import MAX_MATERIALS_PER_PROJECT
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationKind,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.research import (
    MAX_RESEARCH_SOURCES,
    ResearchFinding,
    ResearchReport,
    ResearchSource,
    source_card_text,
)
from pmstudio.domain.ports import (
    DocumentReader,
    EvidenceReader,
    EvidenceWriter,
    ExplorationReader,
    ExplorationWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.errors import DomainError, ShapeError
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import ExplorationId, ProjectId, UserId
from pmstudio.harness.errors import ModelCallFailed, PacketParseError
from pmstudio.harness.events import EventPublisher
from pmstudio.harness.model.agentic import (
    AGENTIC_MAX_TURNS,
    MAX_TOOL_CALLS_PER_LOOP,
    run_tool_loop,
)
from pmstudio.harness.model.contracts import ModelGateway, ModelMessage, PromptLibrary

#: 一趟研究的**总时限**（秒）。单次模型超时是 150s，最坏 8 次 = 20 分钟——
#: 没有总闸时一个卡住的供应商会把 worker 占满，且失败原因只会是"进程可能重启过"
#: （下一次请求的 `clear_stale` 兜底）。10 分钟是"正常 30–120 秒 + 余量"的量级。
RESEARCH_TIMEOUT_SECONDS = 600

#: 综合最多两次（与 `MAX_INQUIRY_ATTEMPTS` / `MAX_INTEGRATION_ATTEMPTS` 同一个口径：
#: 第一次严格、第二次带上"上一次错在哪"重装，两次都不过就判这一趟失败）。
MAX_RESEARCH_ATTEMPTS = 2

#: 结论里引用来源的写法：`S1` / `s1` / 全角。**只认这一个形状**——与 `evidence_refs.EID`
#: 同一套归一化思路（那边认 E，这边认 S），不新增第三种编号。
_SOURCE_REF = re.compile(r"^[SsＳｓ]\s*0*(\d+)$")


def sources_for_prompt(hits: Sequence[ResearchHit]) -> str:
    """来源卡进提示词的形状：`[S1] 标题 / 网址 / 摘要`，序号即模型要引用的 refs。"""

    if not hits:
        return "（这一趟没有收到任何来源）"
    return "\n\n".join(
        f"[S{index}] {hit.title}\n{hit.url}\n{hit.excerpt}"
        for index, hit in enumerate(hits, start=1)
    )


def research_material_key(url: str, content: bytes) -> str:
    """研究来源的去重键：`web_research:{url}|{内容指纹}`。

    与 P3a 的 `web:{url}` **不同命名空间**：同一网址在两边各存一份（前者是"申请结果"，
    后者是"研究来源"），互不顶掉。带内容指纹是为了让"同一网址第二次抓到不同内容"
    成为新的一份（来源材料是冻结的，不能就地升级）。
    """

    digest = hashlib.sha256(content).hexdigest()[:12]
    return hashlib.sha256(f"web_research:{url}|{digest}".encode()).hexdigest()


def resolve_source_refs(
    refs: Sequence[str], *, count: int, lenient: bool
) -> tuple[int, ...]:
    """把 `S1..S5` 归一成 1 起的序号（去重、保序）。

    `lenient=False`：不认识的写法或超出 `count` 的序号抛 `ShapeError`
    （第一次尝试走这条，让模型自己改）；`lenient=True`：丢掉并返回剩下的
    （最后一次尝试，不为一处笔误判死整趟——与 `02` §11.0.2 同一条纪律）。
    """

    resolved: list[int] = []
    unknown: list[str] = []
    for raw in refs:
        match = _SOURCE_REF.match(str(raw).strip())
        index = int(match.group(1)) if match is not None else 0
        if not 1 <= index <= count:
            if not lenient:
                unknown.append(str(raw))
            continue
        if index not in resolved:
            resolved.append(index)
    if unknown:
        raise ShapeError(
            "结论引用了没有给出的来源：" + "、".join(unknown),
            refs=list(unknown),
            count=count,
        )
    return tuple(resolved)


class StartWebResearch:
    """发起并跑完一趟项目级研究。

    两段式与 `RunInquiry` 相同：`create` 只碰数据库（INSERT + 同一 UoW 拿租约），
    `execute` 才联网与调模型。这样"探索行已存在但没人持锁"的窗口不存在。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        explorations: ExplorationReader,
        exploration_writer: ExplorationWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        materials: MaterialStore,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
        gateway: ModelGateway,
        prompts: PromptLibrary,
        synthesizer: ResearchSynthesizer,
        toolbox: ResearchToolboxFactory,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._documents = documents
        self._explorations = explorations
        self._exploration_writer = exploration_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._materials = materials
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._events = events
        self._gateway = gateway
        self._prompts = prompts
        self._synthesizer = synthesizer
        self._toolbox = toolbox
        self._last_error = ""
        # 生产路径：INSERT 与 acquire 同一个 UoW；不注入时只做无租约的旧行为。
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    # —— 两段式 ——

    async def create(
        self,
        *,
        project_id: ProjectId,
        topic: str,
        owner: UserId | None = None,
    ) -> StartedExploration:
        """建一趟研究并拿租约。

        **不做接地检查**：研究是"写之前"的事，空文档照样可以研究
        （M8 调研要求文档非空，那条是"推演用户怎么想"，得有东西可推）。
        """

        self._documents.get(project_id)
        # 装配错**必须在建行之前**说清楚（与 P3a 的 `ensure_ready` 同一条纪律）：
        # 否则会先插一行、跑满 6 轮模型（真花钱）、再落一句"没有取到任何来源"，
        # 把"服务端没配好"伪装成"这一趟没搜到"。
        toolbox = self._toolbox()
        if not toolbox.ready:
            raise ShapeError(
                "服务端没有配置研究工具（联网搜索 / 抓取）——这一趟先别发起"
            )
        exploration_id = ExplorationId(self._ids.new("x"))
        with self._uow:
            exploration = Exploration.start(
                exploration_id=exploration_id,
                project_id=project_id,
                scope=ExplorationScope(kind=ScopeKind.TOPIC, text=topic),
                now=self._clock.now(),
                kind=ExplorationKind.WEB_RESEARCH,
            )
            self._exploration_writer.save(exploration)
            token = acquire_exploration_lease(
                leases=self._leases,
                project_id=project_id,
                exploration_id=exploration_id,
                owner=owner,
                holder=f"research:{self._ids.new('h')}",
                ttl_ms=self._lease_ttl_ms,
            )
        publish_exploration(self._events, self._clock, exploration)
        return StartedExploration(exploration_id=exploration_id, token=token)

    async def execute(self, exploration_id: ExplorationId) -> ExplorationView:
        """跑完一趟：工具循环（UoW 外）→ 综合（UoW 外）→ 一个 UoW 落库。"""

        exploration = self._explorations.get(exploration_id)
        if exploration.status is not ExplorationStatus.RUNNING:
            raise ShapeError(
                f"这一趟研究不在跑（status={exploration.status.value}）",
                exploration_id=str(exploration_id),
            )
        topic = (exploration.scope.text or "").strip()
        toolbox: ResearchToolbox = self._toolbox()
        first_message = self._prompts.render(
            "research_loop", topic=topic, source_cap=str(MAX_RESEARCH_SOURCES)
        )
        try:
            async with asyncio.timeout(RESEARCH_TIMEOUT_SECONDS):
                loop = await run_tool_loop(
                    gateway=self._gateway,
                    invoker=toolbox,
                    specs=toolbox.specs(),
                    messages=(ModelMessage(role="user", content=first_message),),
                    purpose="research_loop",
                    metadata={"exploration_id": str(exploration_id)},
                )
        except TimeoutError:
            return self._fail(
                exploration_id,
                f"研究超过总时限（{RESEARCH_TIMEOUT_SECONDS} 秒）",
            )
        except ModelCallFailed as error:
            return self._fail(exploration_id, f"模型调用失败：{error.message}")

        hits = toolbox.sources
        if not hits:
            return self._fail(
                exploration_id, "这一趟没有取到任何来源（搜索无结果或全部抓取失败）"
            )
        notes = [f"工具调用 {toolbox.attempts} 次"]
        if loop.hit_turn_limit:
            notes.append(
                f"工具循环到了上限（{MAX_TOOL_CALLS_PER_LOOP} 次 / {AGENTIC_MAX_TURNS} 轮），"
                "后面几轮没跑"
            )
        if toolbox.overflowed:
            notes.append(
                f"另有 {toolbox.overflowed} 条搜索结果未收"
                f"（已达来源上限 {MAX_RESEARCH_SOURCES} 条）"
            )

        try:
            async with asyncio.timeout(RESEARCH_TIMEOUT_SECONDS):
                outcome = await self._synthesize(
                    exploration_id, topic=topic, hits=hits, notes=notes
                )
        except TimeoutError:
            return self._fail(
                exploration_id,
                f"研究超过总时限（{RESEARCH_TIMEOUT_SECONDS} 秒）",
            )
        except ModelCallFailed as error:
            return self._fail(exploration_id, f"模型调用失败：{error.message}")
        if outcome is None:
            return self._fail(
                exploration_id,
                f"研究两次都过不了校验：{self._last_error or '未知原因'}",
            )

        self._record(exploration_id, hits=hits, outcome=outcome, notes=notes)
        return exploration_view(self._explorations.get(exploration_id))

    # —— 综合 ——

    async def _synthesize(
        self,
        exploration_id: ExplorationId,
        *,
        topic: str,
        hits: Sequence[ResearchHit],
        notes: list[str],
    ) -> schemas.ResearchPacket | None:
        """两次机会：第一次严格、第二次带上次的错重装。仍不过返回 None。

        校验放在这里（不是等落库时）：`text` 为空、`refs` 指向没给出的来源，
        都是**模型可自纠**的错，该走第二次重装；等 `_record` 里由领域抛出来的话，
        用户看到的是"研究结果落不了库"——指向一个不存在的问题。
        """

        repair = ""
        self._last_error = ""
        for attempt in range(1, MAX_RESEARCH_ATTEMPTS + 1):
            try:
                outcome = await self._synthesizer.run(
                    topic=topic,
                    sources=sources_for_prompt(hits),
                    tool_notes="；".join(notes),
                    exploration_id=str(exploration_id),
                    validation_error=repair,
                    packet_slot=f"a{attempt}",
                )
                for finding in outcome.findings:
                    if not finding.text.strip():
                        raise ShapeError("结论必须写清内容", finding_id=finding.text)
                    resolve_source_refs(
                        finding.refs, count=len(hits), lenient=False
                    )
                return outcome
            except ModelCallFailed:
                raise
            except (PacketParseError, ShapeError) as error:
                self._last_error = getattr(error, "message", str(error))
                repair = f"上一次的产物没通过校验：{self._last_error}"
        return None

    # —— 落库 ——

    def _record(
        self,
        exploration_id: ExplorationId,
        *,
        hits: Sequence[ResearchHit],
        outcome: schemas.ResearchPacket,
        notes: list[str],
    ) -> tuple[ResearchSource, ...] | None:
        """一个 UoW：每来源一个 E + 一份材料 + 报告；形状错则整笔回滚并判失败。"""

        try:
            with self._uow:
                stored = self._explorations.get(exploration_id)
                ledger = self._evidence.get(stored.project_id)
                sources = tuple(
                    self._record_source(stored.project_id, ledger, hit) for hit in hits
                )
                findings, dropped = _map_findings(outcome, sources=sources)
                if dropped:
                    notes.append(f"{dropped} 条结论因引用不存在的来源被丢掉")
                report = ResearchReport(
                    findings=findings,
                    sources=sources,
                    open_questions=tuple(outcome.open_questions),
                    notes=tuple(notes),
                )
                stored.complete_research(report)
                self._exploration_writer.save(stored)
                self._evidence_writer.save(ledger)
        except (PacketParseError, DomainError, MaterialTooLarge) as error:
            # 能抛的都在这儿收（`ShapeError` / `TooManyInquiryItems` /
            # `IllegalExplorationTransition` / 材料配额）：统一记一次失败，
            # 别让"这一趟已被放弃"以 409 漏出去、把行留在 RUNNING。
            # 整笔 UoW 已经回滚，所以没有"发了一半的 E"或"进了一半的材料"。
            self._fail(
                exploration_id,
                f"研究结果落不了库：{getattr(error, 'message', str(error))}",
            )
            return None
        publish_exploration(self._events, self._clock, stored)
        return sources

    def _record_source(
        self, project_id: ProjectId, ledger: EvidenceLedger, hit: ResearchHit
    ) -> ResearchSource:
        text = source_card_text(title=hit.title, url=hit.url, excerpt=hit.excerpt)
        eid = ledger.record(
            kind=EvidenceKind.EXTERNAL,
            text=text,
            source=EvidenceSource(external_ref=hit.url or NO_SOURCE_REF),
            now=self._clock.now(),
        )
        if self._materials.count_for_project(project_id) >= MAX_MATERIALS_PER_PROJECT:
            raise MaterialTooLarge(
                f"这个项目的材料已经到上限（{MAX_MATERIALS_PER_PROJECT} 份），"
                "研究来源不再入库（研究来源冻结、删不掉，只能换项目或调上限）",
                project_id=str(project_id),
            )
        packet: MaterialPacket = self._materials.put(
            project_id=project_id,
            parsed=hit.parsed,
            sha256=research_material_key(hit.url, hit.content),
            content=hit.content,
            now=self._clock.now(),
        )
        return ResearchSource(
            eid=eid,
            material_id=packet.material_id,
            title=hit.title,
            url=hit.url,
            excerpt=hit.excerpt,
        )

    # —— 失败 ——

    def _fail(self, exploration_id: ExplorationId, reason: str) -> ExplorationView:
        """记失败：**独立一个 UoW**（前面的写已经回滚，这里只写状态）。"""

        with self._uow:
            stored = self._explorations.get(exploration_id)
            stored.fail(reason)
            self._exploration_writer.save(stored)
        publish_exploration(self._events, self._clock, stored)
        return exploration_view(stored)


def _map_findings(
    packet: schemas.ResearchPacket, *, sources: tuple[ResearchSource, ...]
) -> tuple[tuple[ResearchFinding, ...], int]:
    """`S` 号 → 账本编号。返回 (结论, 被丢掉的条数)。

    丢弃只发生在"引用对不上号"这一种情况（严格档已经在综合那一步拦过）；
    丢掉要计数，由调用方写进 `notes`——**不许静默少几条结论**。
    """

    by_index = {index: source for index, source in enumerate(sources, start=1)}
    findings: list[ResearchFinding] = []
    dropped = 0
    for position, draft in enumerate(packet.findings, start=1):
        indexes = resolve_source_refs(draft.refs, count=len(sources), lenient=True)
        refs = tuple(by_index[index].eid for index in indexes)
        if not refs:
            dropped += 1
            continue
        findings.append(
            ResearchFinding(
                finding_id=f"F{position}", statement=draft.text, refs=refs
            )
        )
    return tuple(findings), dropped
