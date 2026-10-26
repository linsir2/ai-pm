"""一个用例一个入口。

两条铁律：**模型调用一律在事务外**（`uow` 里不许 `await`，由守卫强制）；
**用例不写领域规则**（规则在聚合与领域服务里，用例只负责编排与事务边界）。
"""

import asyncio
import hashlib
import logging
import math
import secrets
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.discusser import Discusser, SayOutcome
from pmstudio.application.agents.inquirer import Inquirer
from pmstudio.application.agents.integrator import IntegrationOutcome, Integrator
from pmstudio.application.agents.memory_curator import CuratorOutcome, MemoryCurator
from pmstudio.application.agents.persona_simulator import PersonaSimulatorAgent
from pmstudio.application.agents.schemas import (
    Discarded,
    DispositionsPacket,
    IntegrationPacket,
    Overflow,
    WebRequestDraft,
)
from pmstudio.application.agents.seat import SalvagedRef, SeatReviewer
from pmstudio.application.agents.sim_inquirer import (
    SimInquiryAgent,
    SimInquiryOutcome,
    filled_local_id,
)
from pmstudio.application.assembly import (
    NEW_FEATURE_YES,
    Assembly,
    assemble,
    backbone_gap_lines,
    cited_eids,
    contradiction_pairs,
    deferred_from_open_items,
    degraded_question_proposals,
    downgraded_question_proposals,
    draft_of,
    landed_answer_proposals,
    landed_claim_proposals,
    normalize,
    open_item_keys,
    open_item_proposal,
    open_question_proposals,
    question_key,
    questionnaire_of,
    seat_conflict_eid_pairs,
    seat_conflict_pairs,
    seat_questions,
    unaccounted_drops,
    unlanded_open_questions,
)
from pmstudio.application.context import (
    DIGEST_LIMIT,
    FETCH_ORIGIN,
    MATERIAL_BUDGET,
    NO_MATERIAL_TEXT,
    SECTION_OVERHEAD,
    answer_eids,
    budget_drop_line,
    build_project_context,
    build_round_digest,
    extract_observations_from_reviews,
    material_block_cost,
    material_header,
    missing_line,
    render_answers,
    render_asked_questions,
    render_citations,
    render_claims,
    render_contradictions,
    render_digests,
    render_document_state,
    render_document_topics,
    render_existing_entries,
    render_inspirations_index,
    render_materials,
    render_memory,
    render_memory_state,
    render_observations_index,
    render_prior_opinions,
    render_project_context,
    render_reviews,
    render_round_digest,
    render_seat_conflicts,
    render_selected_opinions,
    render_web_results,
    seat_entry_handle,
    select_chunks,
)
from pmstudio.application.context_pool import (
    PINNED_BUDGET_SHARE,
    build_stage_query,
    split_material_budget,
)
from pmstudio.application.deliverable import (
    Deliverable,
    iter_document_entries,
    render_markdown,
)
from pmstudio.application.discussion import (
    discussion_material,
    render_findings,
    render_thread_history,
    validate_ledger_refs,
)
from pmstudio.application.document_window import (
    DocumentWindow,
    document_window,
    visible_eids,
)
from pmstudio.application.dto import (
    SEARCH_LIMIT,
    DiffView,
    EvidenceLookupView,
    ExplorationView,
    MaterialChunk,
    MaterialHit,
    MaterialPacket,
    MaterialSummaryView,
    ParsedMaterial,
    ProjectSummaryView,
    RollbackResult,
    RoundCallsView,
    RoundHeadView,
    RoundView,
    SchemaView,
    SearchResultsView,
    TemplateView,
    ThreadView,
    VersionListView,
    WorkspaceView,
    answer_text,
    diff_view,
    evidence_lookup_view,
    exploration_view,
    material_summary_view,
    proposal_ignore_streak,
    round_view,
    schema_view,
    search_hits,
    template_view,
    thread_view,
    used_by_entries,
    version_summary_view,
    workspace_view,
)
from pmstudio.application.errors import (
    BuiltinTemplateProtected,
    MaterialFrozen,
    MaterialTooLarge,
    RoundBusy,
    RoundNotAttachable,
    SeatAlreadyReviewed,
    UnknownEntryId,
    UnknownEvidenceId,
    UnknownExploration,
    UnknownMaterial,
    UnknownRole,
    UnknownWebRequest,
    UnsupportedMaterial,
    WebExecutionLimitReached,
    WebMaterialNotAttachable,
    WebRequestNotPending,
)
from pmstudio.application.events import (
    DOC_CHANGED,
    EXPLORATION_UPDATED,
    GENERATION_FAILED,
    PACKET_EMITTED,
    ROUND_UPDATED,
)
from pmstudio.application.exploration import (
    candidate_material,
    document_has_content,
    entry_focus,
    material_sha,
    scope_line,
    validate_inquiry,
)
from pmstudio.application.lease import (
    LEASE_TTL_SECONDS,
    LeaseScope,
    StartedExploration,
    acquire_exploration_lease,
    clear_lease_scope,
    current_lease_scope,
    set_lease_scope,
    wall_clock_ms,
)
from pmstudio.application.material_refs import external_ref as _external_ref
from pmstudio.application.material_refs import material_kind as _material_kind
from pmstudio.application.material_synthesis import (
    material_sha as namespaced_material_sha,
)
from pmstudio.application.narrowing import narrow_seats
from pmstudio.application.ports import (
    Clock,
    ExplorationCanceller,
    IdGenerator,
    LeaseStore,
    LoginGuard,
    LoginVerdict,
    MaterialParser,
    MaterialRetriever,
    MaterialStore,
    MaterialVectorIndex,
    ProjectFactory,
    RoundCallLog,
    RoundCanceller,
    SeatCatalog,
    SeatDefinition,
    SessionStore,
    TemplateRepository,
    UsageStore,
    UserRecord,
    UserStore,
    WebFetcher,
    WebSearchGateway,
    WorkspaceIndex,
)
from pmstudio.application.repair import repair_note
from pmstudio.application.round_context import RoundContextPreparer
from pmstudio.application.sim_inquiry import (
    duplicate_of,
    narrow_offense,
    normalize_statement,
    synthesize_sim_material,
)
from pmstudio.application.sim_review import (
    MAX_SIM_REVIEW_ATTEMPTS,
    MAX_SIM_REVIEW_FEATURES,
    MAX_SIM_REVIEW_PERSONAS,
    MAX_SIM_REVIEWS_PER_DRAFT,
    document_anchors,
    evaluate_rules,
    review_issues,
    review_source_ref,
)
from pmstudio.application.web_research import (
    WEB_ORIGIN,
    WEB_SEARCH_KIND,
    WebResearch,
    tool_limit,
    web_execution_limit,
    web_search_tool,
)
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.authoring.schema import REQUIRED_FEATURE_FIELDS
from pmstudio.domain.authoring.views import DocumentView
from pmstudio.domain.discussion.card import Card, CardState
from pmstudio.domain.discussion.thread import (
    MAX_DISCUSSION_TURNS,
    MAX_PANEL_ROUNDS,
    Discussion,
    FindingRef,
    Participant,
    QuestionSnapshot,
    RoundSummary,
    ThreadStatus,
    Turn,
)
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.item import BlindSpot, CandidateState, Hypothesis
from pmstudio.domain.memory.inspiration import InspirationItem, InspirationOrigin
from pmstudio.domain.memory.memory import MEMORY_USAGE_WINDOW, screen_changes, unused_entries
from pmstudio.domain.null_repos import (
    NO_DIGESTS,
    NO_INSPIRATIONS,
    NO_OBSERVATIONS,
    NO_VECTORS,
)
from pmstudio.domain.ports import (
    DigestRepository,
    DiscussionReader,
    DiscussionWriter,
    DocumentReader,
    EvidenceReader,
    EvidenceWriter,
    ExplorationReader,
    ExplorationWriter,
    InspirationRepository,
    MemoryReader,
    MemoryWriter,
    ObservationRepository,
    RoundReader,
    RoundWriter,
    UnitOfWork,
)
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import SeatReview
from pmstudio.domain.round.questionnaire import (
    MAX_OPEN_ITEMS_PER_BATCH,
    MAX_QUESTIONNAIRES,
    MAX_QUESTIONS_PER_BATCH,
    Answer,
    AnswerSet,
    ListItem,
    Question,
    QuestionKind,
)
from pmstudio.domain.round.retrieval import (
    ManualChunk,
    RetrievalHit,
    RetrievalStage,
    RetrievalTrace,
)
from pmstudio.domain.round.round import (
    DroppedItem,
    DropSource,
    MaterialRef,
    MemoryProposal,
    Round,
    SeedRef,
)
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.round.web import (
    MAX_WEB_QUERIES,
    WebOrigin,
    WebRequest,
    WebRequestState,
    normalized_query,
)
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.services.committer import (
    CommitResult,
    ProposalCommitter,
    to_write_instruction,
)
from pmstudio.domain.shared.errors import (
    DomainError,
    IllegalStepTransition,
    NotAwaitingAnswers,
    ShapeError,
    ThreadClosed,
    ThreadFull,
    TooManySimInquiries,
    TooManySimReviews,
    WaitingOnHuman,
)
from pmstudio.domain.shared.ids import (
    PREFERENCE_AREA_ID,
    DiscussionId,
    DraftId,
    EntryId,
    EvidenceId,
    ExplorationId,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
    UserId,
)
from pmstudio.domain.shared.label import ClaimLabel, Zone
from pmstudio.domain.shared.lease import LeaseKind, LeaseToken
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import STANDARD_TEMPLATE_ID, template_from_dict
from pmstudio.domain.shared.template_adapter import TemplateAdapter, adapt
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.harness.errors import (
    ApplicationError,
    ModelCallFailed,
    PacketParseError,
    RateLimited,
)
from pmstudio.harness.events import Event, EventPublisher
from pmstudio.harness.model.envelope import Envelope
from pmstudio.harness.tools.recorder import ToolCallRecorder
from pmstudio.harness.tools.registry import ToolCatalog, ToolDefinition


class _SimpleIdGen:
    """简单的 ID 发生器，用于观察 ID 生成（不需要持久化唯一性）。"""

    def __init__(self) -> None:
        self._seq: dict[str, int] = {}

    def new(self, prefix: str) -> str:
        self._seq[prefix] = self._seq.get(prefix, 0) + 1
        return f"{prefix}-{self._seq[prefix]}-{uuid.uuid4().hex[:6]}"


@dataclass(frozen=True, slots=True)
class ClaimEdit:
    """开场卡上的一处纠正：只改标签与去处（内容归文档，卡片不改内容）。"""

    claim_id: str
    label: str
    target: Target


@dataclass(frozen=True, slots=True)
class AnswerInput:
    question_id: str
    text: str = ""
    selected: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Picked:
    """`_pick_materials` 交出来的东西：块 + 材料包 + 次序。

    三个字段都是调用方**已经在用**的：`picked` 去发号，`packets` 查 `kind`/`external_ref`，
    `order` 定 `position`（同分/同序时的先后）。两段式之后，**`packets` 与 `order` 都含
    自动检索进来的材料**——`create()` 会拿它们直接下标，只交手勾的那份就会 `KeyError`。
    """

    picked: tuple[tuple[str, MaterialChunk], ...]
    packets: dict[str, MaterialPacket]
    order: tuple[str, ...]
    #: 这一趟检索的原始命中（用于 SEED trace；失败时为空）
    hits: tuple[MaterialHit, ...] = ()
    #: 检索失败的原因（None = 成功；空命中不算失败）
    error: str | None = None
    #: 这一轮**真的调用过**检索器吗？没调用（未注入/预算已满）≠ 零命中。
    attempted: bool = False


class StartRound:
    """建轮 → Analyzer 切分定性归位。

    分两步不是洁癖：模型调用必须能在"轮已经存在"之后失败，
    这样重试（`RetryRound`）才能从 started 或 claims_ready 接着跑。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        uow: UnitOfWork,
        analyzer: ClaimAnalyzer,
        assigner: EvidenceAssigner,
        checker: ReadinessChecker,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
        materials: MaterialStore,
        explorations: ExplorationReader,
        discussions: DiscussionReader,
        memory: MemoryReader | None = None,
        retriever: MaterialRetriever | None = None,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._documents = documents
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._uow = uow
        self._analyzer = analyzer
        self._assigner = assigner
        self._checker = checker
        self._clock = clock
        self._ids = ids
        self._events = events
        self._materials = materials
        self._explorations = explorations
        self._discussions = discussions
        self._memory = memory
        # 库级检索是**增强项**：不给就只用手勾的材料（与改前逐字一致）。
        self._retriever = retriever
        # 生产路径：INSERT 与 acquire 必须在同一个 UoW（见 `_acquire_lease`）。
        # 直接调用例的测试可以不注入（内存仓储 / 老夹具），此时不建租约。
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    async def create(
        self,
        *,
        project_id: ProjectId,
        user_input: str,
        material_ids: Sequence[str] = (),
        seed_ids: Sequence[str] = (),
    ) -> RoundId:
        # 项目不存在就地失败（`DocumentReader.get` 抛）；门删掉之后这里不再读模板，
        # 但这道存在性闸得留着——后面的轮次要挂在这个项目上。
        self._documents.get(project_id)
        # 材料在**事务外**读（只读查询），选块是纯函数——所以下面那个事务里只有写入。
        selection = self._pick_materials(
            material_ids, query=user_input, project_id=project_id
        )
        picked, packets, order = selection.picked, selection.packets, selection.order
        seed_hits, seed_error = selection.hits, selection.error
        # 种子同样是事务外的只读校验：必须存在、同项目、跑完了、且被收下为「下一轮输入」
        seeds = self._resolve_seeds(project_id, seed_ids)
        with self._uow:
            self._documents.get(project_id)
            round_id = RoundId(self._ids.new("r"))
            round_ = Round.start(
                round_id=round_id,
                project_id=project_id,
                user_input=user_input,
                now=self._clock.now(),
                seeds=seeds,
            )
            # pin 是投影的输入：手勾的形状不写进轮次，后面的每步投影就没有"手勾优先"。
            round_.pin_materials(material_ids)
            if picked:
                # 逐块记 `external` 出处：**只有带进这一轮的块才有编号**（口径见 02 §11.0.28）
                #
                # 编号按**文档顺序**发（用户勾选顺序 × 块序，见下面的 `sorted`）——
                # 编号是这一轮的身份，不随相关度变；材料卡按行读，号码顺序走。
                # 提示词里材料节也按文档序渲染（U 型已撤回：`context.ushape` 不再存在，
                # 见 02 §11.0.54 的撤回注）。`select_chunks` 只决定"挑哪些块进预算"，
                # 不决定怎么摆——所以呈现、存储、编号现在是同一个序。
                ledger = self._evidence.get(project_id)
                # `order` 是**手勾序 + 自动检索序**：自动进来的材料也要有位次，
                # 否则下面 `position[item[0]]` 直接 KeyError（v2 的崩点）。
                position = {
                    material_id: index for index, material_id in enumerate(order)
                }
                eid_of = {
                    (material_id, chunk.chunk_id): ledger.record(
                        kind=_material_kind(packets[material_id]),
                        text=chunk.text,
                        source=EvidenceSource(
                            external_ref=_external_ref(packets[material_id], chunk)
                        ),
                        now=self._clock.now(),
                    )
                    for material_id, chunk in sorted(
                        picked, key=lambda item: (position[item[0]], item[1].ordinal)
                    )
                }
                round_.add_materials(
                    [
                        MaterialRef(
                            material_id=material_id,
                            chunk_id=chunk.chunk_id,
                            eid=eid_of[(material_id, chunk.chunk_id)],
                        )
                        for material_id, chunk in picked
                    ]
                )
                self._evidence_writer.save(ledger)
            # SEED trace：命中什么、选中什么、失败没有。**没查就不写**（未注入/预算已满
            # 不是"零命中"）；真的查过才写，零命中也写（恢复时不重查）。
            score_by_chunk = {hit.chunk_id: hit.score for hit in seed_hits}
            if selection.attempted:
                round_.note_retrieval(
                    RetrievalTrace(
                        stage=RetrievalStage.SEED,
                        pass_index=0,
                        query=user_input,
                        hits=tuple(
                            RetrievalHit(
                                material_id=hit.material_id,
                                chunk_id=hit.chunk_id,
                                score=hit.score,
                            )
                            for hit in seed_hits
                        ),
                        selected=tuple(
                            RetrievalHit(
                                material_id=material_id,
                                chunk_id=chunk.chunk_id,
                                score=score_by_chunk.get(chunk.chunk_id, 0.0),
                            )
                            for material_id, chunk in picked
                        ),
                        ok=seed_error is None,
                        error=seed_error,
                        created_at=self._clock.now(),
                    )
                )
            # **预算装不下的材料要说出来**（2026-10-21 批 11）：用户勾了 N 份材料，
            # 预算只带得下 K 份 → 剩下的**整份缺席**。不记这一笔，提示词与界面上
            # 都看不出"材料被预算挤掉了"，模型会以为自己看到的就是全部
            # （这正是缺陷 ② 的用户体感：勾了材料、提示词说没材料）。
            #
            # **只数手勾的**：自动检索进来的材料是"加料"，不是"勾了没带进来"——
            # 把它算进 missed 会让界面说"你勾的材料被挤掉了"，而用户根本没勾它。
            selected_ids = {material_id for material_id, _ in picked}
            handpicked = list(dict.fromkeys(material_ids))
            missed = [
                packets[material_id]
                for material_id in handpicked
                if material_id not in selected_ids
            ]
            if missed:
                kept = len(set(handpicked) & selected_ids)
                round_.note_dropped(
                    [
                        DroppedItem(
                            handle=f"材料 {len(handpicked)} 份",
                            reason=(
                                f"开局只带得下 {kept} 份；"
                                f"还有 {len(missed)} 份材料仍在材料池里，"
                                "后续步骤可能补进来"
                            ),
                            source=DropSource.MATERIAL,
                            seat_id=None,
                            pass_index=1,
                        )
                    ]
                )
            # 自动检索进来的材料**单独说一句**：用户没勾它们，但这一轮确实用了——
            # 不记的话模型的上下文里会凭空多出几份"没勾过的材料"，人看不出从哪来。
            auto_ids = [
                material_id
                for material_id in order
                if material_id not in set(handpicked) and material_id in selected_ids
            ]
            if auto_ids:
                round_.note_dropped(
                    [
                        DroppedItem(
                            handle=f"自动检索 {len(auto_ids)} 份",
                            reason=(
                                "这一轮还从材料库里按本轮输入检索并收敛了"
                                f" {len(auto_ids)} 份（不是你勾的，块已带进来）"
                            ),
                            source=DropSource.MATERIAL_AUTO,
                            seat_id=None,
                            pass_index=1,
                        )
                    ]
                )
            self._round_writer.save(round_)
            # 同一 UoW：INSERT 与租约一起提交。acquire 失败就整笔回滚——
            # 不允许"库里已经有这一轮、但没人持有它"的窗口。
            self._acquire_lease(round_id)
        _publish(self._events, self._clock, round_)
        return round_id

    def _acquire_lease(self, round_id: RoundId) -> None:
        if self._leases is None:
            return
        scope = current_lease_scope()
        if scope is None or scope.owner is None or not scope.holder:
            raise ApplicationError(
                "建轮时缺少租约上下文：owner 或持有者没设",
                code="missing_owner_scope",
            )
        token = self._leases.acquire(
            LeaseKind.ROUND,
            str(round_id),
            scope.owner,
            scope.holder,
            wall_clock_ms(),
            self._lease_ttl_ms,
        )
        if token is None:
            raise RoundBusy(round_id)
        scope.bind(token)

    def _pick_materials(
        self,
        material_ids: Sequence[str],
        *,
        query: str,
        project_id: ProjectId,
    ) -> _Picked:
        """两段式：手勾材料**保底优先**，再把库级检索的结果**按分数收敛**进剩余预算。

        第一段与改前一致（`select_chunks` 的第一步保底），只是成本尺子修好了（F0）。
        第二段是本轮重设计的 stage 2：库级检索**宽召回**（`SEARCH_LIMIT`），**只保留有分的
        块**、按分数降序填预算，**不做保底**——否则候选一宽，预算就被摊成十几份材料、
        每份只摊到一两块（v4 实测 gold 材料 13.45 块 → 1.54 块，正确率 0.737 → 0.485）。

        `query` 是本轮输入：预算只有 4000 字，粗排把预算从"排最前面的"挪到"提到本轮用词的"
        （`02 §11.0.53`）。用户勾选的顺序仍然作数——它决定同分时的先后。

        `section_overhead` **只在第一段预留**：那句收尾整节只渲染一次，两段都预留就是
        记两次账，反而把可用预算压掉一倍。
        """

        handpicked = list(dict.fromkeys(material_ids))
        packets: dict[str, MaterialPacket] = {}
        for material_id in handpicked:
            packet = self._materials.get(material_id)
            if packet is None:
                raise ShapeError(f"找不到这份材料：{material_id}", material_id=material_id)
            if packet.project_id != str(project_id):
                # 串项目带料 = 读别人的东西：与删/重解析同一口径，按"资源不在"404，
                # 不按"形状错"422（`test_delete_material.py` 的跨项目用例即此语义）。
                raise UnknownMaterial(material_id)
            if packet.parsed.origin == WEB_ORIGIN:
                # 联网结果走 `render_web_results`（一条结果一个号）；手勾进来会
                # 逐块发号、只渲染一个号——"编号 == 进提示词的块"那条红线。
                raise WebMaterialNotAttachable(material_id=material_id)
            packets[material_id] = packet
        # 自动段**先查**：`split_material_budget` 要知道有没有装得进余量的自动候选，
        # 才能决定手勾是硬上限 60% 还是可用满整个预算。检索器没注入 = 没查。
        hits: tuple[MaterialHit, ...] = ()
        failure: str | None = None
        attempted = False
        auto: dict[str, MaterialPacket] = {}
        if self._retriever is not None:
            attempted = True
            try:
                hits = self._retriever.search(
                    project_id=project_id, query=query, limit=SEARCH_LIMIT
                )
                # 观测（P0.4）：建轮自动检索与顶栏搜索共用检索器，必须能分开。
                logger.info(
                    "material_search caller=round stage=seed project=%s query=%r"
                    " limit=%d hits=%d",
                    project_id,
                    query,
                    SEARCH_LIMIT,
                    len(hits),
                )
            except Exception as error:
                # 检索是**增强项**：它挂了不该让这一轮起不来——回退到"只有勾的材料"，
                # 也就是改前的行为。**但要说一声**：静默降级会让"检索器永久坏了"和
                # "这轮确实没搜到"完全无法区分（评审 B）。
                logger.warning(
                    "material_search caller=round stage=seed project=%s ok=False"
                    "（材料检索失败，本轮只用勾的材料）",
                    project_id,
                    exc_info=True,
                )
                hits = ()
                failure = f"{type(error).__name__}: {error}"
            else:
                # 同一份材料可能在命中里占两槽（`_MATERIAL_SLOTS=2`）→ 按材料去重。
                # **先过滤再算成本**：web/手勾/跨项目/已删除的命中不是自动候选，
                # 不许触发 40% reserve（评审 C4）。
                for material_id in dict.fromkeys(hit.material_id for hit in hits):
                    if material_id in packets:
                        continue
                    packet = self._materials.get(material_id)
                    if packet is None or packet.project_id != str(project_id):
                        continue
                    if packet.parsed.origin == WEB_ORIGIN:
                        # **联网结果走另一条渲染路**：一条结果一个号、预算 `WEB_BUDGET`、
                        # 由 `render_web_results` 出正文。混进材料段会按块发号、按材料预算算，
                        # 结果就是"有号但提示词里一个字没有"——"编号 == 带进提示词的块"那条红线。
                        continue
                    auto[material_id] = packet
        auto_hits = [item for item in hits if item.material_id in auto]
        auto_costs: list[int] = []
        for item in auto_hits:
            packet = auto[item.material_id]
            chunk = next(
                (one for one in packet.parsed.chunks if one.chunk_id == item.chunk_id),
                None,
            )
            if chunk is None:
                continue
            auto_costs.append(
                material_block_cost(packet, chunk, first_of_material=True)
            )
        split = split_material_budget(
            usable=MATERIAL_BUDGET - SECTION_OVERHEAD,
            pinned_share=PINNED_BUDGET_SHARE,
            auto_costs=auto_costs,
        )
        picked, used = select_chunks(
            [packets[item] for item in handpicked],
            query=query,
            budget=split.pinned_cap,
            section_overhead=0,
        )
        # **地板**：每份手勾材料至少要有机会露一块，但**只在 `pinned_cap` 内**。
        # 单块就可能比 60% 预算还大（实测 3000 字的块 > 2400）——只有上限没有地板，
        # 会出现"一份手勾都进不来"；但地板越过 cap 就会把自动段饿死（A2 前的回归）。
        selected_floor = {material_id for material_id, _ in picked}
        floor_packets = [
            packets[item] for item in handpicked if item not in selected_floor
        ]
        if floor_packets:
            floor_picked, floor_used = select_chunks(
                floor_packets,
                query=query,
                budget=max(0, split.pinned_cap - used),
                section_overhead=0,
            )
            picked = picked + floor_picked
            used += floor_used
        order = list(handpicked)
        remaining = split.usable - used
        if auto:
            # `scores` 以 `chunk_id` 为键——前提是它**全局唯一**（生产由 `_restamp` 保证：
            # `{material_id}#cNN`）。只留自动材料的分：手勾的那些已经进过第一段了。
            scores = {
                item.chunk_id: item.score for item in auto_hits
            }
            more, _ = select_chunks(
                list(auto.values()),
                scores=scores,
                budget=remaining,
                section_overhead=0,
            )
            picked = picked + more
            packets.update(auto)
            order += list(auto)
        return _Picked(
            picked=picked,
            packets=packets,
            order=tuple(order),
            hits=tuple(hits),
            error=failure,
            attempted=attempted,
        )

    def _resolve_seeds(
        self, project_id: ProjectId, seed_ids: Sequence[str]
    ) -> tuple[SeedRef, ...]:
        """解析 `"origin:id#条目id"`（如 `exploration:x-1#H1` / `discussion:d-1#C1`）。

        每条都必须存在、同项目、且被收下为「下一轮输入」——缺一条就拒（人话给出哪条、为什么）。
        """

        refs: list[SeedRef] = []
        seen: set[tuple[str, str, str]] = set()
        for raw in seed_ids:
            origin, sep, rest = raw.partition(":")
            ref_id, hash_sep, item_id = rest.partition("#")
            if (
                not sep
                or not hash_sep
                or not origin
                or not ref_id
                or not item_id
            ):
                raise ShapeError(
                    "种子格式必须是 来源:id#条目id（如 exploration:x-1#H1 /"
                    f" discussion:d-1#C1）：{raw}",
                    seed=raw,
                )
            key = (origin, ref_id, item_id)
            if key in seen:
                continue
            seen.add(key)
            if origin == "exploration":
                exploration = self._explorations.get(ExplorationId(ref_id))
                if str(exploration.project_id) != str(project_id):
                    raise ShapeError("种子不属于这个项目", seed=raw)
                if exploration.status is not ExplorationStatus.DONE:
                    raise ShapeError("这趟探索还没跑完，不能用它的产物", seed=raw)
                candidate = next(
                    (
                        item
                        for item in exploration.candidates()
                        if item.candidate_id == item_id
                    ),
                    None,
                )
                if (
                    candidate is None
                    or candidate.state is not CandidateState.KEPT
                    or candidate.kept_as != "seed"
                ):
                    raise ShapeError("这条候选没有被收下为「下一轮输入」", seed=raw)
            elif origin == "discussion":
                thread = self._discussions.get(DiscussionId(ref_id))
                if str(thread.project_id) != str(project_id):
                    raise ShapeError("种子不属于这个项目", seed=raw)
                card = next(
                    (item for item in thread.cards if item.card_id == item_id), None
                )
                if (
                    card is None
                    or card.state is not CardState.KEPT
                    or card.kept_as != "seed"
                ):
                    raise ShapeError("这张卡没有被收下为「下一轮输入」", seed=raw)
            else:
                raise ShapeError(f"不认识的种子来源：{origin}", seed=raw)
            refs.append(SeedRef(origin=origin, ref_id=ref_id, item_id=item_id))
        return tuple(refs)

    async def analyze(self, *, round_id: RoundId) -> None:
        # 读取不开事务：模型调用不能包在事务里，也没必要读到同一快照。
        # 重装期间这一轮没有任何写入，所以文档现状与用户输入读一次就够。
        round_ = self._rounds.get(round_id)
        document = self._documents.get(round_.project_id)
        user_input = round_.user_input
        view = document.view()
        # 分析者拿**条目摘要**而不是全文（`07 §7.12` 第 2 项·claims 层）。量出来的依据：
        # `ClaimDraft` 不含任何文档条目引用，矛盾判据是轮内的，而全文对它唯一的实际用途
        # （web_queries 阶梯第一层"文档里已有的就别提"）在 18 轮真跑里只发生过 1 次；
        # 它每轮都要的"文档现状概貌"已由下面的 `$project_context` 给全。摘要保留十三章骨架
        # 与每条目一句、不给 id 与编号（引不了，给了只会诱导去引）。
        document_state = render_document_topics(
            view, readiness=self._checker.check(view, adapter=adapt(document.template))
        )
        memory_entries = self._memory.get(round_.project_id).entries if self._memory else ()
        project_context = render_project_context(
            build_project_context(view, memory_entries, adapter=adapt(document.template))
        )
        # 上一次失败留下的报错就是这次第一次尝试的起点（同整合那条路，P13 C1/C2）。
        # **没有 hint 就不开事务**：寻常路径的事务数被既有用例钉着（不许为只读开事务）。
        retry_hint = round_.retry_hint
        if retry_hint is not None:
            with self._uow:
                stored = self._rounds.get(round_id)
                stored.take_retry_hint()
                self._round_writer.save(stored)
        repair = retry_hint or ""
        for attempt in range(1, MAX_CLAIMS_ATTEMPTS + 1):
            try:
                analysis = await self._analyzer.analyze(
                    adapter=adapt(document.template),
                    user_input=user_input,
                    document_state=document_state,
                    external_material=external_material_text(round_, store=self._materials),
                    project_context=project_context,
                    round_id=round_id,
                    validation_error=repair,
                    packet_slot=f"a{attempt}",
                    # 本轮挑中（进提示词）的材料块编号：主张只能引这些，
                    # 引没见过的等于编（`analyzer.md` 硬规矩第 5 条）。
                    material_eids=frozenset(ref.eid for ref in round_.materials),
                )
                break
            except PacketParseError as error:
                if attempt == MAX_CLAIMS_ATTEMPTS:
                    # 两次都过不了：判这一轮失败。claims 是一轮的地基——没有主张就没有编号
                    # 与去处，硬编一份出来比失败更糟（GB1）。理由里写清重装过，便于复盘。
                    reason = f"主张两次都过不了校验：{error.message}"
                    fail_round(
                        rounds=self._rounds,
                        round_writer=self._round_writer,
                        uow=self._uow,
                        events=self._events,
                        clock=self._clock,
                        round_id=round_id,
                        reason=reason,
                        # 报文类失败才留提示：重试时它变成第一次尝试的 validation_error
                        hint=repair_note(error),
                    )
                    raise PacketParseError(reason, purpose="claims") from error
                # 重装要带上"上一次错在哪"——只给要求，不复制模型写错的那串
                repair = repair_note(error)
            except ApplicationError as error:
                # 网关那类（超时 / 供应商 5xx）：网关自己已经重试 3 次，重装帮不上忙
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=error.message,
                    # 但"模型没返回 JSON / 空回复"这两类它**自己修得了**（网关带了 hint）
                    hint=getattr(error, "hint", None),
                )
                raise
        else:
            raise AssertionError("不可能走到这里")
        with self._uow:
            round_ = self._rounds.get(round_id)
            document = self._documents.get(round_.project_id)
            ledger = self._evidence.get(round_.project_id)
            assigned = self._assigner.assign_claims(
                analysis.claims, ledger=ledger, round_id=round_id, now=self._clock.now()
            )
            round_.attach_claims(assigned)
            # 互斥对子在 attach_claims 之后挂（它要核对主张确实存在）：判断只做一次，
            # 做在读用户原话的那一步；装配层随后给覆盖两侧的题目盖章（T8）。
            round_.note_analysis_basis(analysis.basis)
            round_.attach_contradictions(analysis.contradictions)
            # 席位名单跟着主张一起落：它是这一次判断的产物，重试/刷新都该看得见
            round_.nominate(analysis.seats)
            # 联网申请也是这次判断的产物：**只提名**，批不批由用户定（P3a）。
            # 旧 `web_queries` 由领域层只读派生，这里只写新键。
            web_requests, web_request_drops = _web_requests_for(
                round_,
                requests=[(item.query, item.why) for item in analysis.web_queries],
                origin="analyzer",
                seat_id=None,
                pass_index=None,
                now=self._clock.now(),
            )
            round_.attach_web_requests(web_requests)
            # 被丢掉的提名（目录里没有的席位 / 超名额的检索词）留痕：
            # 丢人不丢轮，但要说清丢了谁
            round_.note_dropped(analysis.dropped + web_request_drops)
            authorize_named_features(round_, assigned, document=document)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


#: 一轮最多手动带进几块 / 一次最多几块（登记在 `02 §11.0.90`）。
MAX_MANUAL_CHUNKS = 10
MAX_MANUAL_CHUNKS_PER_CALL = 3

#: 应用层只有这一处会"吞"异常（库级检索是增强项），所以要有日志：
#: 否则"检索器永久坏了"与"这一轮确实没搜到"分不开（评审 B）。
logger = logging.getLogger(__name__)



#: 非席位来源（analyzer / integrator / 工具）没有"第几趟席位轮"；`DroppedItem` 要求
#: `pass_index >= 1`，所以 TOOL 留痕写 1 只作哨兵。消费方在 `seat_id is None` 时
#: 不要拿它当"第 1 趟席位轮"分组（按 `packet_id` / origin 归组）。
_NO_SEAT_PASS_INDEX = 1


def _tool_drop(handle: str, reason: str) -> DroppedItem:
    """工具这一趟的留痕：与席位意见无关，所以 `seat_id` 恒为 None（闭环区单列一句）。"""

    return DroppedItem(
        handle=handle,
        reason=reason,
        source=DropSource.TOOL,
        seat_id=None,
        pass_index=_NO_SEAT_PASS_INDEX,
    )


def _web_requests_for(
    round_: Round,
    *,
    requests: Sequence[tuple[str, str]],
    origin: WebOrigin,
    seat_id: str | None,
    pass_index: int | None,
    now: datetime,
    packet_id: str | None = None,
) -> tuple[tuple[WebRequest, ...], tuple[DroppedItem, ...]]:
    """把一方报文里的联网申请变成领域对象：**按方取前 3，余下 TOOL 留痕**。

    schema 不设 `max_length`（模型多写一条不该整包重装）；名额只在挂载前这一处算。
    id 从 `Round.next_web_request_id()` 起，`created_at` 用调用方的钟——
    领域对象不自己造编号或时间。
    """

    existing = sum(1 for item in round_.web_requests if item.origin == origin)
    remaining = max(0, MAX_WEB_QUERIES - existing)
    seen = {
        (item.origin, item.seat_id, normalized_query(item.query))
        for item in round_.web_requests
    }
    next_number = int(round_.next_web_request_id()[1:])
    kept: list[WebRequest] = []
    dropped: list[DroppedItem] = []
    for query, why in requests:
        text = query.strip()
        handle = f"联网申请 {text or '(空)'}"
        if len(kept) >= remaining:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason=(
                        f"每方最多 {MAX_WEB_QUERIES} 条联网申请，多出来的这一条已丢弃"
                    ),
                    source=DropSource.TOOL,
                    seat_id=seat_id,
                    pass_index=(
                        pass_index if pass_index is not None else _NO_SEAT_PASS_INDEX
                    ),
                    packet_id=packet_id,
                )
            )
            continue
        key = (origin, seat_id, normalized_query(text))
        if key in seen:
            continue
        try:
            request = WebRequest(
                request_id=f"w{next_number}",
                query=text,
                why=why,
                origin=origin,
                seat_id=seat_id,
                pass_index=pass_index,
                created_at=now,
            )
        except DomainError as error:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason=f"这条联网申请过不了领域校验：{error.message}",
                    source=DropSource.TOOL,
                    seat_id=seat_id,
                    pass_index=(
                        pass_index if pass_index is not None else _NO_SEAT_PASS_INDEX
                    ),
                    packet_id=packet_id,
                )
            )
            continue
        seen.add(key)
        kept.append(request)
        next_number += 1
    return tuple(kept), tuple(dropped)


def _checked_queries(
    queries: Sequence[str], *, tools: ToolCatalog, keys: frozenset[str]
) -> tuple[tuple[str, ...], ToolDefinition | None]:
    """用户勾中的检索词过两道闸门（去重保序 → 上限 → "这里到底有没有这个工具"）。

    返回 `(勾中的检索词, 可用的联网工具)`：一个都不勾就是 `((), None)`——没工具也不算错。
    """

    chosen = tuple(dict.fromkeys(item.strip() for item in queries if item.strip()))
    if not chosen:
        return (), None
    tool = web_search_tool(tools, keys=keys)
    if tool is None:
        raise ShapeError(
            "这一轮勾了联网检索，但服务端没有配好联网工具（没配密钥，或工具被停用）",
            tool_ids=[item.tool_id for item in tools.all()],
        )
    limit = tool_limit(tool, "max_queries")
    if len(chosen) > limit:
        raise ShapeError(
            f"这一轮勾了 {len(chosen)} 条检索词，最多只允许 {limit} 条",
            limit=limit,
            chosen=len(chosen),
        )
    return chosen, tool


class SubmitOpening:
    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        catalog: SeatCatalog,
        tools: ToolCatalog,
        available_tool_keys: frozenset[str],
        web_search: WebSearchGateway | None,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        materials: MaterialStore,
        index: WorkspaceIndex,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
        vectors: MaterialVectorIndex = NO_VECTORS,
        tool_recorder: ToolCallRecorder | None = None,
        web_research: WebResearch | None = None,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._catalog = catalog
        self._tools = tools
        self._available_tool_keys = available_tool_keys
        self._web_search = web_search
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._materials = materials
        # 工作区索引（只读）：判"这份材料被哪些编号引用过"（`P19` 的冻结判据）——
        # 被引用过的材料**不就地改写**（那些编号指着它里面的块）。
        self._index = index
        self._uow = uow
        self._clock = clock
        self._events = events
        # 向量索引（检索阶段 2）：联网结果**也要**有向量，否则"换个说法搜它"搜不到。
        self._vectors = vectors
        # 工具留样（可观测性 v2）：默认 None = 什么都不记（与模型留样同一条纪律）。
        self._tool_recorder = tool_recorder
        # 执行层与老字段共享同一组依赖；直接构造用例的测试不传也能工作。
        self._web_research = web_research or WebResearch(
            rounds=rounds,
            round_writer=round_writer,
            evidence=evidence,
            evidence_writer=evidence_writer,
            materials=materials,
            tools=tools,
            available_tool_keys=available_tool_keys,
            web_search=web_search,
            index=index,
            uow=uow,
            clock=clock,
            vectors=vectors,
            tool_recorder=tool_recorder,
        )

    async def __call__(
        self,
        *,
        round_id: RoundId,
        seats: Sequence[str],
        claim_edits: Sequence[ClaimEdit] = (),
        web_queries: Sequence[str] = (),
        allow_web: bool = False,
        approved_request_ids: Sequence[str] = (),
    ) -> None:
        # 校验全部在**联网之前**：请求不对（422）就不该留下半推半就的状态，
        # 更不该先花一次网络调用再报错。
        chosen, tool = _checked_queries(
            web_queries, tools=self._tools, keys=self._available_tool_keys
        )
        approved_ids = tuple(
            str(item).strip() for item in approved_request_ids if str(item).strip()
        )
        if approved_ids or allow_web:
            # 旧字符串路径只在勾了词时才查工具；新载体即使没勾词也要知道有没有工具。
            tool = web_search_tool(self._tools, keys=self._available_tool_keys)
        if (approved_ids or allow_web) and tool is None:
            raise ShapeError(
                "这一轮开了自动联网或批准了联网申请，但服务端没有配好联网工具"
                "（没配密钥，或工具被停用）",
                tool_ids=[item.tool_id for item in self._tools.all()],
            )
        if (chosen or approved_ids or allow_web) and self._web_search is None:
            # 目录说工具可用、组装点却没接上实现：这是装配错误，必须当场响，不能悄悄跳过
            raise ShapeError(
                "联网工具声明可用，但组装点没有接上实现（这一轮先别勾联网）",
                tool_id=WEB_SEARCH_KIND,
            )
        # 席位必须在注册表里：不然它会一路走到 `catalog.by_id` 才炸成 500。
        known = {seat.seat_id for seat in self._catalog.all()}
        unknown = [seat_id for seat_id in seats if seat_id not in known]
        if unknown:
            raise ShapeError(
                f"这些席位不在注册表里：{'、'.join(sorted(set(unknown)))}",
                seat_ids=sorted(set(unknown)),
            )
        # 授权与 claim 编辑先在一份**副本**上跑一遍：这一步不写库，只把
        # "这组请求会不会被拒"提前到联网之前。真正的写入仍在一个 UoW 里。
        round_before = self._rounds.get(round_id)
        preview = deepcopy(round_before)
        if claim_edits:
            preview.revise_claims(_apply_claim_edits(preview, claim_edits))
        preview.submit_opening(
            seats=tuple(seats),
            allow_web=allow_web,
            approved_request_ids=approved_ids,
            web_queries_chosen=chosen,
        )
        approved = tuple(
            item
            for item in preview.web_requests
            if item.state is WebRequestState.APPROVED
        )
        if approved:
            # 已批准申请可能先于本次 opening 存在（旧 payload 迁移 / decision），
            # 此时 `chosen` / `approved_ids` 都是空的——工具必须在这里补解析。
            if tool is None:
                tool = web_search_tool(self._tools, keys=self._available_tool_keys)
            if tool is None:
                raise ShapeError(
                    "这一轮有已批准的联网申请，但服务端没有配好联网工具"
                    "（没配密钥，或工具被停用）",
                    tool_ids=[item.tool_id for item in self._tools.all()],
                )
            if self._web_search is None:
                raise ShapeError(
                    "联网工具声明可用，但组装点没有接上实现（这一轮先别勾联网）",
                    tool_id=WEB_SEARCH_KIND,
                )
            # 每轮执行名额是硬上限；预检不通过就不联网。
            limit = web_execution_limit(
                self._tools, keys=self._available_tool_keys
            )
            if len(approved) > limit:
                raise ShapeError(
                    f"这一轮最多执行 {limit} 条联网申请，这次批准了 {len(approved)} 条",
                    limit=limit,
                    approved=len(approved),
                )
        # **先查后写**：工具调用必须在事务外；查完（或查失败）之后，一次事务把
        # 复述卡、申请状态、出处、材料引用一起落下去。网络中途崩溃仍停在复述卡上，
        # 不会留下 approved 让下一步重跑（`P3a` §0.1）。
        execution = await self._web_research.execute(round_id, approved)
        with self._uow:
            round_ = self._rounds.get(round_id)
            if claim_edits:
                round_.revise_claims(_apply_claim_edits(round_, claim_edits))
            round_.submit_opening(
                seats=tuple(seats),
                allow_web=allow_web,
                approved_request_ids=approved_ids,
                web_queries_chosen=chosen,
            )
            if execution is not None:
                ledger = self._evidence.get(round_.project_id)
                self._web_research.apply(round_, ledger, execution)
                self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)

    def _keep(self, project_id: ProjectId, draft: ParsedMaterial) -> MaterialPacket:
        """兼容委托：老测试直接调 `SubmitOpening._keep`。"""

        return self._web_research.keep(project_id, draft)


class DecideWebRequest:
    """逐条批准 / 拒绝联网申请。**批准即执行**（网络在 UoW 外）。

    写路径与其它动作同一条纪律：

    1. 读轮 → 404（申请不存在）/ 409（轮终态、已决策、名额满）；
    2. 一个 UoW 把 `proposed → approved` 落库（这就是 claim，单 worker 口径）；
    3. 联网（事务外）；工具级失败由 `WebResearch` 记成 `failed` + note + TOOL 留痕，
       非工具异常 / 取消由它先标失败、留痕，再原样抛出；
    4. 一个 UoW 写材料/E 号/`done|failed`/留痕 + `round.updated`。
       拒绝只走一个 UoW，**不联网**。

    **非工具异常 / 取消不补发 `round.updated`**（与 B3 执行点一致）：状态已在
    `WebResearch` 自己的 UoW 里落成 `failed`，客户端收到错误/取消后必须**主动
    refetch**（`GET /rounds/{id}`），不要等 SSE。工具级失败仍是正常返回，带事件。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        tools: ToolCatalog,
        available_tool_keys: frozenset[str],
        web_research: WebResearch,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._tools = tools
        self._available_tool_keys = available_tool_keys
        self._web_research = web_research
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(
        self, *, round_id: RoundId, request_id: str, decision: str
    ) -> None:
        if decision not in ("approve", "reject"):
            raise ShapeError(
                "联网申请只能批准或拒绝", decision=decision, request_id=request_id
            )
        round_ = self._rounds.get(round_id)
        current = next(
            (item for item in round_.web_requests if item.request_id == request_id),
            None,
        )
        if current is None:
            raise UnknownWebRequest(request_id, round_id=round_id)
        if round_.step in (Step.DONE, Step.FAILED, Step.ABANDONED):
            raise WebRequestNotPending(
                "这一轮已经结束，不能再决策联网申请",
                round_id=str(round_id),
                step=str(round_.step),
                request_id=request_id,
            )
        if current.state is not WebRequestState.PROPOSED:
            raise WebRequestNotPending(
                "这条联网申请已经决策过了",
                request_id=request_id,
                state=str(current.state),
            )
        if decision == "reject":
            with self._uow:
                target = self._rounds.get(round_id)
                target.decide_web_request(request_id, approve=False)
                self._round_writer.save(target)
            _publish(self._events, self._clock, target)
            return

        # 执行名额：`approved/done/failed` 占位，`proposed/rejected` 不占。
        # 网关没接上属于装配错——在 claim 之前抛，不能留下一条占名额的 approved。
        tool = web_search_tool(self._tools, keys=self._available_tool_keys)
        used = sum(
            1
            for item in round_.web_requests
            if item.state
            in (
                WebRequestState.APPROVED,
                WebRequestState.DONE,
                WebRequestState.FAILED,
            )
        )
        if tool is None:
            raise WebExecutionLimitReached(
                round_id=round_id,
                limit=0,
                used=used,
                tool_available=False,
            )
        limit = self._web_research.ensure_ready("执行联网申请")
        if used >= limit:
            raise WebExecutionLimitReached(
                round_id=round_id,
                limit=limit,
                used=used,
                tool_available=True,
            )

        # claim：先落 approved 再联网——这样即便网络中途崩掉，名额也已经被占住，
        # 下一次决策不会把同一条申请再执行一遍（B3 R14）。
        with self._uow:
            target = self._rounds.get(round_id)
            target.decide_web_request(request_id, approve=True)
            self._round_writer.save(target)
        approved = next(
            item for item in target.web_requests if item.request_id == request_id
        )
        execution = await self._web_research.execute(
            round_id, (approved,), _decision_pass_index(round_)
        )
        if execution is None:  # pragma: no cover —— 有 approved 就不会是 None
            return
        with self._uow:
            target = self._rounds.get(round_id)
            ledger = self._evidence.get(target.project_id)
            self._web_research.apply(target, ledger, execution)
            self._evidence_writer.save(ledger)
            self._round_writer.save(target)
        _publish(self._events, self._clock, target)


def _decision_pass_index(round_: Round) -> int:
    """决策这一趟的趟号（只用于 TOOL 留痕分组）：席位轮看席位趟，草案卡看刚完成的整合。"""

    if round_.step in (Step.SEATS_RUNNING, Step.AWAITING_ANSWERS):
        return max(1, round_.seat_round_count)
    if round_.step is Step.AWAITING_CONFIRM:
        return max(1, round_.integration_seq)
    return max(1, round_.seat_round_count, round_.integration_seq)


class RunSeatRound:
    """并行调各席位 → **每席位一个事务**（交回即落库）+ 汇合点幂等编号。

    模型调用各自独立（一个席位失败不影响别的席位被调用）；崩溃/关页面后重进只补
    `Round.pending_seats` 里缺的席位，已交回的不重跑。任一席位失败 → 整轮 failed，
    文档零写入（"失败必须是失败"），但已交回的意见留着。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        uow: UnitOfWork,
        reviewer: SeatReviewer,
        catalog: SeatCatalog,
        assigner: EvidenceAssigner,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
        materials: MaterialStore,
        memory: MemoryReader,
        observations_repo: ObservationRepository = NO_OBSERVATIONS,
        preparer: RoundContextPreparer | None = None,
        web_research: WebResearch | None = None,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._uow = uow
        self._reviewer = reviewer
        self._catalog = catalog
        self._assigner = assigner
        self._checker = checker
        self._clock = clock
        self._events = events
        self._materials = materials
        self._memory = memory
        self._observations_repo = observations_repo
        self._preparer = preparer
        self._web_research = web_research

    async def __call__(self, *, round_id: RoundId) -> None:
        # 执行点必须在**主读取之前**：新 E 号与本步 web_selection 要在 prepare
        # 时就能看见。这里只为 TOOL 留痕预读一次趟号，执行完会重新读整轮。
        if self._web_research is not None:
            pass_index = self._rounds.get(round_id).seat_round_count
            await self._web_research.execute_approved(round_id, pass_index)
        # 读取阶段不开事务：模型调用绝不能包在事务里。
        round_ = self._rounds.get(round_id)
        document = self._documents.get(round_.project_id)
        pending = tuple(self._catalog.by_id(seat_id) for seat_id in round_.pending_seats)
        view = document.view()
        readiness = self._checker.check(view, adapter=adapt(document.template))
        window = document_window(view, readiness=readiness)
        prepared = None
        if self._preparer is not None and pending:
            prepared = self._preparer.prepare(
                round_id=round_id,
                stage=RetrievalStage.SEAT,
                query=build_stage_query(
                    RetrievalStage.SEAT,
                    round_=round_,
                    seats=pending,
                    readiness=readiness,
                ),
            )
            # 投影刚写了材料与账本：必须重读，否则新 E 号会被下面的 `ledger_ids` 过滤掉。
            round_ = self._rounds.get(round_id)
        ledger = self._evidence.get(round_.project_id)
        document_state = window.text
        claims_text = render_claims(round_.claims)
        answered_text = render_answers(round_, ledger=ledger)
        # 席位把 `q3` 翻译成 E 编号时，只许翻**本轮**的答案（跨轮同号会把出处指错）
        answer_refs = answer_eids(ledger=ledger, round_id=round_id)
        # 历史意见**不再一次性渲染成共用品**：指针式要按席位分流（本席位逐字、其它席位
        # 一行点名），所以把原始 seat_reviews 传下去，由每个席位自己裁（`07 §7.12` 第 1 项）。
        # 顺带省掉一件蠢事：以前 N 个席位人手一份全量历史，其实各不相同也没意义。
        external_material = (
            prepared.text
            if prepared is not None
            else external_material_text(round_, store=self._materials)
        )
        # **不变量**（`02` §11.0.61）：`known` 必须等于这次提示词里**看得见**的编号，
        # 不能再是整个账本——账本里有而提示词里没有的（模型在编号空间上外推出来的幻觉，
        # 真跑 955 个席位引用里占 9 个）以前一律放行。
        # 四个带编号的槽位都要算进去：文档 / claims / answered / 本轮材料。
        ledger_ids = frozenset(evidence.eid for evidence in ledger.evidences())
        known_eids = window.known_eids(ledger) | (
            visible_eids(claims_text, answered_text, external_material) & ledger_ids
        )
        #: 这一趟是第几趟（问卷回来后会 +1 再跑）。信封槽位的一部分，见 `_review_one`。
        pass_index = round_.seat_round_count
        # L1 项目上下文：每轮都注入，让席位知道"我们在哪"
        project_context = render_project_context(
            build_project_context(
                view, self._memory.get(round_.project_id).entries, adapter=adapt(document.template)
            )
        )
        # L2 观察索引：上一轮席位的洞察，渐进式披露
        observations_index = self._build_observations_index(round_.project_id)

        results = await asyncio.gather(
            *(
                self._review_one(
                    round_id=round_id,
                    seat=seat,
                    document_state=document_state,
                    claims=claims_text,
                    opinions_so_far=round_.seat_reviews,
                    answered=answered_text,
                    ledger=ledger,
                    # 跨轮记忆（M6）：**已确认**的那些，与整合者看同一份渲染
                    project_memory=render_memory(
                        self._memory.get(round_.project_id).entries
                    ),
                    project_context=project_context,
                    observations_index=observations_index,
                    known_eids=known_eids,
                    answer_eids=answer_refs,
                    external_material=external_material,
                    pass_index=pass_index,
                )
                for seat in pending
            ),
            return_exceptions=True,
        )
        failures = [
            (seat, result)
            for seat, result in zip(pending, results, strict=True)
            if isinstance(result, BaseException)
        ]
        if failures and any(
            not isinstance(error, (PacketParseError, DomainError))
            for _, error in failures
        ):
            # 有网关类（超时 / 5xx 等）失败：那是 infra 瞬时错，不是模型交不出东西——
            # 照旧整轮失败可重试（resume 只补没交回的席位，代价已经最小）。下面一字不动。
            reason = "；".join(
                f"{seat.seat_id}：{getattr(error, 'message', str(error))}"
                for seat, error in failures
            )
            fail_round(
                rounds=self._rounds,
                round_writer=self._round_writer,
                uow=self._uow,
                events=self._events,
                clock=self._clock,
                round_id=round_id,
                reason=f"席位失败：{reason}",
            )
            first_error = failures[0][1]
            if isinstance(first_error, DomainError):
                # 领域校验不通过也要报成"模型输出不可用"（502），别漏成 409——
                # 这条是真模型冒烟实测出来的：某条意见缺依据时曾以 409 漏出去。
                raise PacketParseError(
                    f"席位失败：{reason}", purpose="seat_review"
                ) from first_error
            raise first_error
        if failures:
            # 全是内容错（两次都交不出可用报文）：记名跳过这些席位，整轮继续
            # （`02` §11.0.67）。网关错不在这里——上面已经整轮失败了。
            with self._uow:
                round_ = self._rounds.get(round_id)
                for seat, error in failures:
                    if seat.seat_id in round_.skipped_seats:
                        # 用户先跳过、在飞任务后失败：`skip_seat` 幂等无操作，
                        # 但下面的留痕是追加语义——不 guard 会多记一条谎言
                        # （"两次尝试都未能交回"，实际是用户跳的）。
                        continue
                    round_.skip_seat(seat.seat_id)
                    round_.note_dropped(
                        (
                            DroppedItem(
                                handle=seat.seat_id,
                                reason=(
                                    "两次尝试都未能交回可用报文，已跳过"
                                    f"（{getattr(error, 'message', str(error))}）"
                                ),
                                source=DropSource.SKIPPED,
                                seat_id=seat.seat_id,
                                pass_index=round_.seat_round_count,
                                packet_id=None,
                            ),
                        )
                    )
                self._round_writer.save(round_)
            round_ = self._rounds.get(round_id)
            if not round_.current_reviews:
                # 本趟一个都没交回：零疑问，照旧判死（`02` §7"一条疑问都没有时"口径）。
                reason = "；".join(
                    f"{seat.seat_id}：{getattr(error, 'message', str(error))}"
                    for seat, error in failures
                )
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=f"本轮所有席位都未能交回可用意见：{reason}",
                )
                first_error = failures[0][1]
                if isinstance(first_error, DomainError):
                    raise PacketParseError(
                        f"本轮所有席位都未能交回可用意见：{reason}",
                        purpose="seat_review",
                    ) from first_error
                raise first_error

        # 汇合点：席位齐了才编号（按席位顺序，且幂等——重进这里不会重复占号）
        round_ = self._rounds.get(round_id)
        if round_.pending_seats:
            _publish(self._events, self._clock, round_)
            return
        with self._uow:
            ledger = self._evidence.get(round_.project_id)
            self._assigner.assign_opinions(
                round_.current_reviews,
                ledger=ledger,
                round_id=round_id,
                now=self._clock.now(),
            )
            self._evidence_writer.save(ledger)
        _publish(self._events, self._clock, round_)
        # L2 观察提取：席位意见跨轮存活
        self._extract_and_store_observations(round_)

    def _build_observations_index(self, project_id) -> str:
        """读取最近观察，渲染为索引注入提示词。"""
        recent = self._observations_repo.recent(str(project_id))  # 全部去重后的：截断与报数在渲染层
        return render_observations_index(recent)

    def _extract_and_store_observations(self, round_) -> None:
        """从完成的席位轮提炼观察并存储。"""
        ids = _SimpleIdGen()
        observations = extract_observations_from_reviews(
            round_.current_reviews,
            str(round_.round_id),
            str(round_.project_id),
            ids,
            created_at=round_.created_at.isoformat(),
        )
        if observations:
            self._observations_repo.save_batch(observations)

    async def _review_one(
        self,
        *,
        round_id: RoundId,
        seat: SeatDefinition,
        document_state: str,
        claims: str,
        opinions_so_far: Sequence[SeatReview],
        answered: str,
        ledger: EvidenceLedger,
        project_memory: str,
        project_context: str = "",
        observations_index: str = "",
        known_eids: frozenset[EvidenceId],
        answer_eids: dict[str, str] | None = None,
        external_material: str = "",
        pass_index: int = 1,
    ) -> None:
        """一个席位：调模型（事务外）→ 交回就落库。`with uow` 里没有 await（守卫会拦）。

        `pass_index` 是"这一轮里的第几趟席位轮"（问卷回来后会再跑一趟）。它和 `attempt`
        一起构成信封的**槽位**：身份由协调者（这里）分配，因为 agent 只知道自己是谁产出的，
        不知道这是第几趟第几次——不传就会撞车（同一席位两趟拿到同一个 packet_id）。
        """

        # 指针式历史（`07 §7.12` 第 1 项）：本席位自己的逐字，其它席位一行点名。
        # 每个席位各裁一次——同一份历史对不同席位是不同的视图。
        previous = render_prior_opinions(
            opinions_so_far,
            ledger=ledger,
            round_id=round_id,
            seat_id=seat.seat_id,
        )
        # known 补齐（2026-10-14）：提示词里**看得见**的编号都得算数——本席位上一趟自己的
        # 「出处编号 E12」、项目记忆的「出处：E…」、观察索引的引用，以前只算文档 / claims /
        # 已答 / 材料四处，这些"看得见却判不存在"的合法引用会被误杀（§11.0.61 的完整面）。
        seat_known = _seat_known_eids(
            known_eids, previous, project_memory, observations_index, ledger=ledger
        )
        allowed_eids = _render_allowed_eids(seat_known)
        validation_error = ""
        for attempt in range(1, MAX_SEAT_ATTEMPTS + 1):
            try:
                outcome = await self._reviewer.review(
                    seat=seat,
                    document_state=document_state,
                    claims=claims,
                    opinions_so_far=previous,
                    answered=answered,
                    project_memory=project_memory,
                    project_context=project_context,
                    observations_index=observations_index,
                    external_material=external_material,
                    known_eids=seat_known,
                    answer_eids=answer_eids or {},
                    round_id=round_id,
                    validation_error=validation_error,
                    packet_slot=f"p{pass_index}a{attempt}",
                    allowed_eids=allowed_eids,
                    # 最后一次尝试：引用了不存在的编号不再判死整轮，摘掉该编号 + 留痕
                    # （`02` §11.0.2 口径；上面几次仍抛错，给它自纠的机会）。
                    lenient=attempt == MAX_SEAT_ATTEMPTS,
                )
            except PacketParseError as error:
                # 解析错也是**可修复的错**：只重跑这个席位（其余席位不动）。
                # 真跑 P7 验证实测：某个席位把 `evidence_refs` 写成 `null`，没重装时整轮判死。
                if attempt == MAX_SEAT_ATTEMPTS:
                    raise
                validation_error = repair_note(error)
                continue
            if outcome.discarded and attempt < MAX_SEAT_ATTEMPTS:
                # 容器里混进了**根本不是对象**的元素（真跑 F17：`opinions[1]` 是占位符字符串）。
                # 与超限同一个口径：先重装一次让它自己改；第二次仍错才丢掉那一条。
                first = outcome.discarded[0]
                validation_error = repair_note(
                    PacketParseError(
                        "模型返回的报文过不了 schema",
                        purpose="seat_review",
                        discarded=(first.container, first.index),
                    )
                )
                continue
            if outcome.overflow and attempt < MAX_SEAT_ATTEMPTS:
                # **先重装、再截断**（报告 F4）：超限是可修复的错，先让它自己精简一次。
                # 判定点放在用例层：重试策略归这里，席位 agent 只负责"报文 → 领域对象"。
                first = outcome.overflow[0]
                validation_error = repair_note(
                    PacketParseError(
                        "模型返回的报文过不了 schema",
                        purpose="seat_review",
                        overflow=(
                            first.container,
                            first.kept + first.dropped,
                            first.kept,
                        ),
                    )
                )
                continue
            break
        review = outcome.review
        with self._uow:
            round_ = self._rounds.get(round_id)
            if review.seat_id in round_.skipped_seats:
                # 用户跳过后迟交：整包丢弃，记一条（与跳过同一桶 USER_SKIPPED）。
                # 不 attest——不变式"跳过的席位零意见进整合"靠这一行构造性成立；
                # 不记截断/摘除细项——整包都没了，细项无意义；
                # 不发 PACKET_EMITTED——没交回的东西谎报"交回了"，前端会 show
                # 一个不存在的包。直接 return（成功形状，gather 只分异常/非异常）。
                round_.note_dropped(
                    (
                        DroppedItem(
                            handle=review.seat_id,
                            reason="跳过之后才交回，已丢弃",
                            source=DropSource.USER_SKIPPED,
                            seat_id=review.seat_id,
                            pass_index=round_.seat_round_count,
                            packet_id=outcome.envelope.packet_id,
                        ),
                    )
                )
                self._round_writer.save(round_)
                return
            round_.attest(review, packet_id=outcome.envelope.packet_id)
            # 席位提名的联网申请：只挂载、不执行（批不批由用户 / allow_web 决定）；
            # 名额在挂载前按方截断，余下 TOOL 留痕。
            web_requests, web_request_drops = _web_requests_for(
                round_,
                requests=[(item.query, item.why) for item in outcome.web_requests],
                origin="seat",
                seat_id=review.seat_id,
                pass_index=round_.seat_round_count,
                now=self._clock.now(),
                packet_id=outcome.envelope.packet_id,
            )
            round_.attach_web_requests(web_requests)
            round_.note_dropped(web_request_drops)
            # 被截断掉的意见要留痕（"一条都不许默默丢掉"）——它没进卡片，但要说清为什么。
            round_.note_dropped(
                _truncation_items(
                    review.seat_id,
                    outcome.overflow,
                    pass_index=round_.seat_round_count,
                    packet_id=outcome.envelope.packet_id,
                )
            )
            # 非对象元素被摘掉的那些条也要留痕（`02` §11.0.35）：一个占位符换一行留痕，
            # 而不是换一次整轮判死。
            round_.note_dropped(
                _discarded_items(
                    review.seat_id,
                    outcome.discarded,
                    pass_index=round_.seat_round_count,
                    packet_id=outcome.envelope.packet_id,
                )
            )
            # 最后兜底里摘掉的"不存在的编号"也要留痕（2026-10-14）：正文照收、依据摘了，
            # 但用户得知道这条意见原本引了个不存在的编号（不静默）。
            round_.note_dropped(
                _salvage_items(
                    review.seat_id,
                    outcome.salvaged,
                    pass_index=round_.seat_round_count,
                    packet_id=outcome.envelope.packet_id,
                )
            )
            self._round_writer.save(round_)
            pass_index = round_.seat_round_count
            project_id = round_.project_id
        # 逐席位发事件：只通知"这一包交回了"，opinions 真源在 GET /round。
        self._events.publish(
            Event(
                name=PACKET_EMITTED,
                project_id=str(project_id),
                round_id=str(round_id),
                payload={
                    "packet_id": outcome.envelope.packet_id,
                    "run_id": outcome.envelope.run_id,
                    "kind": outcome.envelope.kind,
                    "produced_by": outcome.envelope.produced_by,
                    "basis": outcome.envelope.basis,
                    "seat_id": review.seat_id,
                    "pass_index": pass_index,
                },
                occurred_at=self._clock.now(),
            )
        )


# —— 用例之间共用的小工具 ——


MAX_INTEGRATION_ATTEMPTS = 2

#: 归因补交代的额度（`02` §11.0.38）：报文没错、但模型没给席位意见交代去向时，
#: 用**小调用**问"这些里哪些是你没采用的"。与校验重装分开算——那是另一类问题
#: （改形状 vs 补去向），修法也不一样。
#:
#: 为什么是 2 而不是 1：P23 真跑里第一次点名（整包重写）之后模型**会补**
#: （点名 3/4/3 条，它补了 2/4/3），但重写出的报文又漏了别的，总量降不下来。
#: 现在的补交代**不重写**任何题与提案，所以第二次一般能收干净；
#: 另配一条"点数没降就不再花"的护栏（见 `_integrate_with_repair` 第二段）。
MAX_ACCOUNTING_REPAIRS = 2

#: claims 也允许"重装一次"：真模型是抖的（同一份输入换个采样就过），让用户手动点重试
#: 是把成本推给人。与整合者同一个口径——**必须带上一次错在哪**（`repair_note`），
#: 否则只是拿同一句提示词再撞一堵墙（`learnings.md` L13）。
MAX_CLAIMS_ATTEMPTS = 2

#: 记忆整理也允许"重装一次"（M6d）：真跑第一趟（L1 第 1 轮，`qwen-plus`）模型提了 4 条
#: 内容全对、**一条都没写出处**，于是被机械筛整批丢掉——那是"照同一句提示词再问一遍就能修"
#: 的形状错误，所以给它一次带错因的补问（与席位/整合者同一条规矩）。
#: 上限 2 次，且**只在整批被筛掉时**才补问：正常一轮仍然只有 1 次调用。
MAX_CURATOR_ATTEMPTS = 2

#: 单份材料上限（20MB）：在**读 body 之前**用 `file.size` 判一次，用例里再兜一次
MAX_MATERIAL_BYTES = 20 * 1024 * 1024
#: 一个项目最多几份材料（防止材料列表与检索被塞爆）
MAX_MATERIALS_PER_PROJECT = 50

#: 单个席位也允许"重装一次"：超限（意见太多）与别的报文错都是可修复的，
#: 而整轮重跑要付 4–6 个席位的代价（真跑实测：188 次席位调用里多出一倍是重跑）。
MAX_SEAT_ATTEMPTS = 2

@dataclass(frozen=True, slots=True)
class IntegrationResult:
    """整合这一趟的产物。两个 carried 分开报，别让调用方猜哪个是哪个。"""

    assembled: Assembly
    gaps: tuple[str, ...]
    #: 来自**轮内队列**的题（要按"真被问出去"的数量出队）
    carried_from_queue: tuple[Question, ...] = ()
    #: 来自**文档 s11 未决条目**的题（要记住来源：答完撤下那条条目）
    carried_from_open_items: tuple[Question, ...] = ()
    #: **最终**（含补交代合并后的）信封。降级路径没有可用报文，所以是 `None`。
    envelope: Envelope | None = None

    @property
    def web_requests(self) -> tuple[WebRequestDraft, ...]:
        """从**最终信封**派生的联网申请——不另存一份（避免第二真相源）。"""

        if self.envelope is None:
            return ()
        packet = self.envelope.packet
        return (
            tuple(packet.web_requests)
            if isinstance(packet, IntegrationPacket)
            else ()
        )

def _retire_answered_open_items(
    round_: Round, *, ledger: EvidenceLedger, start_index: int, adapter: TemplateAdapter
) -> tuple[Proposal, ...]:
    """这一轮**被回答过**、且来自 s11 未决条目的题 → 各出一条 `remove` 把未决项撤下。

    机械做，不问模型：真跑里模型连"没采用的席位意见"都漏填，这种"记账"的活不能指望它。
    没回答（跳过）的不撤——它还该留在 s11 里等下一轮。
    """

    # 来源键带 form_id：`carried_sources` 记的就是这一把（题号在一轮里唯一之后它仍然成立）
    answered = {
        f"{answer_set.form_id}#{answer.question_id}": answer.question_id
        for answer_set in round_.answer_sets
        for answer in answer_set.answers
    }
    # 编号按**本轮**认（老实现收整个项目账本，别的轮里同号的那条会把出处引走）
    mapping = answer_eids(ledger=ledger, round_id=round_.round_id)
    sources = round_.carried_sources()
    proposals: list[Proposal] = []
    index = start_index
    for key in sorted(answered):
        question_id = answered[key]
        entry_id = sources.get(key)
        eid = mapping.get(str(question_id))
        if entry_id is None or eid is None:
            continue
        proposals.append(
            Proposal(
                proposal_id=ProposalId(f"p{index:02d}"),
                entry_id=EntryId(entry_id),
                op=WriteOp.REMOVE,
                target=Target.body(adapter.open_home),
                text="本轮已回答，撤下这条未决项",
                origin=Origin(OriginType.SYSTEM_DEFAULT, (EvidenceId(eid),)),
                kind=BlockKind.QUESTION,
            )
        )
        index += 1
    return tuple(proposals)


def _cited_eids(
    proposals: Sequence[Proposal], questions: Sequence[Question]
) -> frozenset[EvidenceId]:
    """这一趟**用到的**出处编号（提案的依据 + 题目的依据）。

    "答案要么被引用、要么机械落地"（`D3`）里的"被引用"就看它：这条答案的编号出现在
    提案或题目的依据里，就算整合者交代过它的去处了。

    **`remove` 不算**（2026-09-29 P11 真跑抓到）：撤下未决条目只是记账，它不写任何内容；
    把它算成"引用了这条答案"，carried 题的答案就会被它自己那条 remove 引走、
    机械落地永远不触发，结论照样蒸发。
    """

    return frozenset(
        [
            ref
            for proposal in proposals
            if proposal.op is not WriteOp.REMOVE
            for ref in proposal.origin.refs
        ]
        + [ref for question in questions for ref in question.evidence_refs]
    )


def _open_item_defaults(
    round_: Round,
    questions: Sequence[Question],
    *,
    ledger: EvidenceLedger,
    now: datetime,
    reason: str,
) -> dict[QuestionId, EvidenceId]:
    """给"没排上的题"补 system_default 出处（编号在账本里，s11 提案才有东西可引用）。"""

    defaults: dict[QuestionId, EvidenceId] = {}
    for item in questions:
        defaults[item.question_id] = ledger.record(
            kind=EvidenceKind.SYSTEM_DEFAULT,
            text=f"{item.title}（{reason}，未决定）",
            source=EvidenceSource(round_id=round_.round_id, rationale=reason),
            now=now,
        )
    return defaults


def _truncation_items(
    seat_id: str,
    overflow: tuple[Overflow, ...],
    *,
    pass_index: int,
    packet_id: str | None = None,
) -> list[DroppedItem]:
    """席位交回超上限被截断的那几条：留痕说清"第几条起没进卡片"。"""

    return [
        DroppedItem(
            handle=f"{seat_id}:O{item.kept + 1}..O{item.kept + item.dropped}",
            reason=f"超出单席位上限 {item.kept} 条，第 {item.kept + 1} 条起未进卡片",
            source=DropSource.ASSEMBLY,
            seat_id=seat_id,
            pass_index=pass_index,
            packet_id=packet_id,
        )
        for item in overflow
    ]


def _salvage_items(
    seat_id: str,
    salvaged: tuple[SalvagedRef, ...],
    *,
    pass_index: int,
    packet_id: str | None = None,
) -> list[DroppedItem]:
    """降级时从某条意见上摘掉"不存在的编号"的留痕（2026-10-14）。

    正文照收、依据摘了——用户得看见"这条原本引了个不存在/看不见的编号"，
    否则它落到「它提议的」会像模型本来就没给依据（那是另一回事）。
    """

    return [
        DroppedItem(
            handle=f"{seat_id}:{item.local_id}",
            reason=(
                f"这条意见引用了不存在的编号 {'、'.join(item.dropped)}：编号已摘掉，"
                "正文照收（按无依据处理，草案里归到「它提议的」）"
            ),
            source=DropSource.ASSEMBLY,
            seat_id=seat_id,
            pass_index=pass_index,
            packet_id=packet_id,
        )
        for item in salvaged
    ]


def _seat_known_eids(
    known_eids: frozenset[EvidenceId],
    *texts: str,
    ledger: EvidenceLedger,
) -> frozenset[EvidenceId]:
    """席位这一趟**可引用**的编号 = 原来四处 + 另外三处"看得见"的（§11.0.61 完整面）。

    传进来的 `texts` 是本席位这一趟提示词里除文档/claims/已答/材料之外的带编号槽位
    （本席位上一趟的意见、项目记忆、观察索引）。判定同一条不变量的另一半：
    **凡是提示词里看得见的编号都算数**——`visible_eids(文本) ∩ 账本` 才是合法集合，
    光扫文本不看账本会把正文里随便一个 "E数字" 也收进来。
    """

    return known_eids | (visible_eids(*texts) & DocumentWindow.ledger_ids(ledger))


def _render_allowed_eids(known: frozenset[EvidenceId]) -> str:
    """给席位看的"可引编号清单"（提示词那一节正文）。

    空集也要明说——没有可引的编号时，判断要么写成 question、要么走 basis_note，
    别让它对着空清单硬编一个（这正是幻觉的来源）。
    """

    if not known:
        return "（这一轮没有任何可引用的编号：判断请写 question，或用 basis_note 说明是纯经验判断）"
    ordered = sorted(known, key=lambda eid: int(str(eid)[1:]))
    return "、".join(str(eid) for eid in ordered)


def _discarded_items(
    seat_id: str,
    discarded: tuple[Discarded, ...],
    *,
    pass_index: int,
    packet_id: str | None = None,
) -> list[DroppedItem]:
    """席位交回的**非对象元素**被摘掉的那几条：留痕带上位置（`02` §11.0.35）。

    它没有 `local_id`（那一"条"根本不是对象），所以 handle 用 `seat.x:opinions[2]`
    这种带位置的写法——用户查得到"是这一包里的第几条"，而模型写的那串占位符**不进留痕**
    （回喂原串等于给它一个可以照抄的模板，learnings L20）。
    """

    return [
        DroppedItem(
            handle=f"{seat_id}:{item.container}[{item.index}]",
            reason=item.reason,
            source=DropSource.ASSEMBLY,
            seat_id=seat_id,
            pass_index=pass_index,
            packet_id=packet_id,
        )
        for item in discarded
    ]


#: 整合报文的容器名 → 人话（留痕是给人看的，别甩 `proposals[2]` 这种机器写法）
_DISCARD_TITLES = {
    "questions": "题目",
    "list_items": "清单项",
    "proposals": "提案",
    "dropped_opinions": "未申报丢弃",
    "web_requests": "联网申请",
}


def _packet_discard_items(
    discarded: tuple[Discarded, ...],
    *,
    pass_index: int,
    packet_id: str | None = None,
) -> tuple[DroppedItem, ...]:
    """整合报文里被摘掉的**非对象元素**：丢那一条 + 留痕（P23）。

    与超限截断（`_overflow_items`）同一个出口、同一条纪律：一条都不许默默丢。
    它没有席位地址（不是某条意见），所以 handle 用 `提案 #2（不是对象）` 这种带容器名的写法。
    """

    return tuple(
        DroppedItem(
            handle=(
                f"{_DISCARD_TITLES.get(item.container, item.container)}"
                f" #{item.index}（不是对象）"
            ),
            reason=item.reason,
            source=DropSource.ASSEMBLY,
            seat_id=None,
            pass_index=pass_index,
            packet_id=packet_id,
        )
        for item in discarded
    )


def _overflow_items(
    overflow: tuple[Overflow, ...],
    *,
    pass_index: int,
    packet_id: str | None = None,
) -> tuple[DroppedItem, ...]:
    """整合这一趟被截断掉的那几条：留痕说清是"超出单轮上限"。"""

    return tuple(
        DroppedItem(
            handle=f"提案 #{item.kept + 1} 起（共 {item.dropped} 条）",
            reason=f"超出单轮上限 {item.kept} 条，被截断未进卡片（{item.container}）",
            source=DropSource.ASSEMBLY,
            seat_id=None,
            pass_index=pass_index,
            packet_id=packet_id,
        )
        for item in overflow
    )


def _render_authorized_features(round_: Round, *, document: Document) -> str:
    """给整合者看的"本轮已授权的功能"清单（报告 F1 的直接解药）。

    主张点名过的功能在切分阶段就已经授权（`authorize_named_features`），但提示词里原来只说
    "不许新建功能子树"——模型于是整轮都不产功能候选，s5 就空着。这里把已授权的点名列出来：
    **这些功能视为已存在，可以直接写它的功能字段**。
    """

    lines = [
        f"- {slug}（被 {eid} 点名，已授权）：直接写它的功能字段"
        for slug, eid in sorted(round_.authorized_feature_basis.items())
    ]
    existing = sorted(
        str(block.feature_slug)
        for block in document.features()
        if block.feature_slug is not None
    )
    if existing:
        lines.append("- 文档里已有的功能：" + "、".join(existing))
    return "\n".join(lines) or "（这一轮没有点名任何功能）"


def _render_open_items(carried: Sequence[Question]) -> str:
    """给整合者看的"前几轮留下的未决项"清单（带**条目地址**，它才撤得对）。

    也写上**去处**（P11）：让它在同一批里看得见"这个去处已经有老问题在等"，
    少做一份"把同一件事重新问一遍"的活。

    **地址要有名字，并说清它只填 `entry_id`**（M7 A′ / learnings L13 / M6f 对记忆 id
    的同一手法）：以前裸写成 `[r-… #open61]`，真跑里模型把它当"引用"抄进了标题 →
    正文里出现 `（open61/open67/open112）` 这种读者看不懂的内部地址。
    """

    lines = [
        f"- 条目地址 {item.from_entry} · 〔去处：{item.target.label()}〕{item.title}"
        f"（跳过后果：{item.consequence_if_skipped}）"
        for item in carried
        if item.from_entry is not None
    ]
    if not lines:
        return "（没有未决项）"
    lines.append(
        "上面每行的**条目地址**只用来出 `remove` 撤下那条未决项（填进 `entry_id`）——"
        "**不许写进任何标题或正文**；出处编号只有 `E…` 这一种。"
    )
    return "\n".join(lines)


def _render_list_items(round_: Round) -> str:
    """给整合者看的"这一轮前面几批记下的清单项"（F8：不许说完就扔）。

    限量 10 条：它是提示，不是账本——真账在 `Round.list_items` 与草案卡上。
    跨轮不保（`Round` 一结束就没了）：跨轮延续靠 s11 未决条，这条口径写在 `02` 设计文档里。
    """

    items = round_.list_items[:10]
    lines = [
        f"- 〔去处：{item.target.label()}〕{item.text}（为什么记下：{item.reason}）"
        for item in items
    ]
    omitted = len(round_.list_items) - len(items)
    if omitted > 0:
        lines.append(f"（共 {len(round_.list_items)} 条，只带了前 {len(items)} 条）")
    return "\n".join(lines) or "（这一轮前面几批没有记下清单项）"


def _unhandled_list_items(
    round_: Round,
    *,
    questions: Sequence[Question],
    proposals: Sequence[Proposal],
) -> tuple[ListItem, ...]:
    """这一轮记下的清单项里，**没人处理**的那些（A2）。

    判据是机械的：`(去处, 归一化文本)` 已经出现在本轮的题目或提案里 → 算处理过。
    宁可多记一条（保守），也不做相似度判断——"换句话说的同一件事"这里看不出来。
    """

    handled = {(question.target.label(), normalize(question.title)) for question in questions}
    handled |= {(proposal.target.label(), normalize(proposal.text)) for proposal in proposals}
    seen: set[tuple[str, str]] = set()
    unhandled: list[ListItem] = []
    for item in round_.list_items:
        key = (item.target.label(), normalize(item.text))
        if key in handled or key in seen:
            continue
        seen.add(key)
        unhandled.append(item)
    return tuple(unhandled)


def _next_proposal_index(proposals: Sequence[Proposal]) -> int:
    """续编提案编号：**现有最大编号 + 1**，不是 `len + 1`。

    装配层会把去处不合法 / 与既有内容重复的提案**丢掉**，编号因此出现空洞
    （p01、p03…）。真跑实测（P7 验证）：按条数续编会撞上后面那条的编号，
    `DuplicateProposalId` 把整轮打成 500（`p15` 重复）。
    """

    numbers = [
        int(text)
        for text in (str(item.proposal_id)[1:] for item in proposals)
        if text.isdigit()
    ]
    return max(numbers, default=0) + 1


def _stop_asking_reason(round_: Round) -> str | None:
    """该不该停止追问？两个闸门，谁先到算谁，理由写进证据与缺口行里。

    - **问卷已两批**（`04-未决.md` C6）：这是用户最先感受到的那条——别再没完没了地问；
    - **席位轮到上限**（`02` §7）：兜底，正常路径下问卷闸门会先响。
    """

    # 用户"继续深挖"过几次，额度就多几批（C6，P14）：闸门只放宽问卷，不放宽席位轮上限
    allowed = MAX_QUESTIONNAIRES + round_.extra_batches
    if len(round_.answer_sets) >= allowed:
        return f"问卷已满 {allowed} 批"
    # 席位轮闸门也认 dig 的额度：它必须与 `Round.can_begin_seat_round` 看同一个数，
    # 否则用户 dig 之后会在整合这一步被立刻降级（真跑里踩到过）。
    if round_.seat_round_count >= round_.seat_round_limit:
        return "已到席位轮上限"
    return None


class IntegrateRound:
    """整合 → 干跑 →（失败就重装一次）→ 一个事务里出问卷或草案。"""

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        uow: UnitOfWork,
        integrator: Integrator,
        catalog: SeatCatalog,
        index: WorkspaceIndex,
        checker: ReadinessChecker,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
        materials: MaterialStore,
        curator: MemoryCurator,
        memory: MemoryReader,
        observations_repo: ObservationRepository = NO_OBSERVATIONS,
        digest_repo: DigestRepository = NO_DIGESTS,
        inspiration_repo: InspirationRepository = NO_INSPIRATIONS,
        preparer: RoundContextPreparer | None = None,
        web_research: WebResearch | None = None,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._uow = uow
        self._integrator = integrator
        self._catalog = catalog
        self._index = index
        self._checker = checker
        self._clock = clock
        self._ids = ids
        self._events = events
        self._materials = materials
        self._curator = curator
        self._memory = memory
        self._observations_repo = observations_repo
        self._digest_repo = digest_repo
        self._inspiration_repo = inspiration_repo
        self._preparer = preparer
        self._web_research = web_research

    async def __call__(self, *, round_id: RoundId) -> None:
        # 早于 `_integrate_with_repair` 的第一次读 round / `retry_hint`：
        # 已批准 + allow_web 自动批准的申请都该在本步 prepare 前执行完。
        if self._web_research is not None:
            # 整合这一趟的 pass 是 `integration_seq + 1`（成功后 +1，见 round_context）。
            pass_index = self._rounds.get(round_id).integration_seq + 1
            await self._web_research.execute_approved(round_id, pass_index)
        result = await self._integrate_with_repair(round_id=round_id)
        assembled, gaps = result.assembled, result.gaps
        # 记忆整理（M6）：**出草案那一刻**跑一次小调用。它必须在这一行——
        # `with self._uow` 之前，因为"模型调用一律在事务外"（架构守卫钉着）。
        memory_proposals, memory_notes = await self._curate(round_id=round_id, result=result)

        with self._uow:
            round_ = self._rounds.get(round_id)
            ledger = self._evidence.get(round_.project_id)
            document = self._documents.get(round_.project_id)
            adapter = adapt(document.template)
            now = self._clock.now()
            # 整合者提名的联网申请：**最终 UoW 才挂载**，本轮不执行（P3a）。
            # 请求从最终信封派生（补交代合并后），不另存一份。
            web_requests, web_request_drops = _web_requests_for(
                round_,
                requests=[(item.query, item.why) for item in result.web_requests],
                origin="integrator",
                seat_id=None,
                pass_index=None,
                now=now,
                packet_id=None if result.envelope is None else result.envelope.packet_id,
            )
            round_.attach_web_requests(web_requests)
            round_.note_dropped(web_request_drops)
            # 只有**真被问出去**的那几条才从轮内队列出队（carried 里还混着 s11 来的题）
            asked = {question_key(item) for item in assembled.questions}
            round_.take_deferred(
                sum(
                    1
                    for item in result.carried_from_queue
                    if question_key(item) in asked
                )
            )
            round_.defer(assembled.deferred)
            proposals = list(assembled.proposals)
            degraded = 0
            if assembled.degrade_reason is not None:
                degraded_proposals = degraded_question_proposals(
                    round_.current_reviews,
                    adapter=adapter,
                    ledger=ledger,
                    round_id=round_id,
                    now=now,
                    start_index=_next_proposal_index(proposals),
                    reason=assembled.degrade_reason,
                )
                degraded = len(degraded_proposals)
                proposals += list(degraded_proposals)
            system_defaults = _system_defaults(round_, ledger=ledger, now=now)
            # s11 里已经活跃的条目身份：下面三处落地都拿它判重（P13 C6）
            known_open_items = open_item_keys(document)
            proposals += list(
                open_question_proposals(
                    round_,
                    adapter=adapter,
                    system_defaults=system_defaults,
                    start_index=_next_proposal_index(proposals),
                    known=known_open_items,
                )
            )
            questions = assembled.questions
            gaps = list(gaps)
            # 跳过的席位要在这里点名（`02` §11.0.67）：`skipped_seats` 是轮级的，
            # 后续每趟整合都带——问卷路没有 gaps 通道所以只落在草案卡，闭环区留痕折叠行两边都有。
            for seat_id in round_.skipped_seats:
                gaps.append(
                    f"席位{self._catalog.by_id(seat_id).title}（{seat_id}）"
                    "未能交回可用意见，已跳过；本轮缺少该席位的视角"
                )
            if assembled.degrade_reason is not None:
                gaps.append(
                    f"{assembled.degrade_reason}：{degraded} 条席位疑问未归并，"
                    "已原样落到 s11（未改一个字）"
                )
            stop_reason = _stop_asking_reason(round_)
            if questions and stop_reason is not None:
                # 不再追问：题目降级成 s11 开放问题，直接出草案（02 §7 / 04-未决 C6）
                # 已经在 s11 里的那几题不再落第二遍（P13 C6）；信息没丢，所以不另记缺口行
                landable = unlanded_open_questions(questions, known=known_open_items)
                for question in landable:
                    system_defaults[question.question_id] = ledger.record(
                        kind=EvidenceKind.SYSTEM_DEFAULT,
                        text=f"{question.title}（{stop_reason}，未决定）",
                        source=EvidenceSource(
                            round_id=round_id,
                            rationale=f"{stop_reason}，不再追问，转为开放性记录",
                        ),
                        now=now,
                    )
                proposals += list(
                    downgraded_question_proposals(
                        landable,
                        adapter=adapter,
                        system_defaults=system_defaults,
                        start_index=_next_proposal_index(proposals),
                        round_id=round_id,
                        reason=stop_reason,
                    )
                )
                gaps += [
                    f"未决（{stop_reason}，不再追问）：{item.title}" for item in questions
                ]
                questions = ()
            if not questions:
                # 出草案这一支：没问上的题落 s11；但队列**本身留着**（P14 C6）——
                # 用户点"继续深挖"就回到席位轮，届时这些题会被 carried 真的问出来。
                # 不清空的代价：正常确认的轮里队列还剩几条，轮已 done，没人再读它。
                leftover = list(round_.deferred)
                if leftover:
                    # 已经在 s11 里的那几题不再落第二遍（P13 C6）；缺口行照旧列全（它们仍没定）
                    landable_leftover = unlanded_open_questions(
                        leftover, known=known_open_items
                    )
                    defaults = _open_item_defaults(
                        round_,
                        landable_leftover,
                        ledger=ledger,
                        now=now,
                        reason="本批没排上",
                    )
                    proposals += list(
                        downgraded_question_proposals(
                            landable_leftover,
                            adapter=adapter,
                            system_defaults=defaults,
                            start_index=_next_proposal_index(proposals),
                            round_id=round_id,
                            reason="本批没排上",
                        )
                    )
                    gaps += [f"未决（本批没排上）：{item.title}" for item in leftover]
                # 被回答过的未决项：机械出 remove 把它撤下（不指望模型自觉，`02` §11.0.10）
                proposals += list(
                    _retire_answered_open_items(
                        round_,
                        ledger=ledger,
                        start_index=_next_proposal_index(proposals),
                        adapter=adapter,
                    )
                )
                # 答案要么被引用、要么机械落地（D3）：跨轮去重只认文档，答案不落地下一轮必然再问
                landed = landed_answer_proposals(
                    round_,
                    ledger=ledger,
                    document=document,
                    cited=_cited_eids(proposals, assembled.questions),
                    start_index=_next_proposal_index(proposals),
                )
                proposals += list(landed.proposals)
                gaps += list(landed.notes)
                # 主张要么被引用、要么机械落地（D3 向 claims 延伸，`02` §11.0.71）：
                # claims 与 answers 的编号空间不交（不同账本项），谁先谁后结果一样。
                landed_claims = landed_claim_proposals(
                    round_,
                    document=document,
                    cited=_cited_eids(proposals, assembled.questions),
                    start_index=_next_proposal_index(proposals),
                )
                proposals += list(landed_claims.proposals)
                gaps += list(landed_claims.notes)
                # **留痕**（2026-10-21，批 15，`04-未决.md` C1）：这条输入没被整合者
                # 引用、是被系统机械落地的 → 记进 `Round.dropped`，闭环区按来源分组显示，
                # 用户看得见哪条是"系统替它落地的"。
                if landed_claims.dropped:
                    round_.note_dropped(landed_claims.dropped)
                # 清单项的去向（A2）：没人处理的那些落 s11——非阻塞、带去处，
                # 下一轮的 carried 才问得回原章节，不会"轮一结束就没了"。
                unhandled = _unhandled_list_items(
                    round_, questions=(), proposals=proposals
                )
                # 已经在 s11 里的那条不再落第二遍；剩下这些仍然没定，缺口行照说
                landable_items = tuple(
                    item
                    for item in unhandled
                    if (item.target.label(), normalize(item.text)) not in known_open_items
                )
                already_known = len(unhandled) - len(landable_items)
                if already_known:
                    gaps.append(
                        f"有 {already_known} 条清单项已经在 s11 里，不重复落："
                        + "；".join(
                            item.text
                            for item in unhandled
                            if (item.target.label(), normalize(item.text))
                            in known_open_items
                        )[:3]
                    )
                if landable_items:
                    start = _next_proposal_index(proposals)
                    for offset, item in enumerate(landable_items):
                        eid = ledger.record(
                            kind=EvidenceKind.SYSTEM_DEFAULT,
                            text=f"{item.text}（清单项，本轮没处理）",
                            source=EvidenceSource(
                                round_id=round_id,
                                rationale="清单项没被处理，落 s11 继续排队",
                            ),
                            now=now,
                        )
                        proposals.append(
                            open_item_proposal(
                                adapter=adapter,
                                title=item.text,
                                consequence="先搁着，不影响本轮交付",
                                blocking=False,  # C4：清单项不堵交付
                                target_label=item.target.label(),
                                eid=eid,
                                index=start + offset,
                                round_id=round_id,
                                rationale="清单项没被处理，落 s11 继续排队",
                            )
                        )
                    gaps.append(
                        f"还有 {len(landable_items)} 条清单项没被处理，已记进 s11（不阻塞交付）："
                        + "；".join(item.text for item in landable_items[:3])
                    )
            dropped = _dropped_items_for(
                round_,
                assembled=assembled,
                proposals=proposals,
                # **含本批没排上的那些**（F19 的漏算）：它们已经被组装出来了，只是没进这一批
                # 的 5 个名额。它们引用的判断当然算"交代过"——少算这一截，那条判断会被误报
                # "没人交代"，而它的内容其实已经在题目里了。
                produced_questions=assembled.questions + assembled.deferred,
                ledger=ledger,
            )
            form_id = FormId(self._ids.new("f"))
            # 这一批记下的清单项要进轮次（两条路都记）：问卷一批换一批，
            # 挂在问卷上的那份下一批就看不见了（F8）。
            round_.remember_list_items(assembled.list_items)
            questionnaire = questionnaire_of(
                round_id, form_id, questions, assembled.list_items
            )
            if questionnaire is not None:
                # 记住这一批问回来的 s11 未决条目（键带 form_id：question_id 每批都从 q1 重排）
                round_.remember_carried(form_id, questionnaire.questions)
                round_.note_unreferenced_materials(
                    _unreferenced_materials_for(
                        round_,
                        proposals=proposals,
                        questions=assembled.questions + assembled.deferred,
                    )
                )
                round_.integrate(questionnaire=questionnaire, dropped=dropped)
            else:
                # C4：出草案那一刻算一次"连续几轮没采用席位提议"（只读投影 + 纯函数）
                round_.remember_proposal_ignore_streak(
                    proposal_ignore_streak(self._index.recent_rounds(round_.project_id))
                )
                # 这一轮攒下的记忆提议（草案卡上那一档）：**提议**，默认不勾
                round_.remember_memory_proposals(memory_proposals)
                gaps = gaps + list(memory_notes)
                draft = draft_of(round_id, DraftId(self._ids.new("d")), proposals, gaps)
                if draft is None:
                    raise ShapeError("整合既没有给出题目也没有给出提案")
                round_.note_unreferenced_materials(
                    _unreferenced_materials_for(
                        round_,
                        proposals=proposals,
                        questions=assembled.questions + assembled.deferred,
                    )
                )
                round_.integrate(draft=draft, dropped=dropped)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)

    async def _curate(
        self, *, round_id: RoundId, result: IntegrationResult
    ) -> tuple[tuple[MemoryProposal, ...], tuple[str, ...]]:
        """出草案那一刻的**一次小调用**：这一轮有什么值得记进跨轮记忆（M6）。

        三条口径（`02` §11.0.44）：

        ① **只在这一轮真的出草案时跑**——出问卷的那几批不跑（记忆整理是"这一轮的收获"，
           一轮跑一次就够，别按批重复花钱）；
        ② 机械筛（`screen_changes`）在**模型之后、落库之前**：缺出处 / 点名不存在 /
           装不下 / 超两行的那些**丢 + 留痕**，绝不进卡片让用户勾；
        ③ **挂了不打崩这一轮**：草案照出，留一行（与工具层、P24 小调用同一条纪律）。

        留痕走**草案卡上的缺口行**（`Draft.gaps`），**不走 `Round.dropped`**——
        那条通道的语义被文档与用例钉成"席位意见的交代账"（`unaccounted` 指标的分子），
        把记忆的事混进去会污染那条指标。用户两边都看得见，指标只有一个意思。
        """

        round_ = self._rounds.get(round_id)
        if result.assembled.questions and _stop_asking_reason(round_) is None:
            return (), ()          # 这一批只出问卷：还没到"出草案那一刻"
        ledger = self._evidence.get(round_.project_id)
        memory = self._memory.get(round_.project_id)
        known_refs = frozenset(str(item.eid) for item in ledger.evidences())
        validation_error = ""
        screened = None
        outcome: CuratorOutcome | None = None
        for attempt in range(1, MAX_CURATOR_ATTEMPTS + 1):
            try:
                outcome = await self._curator.propose(
                    # 给整理者的那一份**带条目 id**（它要写 target_id），席位的**不带**（M6f）
                    memory_state=render_memory_state(memory.entries),
                    round_digest=render_round_digest(round_, ledger=ledger),
                    round_id=round_id,
                    validation_error=validation_error,
                    packet_slot=f"a{attempt}",
                )
            except (ModelCallFailed, PacketParseError, ShapeError) as failure:
                return (), (f"这一轮没能整理记忆（{failure}），草案照出",)
            screened = screen_changes(
                outcome.changes,
                memory=memory,
                known_refs=known_refs,
                # 机械回填的候选：**这一轮账本里的编号**（模型只决定记哪条，出处系统盖）
                fallback_refs=frozenset(
                    str(item.eid)
                    for item in ledger.evidences()
                    if item.source.round_id == str(round_id)
                ),
            )
            # 只有"整批被筛掉"才补问一次（正常一轮仍是 1 次调用）：
            # 真跑第一趟就是这种形状——4 条内容全对、一条都没写 refs。
            if screened.accepted or not outcome.changes or attempt == MAX_CURATOR_ATTEMPTS:
                break
            validation_error = "\n".join(
                f"- {item.reason}" for item in screened.dropped
            )
        proposals = tuple(
            MemoryProposal(proposal_id=f"mem#{index}", change=change)
            for index, change in enumerate(screened.accepted, start=1)
        )
        notes = [
            f"记忆 #{index} 没进卡片：{item.reason}"
            for index, item in enumerate(screened.dropped, start=1)
        ]
        notes += [
            f"记忆报文 {item.container} 超出上限：保留了前 {item.kept} 条，溢出 {item.dropped} 条"
            for item in outcome.overflow
        ]
        notes += [
            f"记忆报文 {item.container} 第 {item.index} 条没收：{item.reason}"
            for item in outcome.discarded
        ]
        return proposals, tuple(notes)

    def _assemble_and_gaps(
        self,
        *,
        round_: Round,
        document: Document,
        ledger: EvidenceLedger,
        outcome: IntegrationOutcome,
        carried: Sequence[Question],
    ) -> tuple[Assembly, tuple[str, ...]]:
        """装配 + 这一趟的留痕 + 草案卡缺口行。

        拿到报文时走一次；**每次把"没采用的申报"并回报文之后再走一次**。
        `assemble` 与 `dry_run` 都是纯函数（后者在深拷贝上跑），重复调用只花 CPU，
        不会多发一次模型请求。
        """

        assembled = assemble(
            outcome.envelope.packet,
            round_=round_,
            document=document,
            ledger=ledger,
            carried=carried,
            contradictions=contradiction_pairs(round_)
            | seat_conflict_eid_pairs(
                seat_conflict_pairs(round_.current_reviews),
                ledger=ledger,
                round_id=round_.round_id,
            ),
        )
        # 截断 / 形状错掉的那几条要留痕 + 在草案卡缺口行里说清（"不静默丢"）。
        assembled = replace(
            assembled,
            dropped=assembled.dropped
            + _packet_discard_items(
                outcome.discarded,
                pass_index=round_.seat_round_count,
                packet_id=outcome.envelope.packet_id,
            )
            + _overflow_items(
                outcome.overflow,
                pass_index=round_.seat_round_count,
                packet_id=outcome.envelope.packet_id,
            ),
        )
        gaps = dry_run(
            document, assembled.proposals, round_=round_, now=self._clock.now()
        )
        if outcome.overflow:
            count = sum(item.dropped for item in outcome.overflow)
            gaps = gaps + (
                f"本轮有 {count} 条内容因单轮上限未进卡片（可在下一轮继续提）",
            )
        if outcome.discarded:
            gaps = gaps + (
                f"本轮有 {len(outcome.discarded)} 条内容因为报文形状不对没进卡片"
                "（模型交回的那一条不是对象，可在下一轮继续提）",
            )
        # 骨架六章还空着就说一句（报告 F18）：走 `gaps` 这条既有通道，
        # 前端 `DraftDrawer` 已经在渲染它（"写入后仍然空着的必填项"），一个字都不用改。
        gaps = gaps + backbone_gap_lines(
            readiness=self._checker.check(document.view(), adapter=adapt(document.template)),
            proposals=assembled.proposals,
            adapter=adapt(document.template),
        )
        return assembled, gaps

    def _build_observations_index(self, project_id) -> str:
        """读取最近观察，渲染为索引注入提示词。"""
        recent = self._observations_repo.recent(str(project_id))  # 全部去重后的：截断与报数在渲染层
        return render_observations_index(recent)

    async def _integrate_with_repair(self, *, round_id: RoundId):  # type: ignore[no-untyped-def]
        """先拿到一份能过校验的整包，再在**便宜的那一层**补交代（`02` §11.0.38）。

        两段是刻意分开的：第一段的每次重试都是**整包调用**（改形状 / 改内容），
        第二段的每次补交代都是**小调用**（只问几条意见的去向，不重写题与提案）。
        混在一个循环里写过一版（P23），真跑证明"重写整包"会补一批又漏一批。
        """
        # 上一次失败留下的报错（如果有）就是这次第一次尝试的起点：重试不再"盲调"（P13 C1/C2）。
        # 取走即清——不清的话，陈年错误会被后面每个动作当成校验错误递进去。
        retry_hint = self._rounds.get(round_id).retry_hint
        if retry_hint is not None:
            with self._uow:
                stored = self._rounds.get(round_id)
                stored.take_retry_hint()
                self._round_writer.save(stored)
        validation_error = retry_hint or ""
        carried: tuple[Question, ...] = ()
        carried_from_queue: tuple[Question, ...] = ()
        carried_from_open_items: tuple[Question, ...] = ()
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        # 两本账分开算（`02` §11.0.37/§11.0.38）：
        #   `validate_left` —— 报文过不了校验时的重装（**整包**，原来的 2 次）；
        #   `accounting_left` —— 报文没错、但席位意见没交代去向时的补交代
        #     （**小调用**，另算；不然它会挤掉"再试一次改正报文"的机会）。
        validate_left = MAX_INTEGRATION_ATTEMPTS
        accounting_left = MAX_ACCOUNTING_REPAIRS
        first_pass = True
        # 整合前取一次"缺口证据"：整趟只查一次，报文重装不重查（同一 integration_seq）。
        prepared_text: str | None = None
        if self._preparer is not None:
            round_for_query = self._rounds.get(round_id)
            document_for_query = self._documents.get(round_for_query.project_id)
            adapter_for_query = adapt(document_for_query.template)
            readiness_for_query = self._checker.check(
                document_for_query.view(), adapter=adapter_for_query
            )
            prepared_text = self._preparer.prepare(
                round_id=round_id,
                stage=RetrievalStage.INTEGRATE,
                query=build_stage_query(
                    RetrievalStage.INTEGRATE,
                    round_=round_for_query,
                    readiness=readiness_for_query,
                ),
            ).text
        # —— 第一段：拿到一份能过校验的整包 ——
        while True:
            round_ = self._rounds.get(round_id)
            document = self._documents.get(round_.project_id)
            adapter = adapt(document.template)
            ledger = self._evidence.get(round_.project_id)
            # carried 只算一次：重装时模型看到的是同一份"要优先问的题"（队列在事务里才消费）
            if first_pass:
                # 跨轮队列 = 文档 s11 里最老的 N 条未决条目；轮内队列 = 本批没问上的题
                carried_from_queue = round_.peek_deferred(MAX_QUESTIONS_PER_BATCH)
                carried_from_open_items = deferred_from_open_items(
                    document,
                    ledger=ledger,
                    limit=MAX_OPEN_ITEMS_PER_BATCH,
                    skip_entries=frozenset(round_.carried_sources().values()),
                )
                carried = carried_from_open_items + carried_from_queue
                first_pass = False
            answered, _, skipped = render_answers(
                round_, ledger=self._evidence.get(round_.project_id)
            ).partition("--")
            # 问过但没答也没跳过的那批题**另给一段**：`render_answers` 只认 `answer_sets`，
            # 而"用户一道都没答就点确认"是正常主路径（`AnswerSet.__post_init__` 又规定
            # 交卷至少要有一条答案或显式跳过，所以没有答案集 = 这一批一道都没碰过）——
            # 那时题面一个字都不进提示词，模型下一趟就会把上一批的题逐字重写一遍
            # （真跑 10 轮 14 条留痕，14/14 逐字相同），撞装配层判重被丢掉。
            asked_questions = render_asked_questions(round_)
            view = document.view()
            try:
                authorized_features = _render_authorized_features(
                    round_, document=document
                )
                open_items = _render_open_items(carried_from_open_items)
                previous_list_items = _render_list_items(round_)
                contradictions = render_contradictions(round_)
                seat_conflicts = render_seat_conflicts(
                    seat_conflict_pairs(round_.current_reviews),
                    ledger=ledger,
                    round_id=round_id,
                )
                outcome = await self._integrator.integrate(
                    adapter=adapter,
                    document_state=render_document_state(
                        view, readiness=self._checker.check(view, adapter=adapt(document.template))
                    ),
                    claims=render_claims(round_.claims),
                    opinions=render_reviews(
                        round_.current_reviews,
                        titles=titles,
                        ledger=ledger,
                        round_id=round_id,
                    ),
            answered=answered,
            skipped=skipped,
            # 问过、没答也没跳过的那批（模型看不见它们，只能靠这一段）
            asked_questions=asked_questions,
            # 跨轮记忆（M6）：**已确认**的那些（席位与整合者看同一份渲染）
            project_memory=render_memory(self._memory.get(round_.project_id).entries),
            # L1 项目上下文：让整合者知道"我们在哪"
            project_context=render_project_context(
                build_project_context(
                    view,
                    self._memory.get(round_.project_id).entries,
                    adapter=adapt(document.template),
                )
            ),
            # L2 观察索引：上一轮席位的洞察
            observations_index=self._build_observations_index(round_.project_id),
            # L4 轮次摘要：之前轮次发生了什么
            # 全部的：截断、"共几轮"与超水位的"更早的待复核"都在渲染层（与 L2 同一口径）
            digests=render_digests(
                self._digest_repo.recent(round_.project_id),
                limit=DIGEST_LIMIT,
            ),
            # L3 灵感档案：讨论/探索/模拟中的发现（同上：不预截断）
            inspirations_index=render_inspirations_index(
                self._inspiration_repo.recent(round_.project_id)
            ),
            entry_ids=render_existing_entries(document),
                    external_material=(
                        prepared_text
                        if prepared_text is not None
                        else external_material_text(round_, store=self._materials)
                    ),
                    validation_error=validation_error,
                    round_id=round_id,
                    authorized_features=authorized_features,
                    open_items=open_items,
                    previous_list_items=previous_list_items,
                    contradictions=contradictions,
                    seat_conflicts=seat_conflicts,
                    packet_slot=(
                        f"i{round_.integration_seq}"
                        f"a{MAX_INTEGRATION_ATTEMPTS - validate_left + 1}"
                    ),
                )
                if outcome.discarded and validate_left > 1:
                    # 容器里混进了**根本不是对象**的元素（P23）：与席位、主张同一套——
                    # 先重装一次让它自己改；第二次仍错就在下面丢掉那一条 + 留痕。
                    first = outcome.discarded[0]
                    validation_error = repair_note(
                        PacketParseError(
                            "模型返回的报文过不了 schema",
                            purpose="integration",
                            discarded=(first.container, first.index),
                        )
                    )
                    validate_left -= 1
                    continue
                if outcome.overflow and validate_left > 1:
                    # 超限是可修复的错：先让它自己精简一次，第二次才截断（报告 F2/F11）。
                    first = outcome.overflow[0]
                    validation_error = repair_note(
                        PacketParseError(
                            "模型返回的报文过不了 schema",
                            purpose="integration",
                            overflow=(
                                first.container,
                                first.kept + first.dropped,
                                first.kept,
                            ),
                        )
                    )
                    validate_left -= 1
                    continue
                assembled, gaps = self._assemble_and_gaps(
                    round_=round_,
                    document=document,
                    ledger=ledger,
                    outcome=outcome,
                    carried=carried,
                )
                if (
                    not assembled.questions
                    and not assembled.proposals
                    and not assembled.list_items
                ):
                    # 装配完**什么都不剩**——模型整包是空的，或者它写的东西全被丢掉了
                    # （去处写错 / 条目不存在 / 与文档重复）。判成**报文错**，走既有的
                    # "重装 → 仍不行就降级"出口；不判的话会一路走到 `draft_of(...) is None`
                    # → `ShapeError` → **HTTP 409**，用户看到的是一句莫名其妙的错。
                    # 判据用**装配后**的产物（不是报文）：那才是用户真能拿到的东西。
                    raise PacketParseError(
                        "装配完什么都不剩：题、提案、清单项都没有（这一趟等于没给东西）",
                        purpose="integration",
                    )
            except ModelCallFailed as error:
                # 网关类失败：网关自己已经重试 3 次 + 退避，报文重装解决不了它 → 立刻判死
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=error.message,
                    # "模型没返回 JSON / 空回复"这两类它自己修得了（网关带了 hint）；
                    # 超时 / 5xx 那类不带（`hint` 是 None）
                    hint=error.hint,
                )
                raise
            except (PacketParseError, DomainError) as error:
                # 形状错 / 结构错 / 内容错**都是可修复的错**：给第二次机会。
                # 以前 `except ApplicationError` 把解析错和网关错一起判死，于是
                # `repair_note` 对整合从来没生效过（真跑留样 15 与 16 差 55 秒就是证据）。
                message = getattr(error, "message", str(error))
                if validate_left == 1:
                    reason = f"整合报文两次都过不了校验：{message}"
                    if seat_questions(round_.current_reviews):
                        # 有可救的东西就别判死：席位的疑问原样落成 s11 开放问题，
                        # 走草案卡让用户自己决定（04-未决 C9；与 C6/C7 的降级同一套语义）。
                        return IntegrationResult(
                            assembled=Assembly(
                                questions=(),
                                list_items=(),
                                proposals=(),
                                degrade_reason=reason,
                            ),
                            gaps=(),
                            carried_from_queue=carried_from_queue,
                            carried_from_open_items=carried_from_open_items,
                        )
                    fail_round(
                        rounds=self._rounds,
                        round_writer=self._round_writer,
                        uow=self._uow,
                        events=self._events,
                        clock=self._clock,
                        round_id=round_id,
                        reason=reason,
                        # 报文类失败才留提示：重试时它变成第一次尝试的 validation_error
                        hint=(
                            repair_note(error)
                            if isinstance(error, PacketParseError)
                            else message
                        ),
                    )
                    raise PacketParseError(reason, purpose="integration") from error
                validation_error = (
                    # 解析错：`repair_note` 有结构化细节（形状 / 超限 / 字段路径）要说
                    repair_note(error)
                    if isinstance(error, PacketParseError)
                    # 领域错：它自己的话术更具体（如"选择题至少两个选项"），别用通用话盖掉
                    else message
                )
                validate_left -= 1
                continue
            except ApplicationError as error:
                # 兜底：认不出的应用层错误照旧"失败必须是失败"（与改动前的行为一致）
                fail_round(
                    rounds=self._rounds,
                    round_writer=self._round_writer,
                    uow=self._uow,
                    events=self._events,
                    clock=self._clock,
                    round_id=round_id,
                    reason=error.message,
                )
                raise
            # 报文没错，但模型可能**没给席位意见交代去向**（`02` §11.0.37）。
            break

        # —— 第二段：归因补交代（`02` §11.0.38）——
        #
        # 与第一段分开是因为**修法不一样**：第一段要么改整包（校验错），要么判死；
        # 这一段只用**小调用**问"我点名的那些里，哪些是你没采用的"，然后把它并回原报文，
        # **绝不重写题目与提案**。真跑（P23）证明重写整包会"补一批、又漏一批"：
        # 点名 3/4/3 条它补了 2/4/3 条，可重写出来的报文又漏了别的，总量降不下来。
        missing_before: int | None = None
        while True:
            accounted = _accounted(
                round_,
                assembled=assembled,
                proposals=assembled.proposals,
                questions=assembled.questions + assembled.deferred,
            )
            unaccounted = _unaccounted_for(
                round_,
                assembled=assembled,
                proposals=assembled.proposals,
                questions=assembled.questions + assembled.deferred,
                ledger=ledger,
            )
            if not unaccounted or not accounting_left:
                break
            if missing_before is not None and len(unaccounted) >= missing_before:
                # 上一趟点过名之后**没见少**：再点一次大概率还是这样，别白花钱。
                break
            named = frozenset(item.handle for item in unaccounted)
            try:
                answer = await self._integrator.dispositions(
                    opinions=render_selected_opinions(
                        round_.current_reviews,
                        named,
                        ledger=ledger,
                        round_id=round_id,
                    ),
                    produced=render_citations(
                        accounted_handles=accounted.handles,
                        questions=assembled.questions + assembled.deferred,
                        proposals=assembled.proposals,
                    ),
                    round_id=round_id,
                    packet_slot=(
                        f"i{round_.integration_seq}"
                        f"d{MAX_ACCOUNTING_REPAIRS - accounting_left + 1}"
                    ),
                )
            except ApplicationError:
                # 补交代是**锦上添花**：这一步挂了不该把一整轮判死（报文本身是好的）。
                # 剩下的照旧写进 `Round.dropped`（source=unaccounted）——沉默仍是沉默。
                break
            accounting_left -= 1
            missing_before = len(unaccounted)
            # 合并进**信封**而不是 Outcome：报文只有信封一处（这是这轮刚消掉的两条路径），
            # `replace(outcome, packet=...)` 会造出"Outcome 是新的、信封是旧的"那种漂移。
            outcome = replace(
                outcome,
                envelope=replace(
                    outcome.envelope,
                    packet=_merge_dispositions(
                        outcome.envelope.packet, answer.envelope.packet, named=named
                    ),
                ),
            )
            assembled, gaps = self._assemble_and_gaps(
                round_=round_,
                document=document,
                ledger=ledger,
                outcome=outcome,
                carried=carried,
            )
        return IntegrationResult(
            assembled=assembled,
            gaps=gaps,
            carried_from_queue=carried_from_queue,
            carried_from_open_items=carried_from_open_items,
            envelope=outcome.envelope,
        )


def _dropped_items_for(
    round_: Round,
    *,
    assembled: Assembly,
    proposals: Sequence[Proposal],
    produced_questions: Sequence[Question],
    ledger: EvidenceLedger,
) -> tuple[DroppedItem, ...]:
    """这一趟的留痕：三个来源合成一条通道（`04-未决.md` C5 后半）。

    装配层不假设"模型会老实交代"——它自己核一遍：哪些意见真的被用到了、
    哪些只是被申报为丢弃、剩下的就是"没人说过它去哪了"（`unaccounted`）。
    问卷路、草案路、降级路都走这一个出口，所以留痕不会只在某一条路上活着。
    """

    return assembled.dropped + _unaccounted_for(
        round_,
        assembled=assembled,
        proposals=proposals,
        questions=produced_questions,
        ledger=ledger,
    )


@dataclass(frozen=True, slots=True)
class Accounting:
    """这一趟"哪些席位意见算交代过了"：两个集合（地址 + 它自己的出处编号）。"""

    handles: frozenset[str]
    eids: frozenset[EvidenceId]


def _accounted(
    round_: Round,
    *,
    assembled: Assembly,
    proposals: Sequence[Proposal],
    questions: Sequence[Question],
) -> Accounting:
    """把"交代过"算成一个值——**判定（要不要点名重装）与写库（留痕）共用这一份实现**。

    两处口径不同的话，要么"为不存在的问题多花一次模型调用"，要么"该报的没报"。

    为什么判定时用 `assembled.proposals`、写库时用加上机械提案的全量也对得上：
    `__call__` 里补的那些机械提案（s11 落地 / 答案机械落地 / 撤下未决 / 降级）引用的
    只有 `system_default` 与 `card_answer` 的编号，**从不引用席位判断自己的编号**，
    所以两份 `eids` 里"属于席位判断"的那部分完全一样。
    """

    handles = frozenset(assembled.accounted_handles)
    if assembled.degrade_reason is not None:
        # 整合失败时，这一趟的疑问会被原样落进 s11：算交代过；没落地的判断仍会报
        handles |= frozenset(
            seat_entry_handle(seat_id, opinion.local_id)
            for seat_id, opinion in seat_questions(round_.current_reviews)
        )
    eids = cited_eids(proposals, questions)
    return Accounting(handles=handles, eids=eids)


def _unaccounted_for(
    round_: Round,
    *,
    assembled: Assembly,
    proposals: Sequence[Proposal],
    questions: Sequence[Question],
    ledger: EvidenceLedger,
) -> tuple[DroppedItem, ...]:
    """这一趟**没被交代**的席位意见（`02` §11.1 那三条边界的唯一实现）。

    整合那一趟用它决定"要不要点名重装一次"，写库那一刻用它出留痕——同一份口径。
    """

    accounted = _accounted(
        round_, assembled=assembled, proposals=proposals, questions=questions
    )
    return unaccounted_drops(
        round_.current_reviews,
        ledger=ledger,
        round_id=round_.round_id,
        accounted_handles=accounted.handles,
        accounted_eids=accounted.eids,
        declared=frozenset(
            item.handle
            for item in assembled.dropped
            if item.source is DropSource.INTEGRATOR
        ),
    )


def _unreferenced_materials_for(
    round_: Round,
    *,
    proposals: Sequence[Proposal],
    questions: Sequence[Question],
) -> tuple[str, ...]:
    """本轮带进提示词但未被任何提案/题目引用的材料块出处编号（F35）。

    核算口径与席位意见的 `_accounted` 同一份：提案 origin.refs ∪ 题目 evidence_refs。
    差集 = 零引用。只计算"本轮带进来的"（Round.materials），不算账本里其他编号。
    """
    accounted = cited_eids(proposals, questions)
    return tuple(
        ref.eid
        for ref in round_.materials
        if ref.eid not in accounted
    )


def _merge_dispositions(
    packet: IntegrationPacket,
    answer: DispositionsPacket,
    *,
    named: frozenset[str],
) -> IntegrationPacket:
    """把小调用交回的"没采用的申报"并回原报文（`02` §11.0.38）。

    两条边界：

    - **只认点过名的 handle**：这次问的就是那几条，答案里多出来的地址不并入——
      它不是"哪条意见没进卡片"，并进来只会给留痕添噪音；
    - **按 handle 去重**：模型可能把已经申报过的又说一遍。

    返回**新**报文（不原地改）：并回去的只有 `dropped_opinions`，题目与提案一个字不动。
    """

    existing = {item.handle for item in packet.dropped_opinions}
    merged = list(packet.dropped_opinions)
    for item in answer.dropped_opinions:
        if item.handle in named and item.handle not in existing:
            merged.append(item)
            existing.add(item.handle)
    if len(merged) == len(packet.dropped_opinions):
        return packet
    return packet.model_copy(update={"dropped_opinions": merged})


class SubmitAnswers:
    """答案落账本（card_answer + question_id）→ 回到席位轮。

    答案**不是**被消费掉就完：它进账本，图接着回到席位轮重新判断（`02` §2 要点 2）。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        catalog: SeatCatalog,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._catalog = catalog
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(
        self,
        *,
        round_id: RoundId,
        form_id: str,
        answers: Sequence[AnswerInput],
        skipped: Sequence[str] = (),
    ) -> None:
        with self._uow:
            round_ = self._rounds.get(round_id)
            ledger = self._evidence.get(round_.project_id)
            questionnaire = round_.questionnaire
            if questionnaire is None:
                raise NotAwaitingAnswers(step=round_.step)
            by_id = {str(question.question_id): question for question in questionnaire.questions}
            now = self._clock.now()
            answer_set = AnswerSet(
                form_id=FormId(form_id),
                answers=tuple(
                    Answer(
                        question_id=QuestionId(item.question_id),
                        text=item.text,
                        selected=tuple(item.selected),
                    )
                    for item in answers
                ),
                answered_at=now,
                skipped=tuple(QuestionId(item) for item in skipped),
            )
            # C8 缩编：只请"这批题跟它有关"的席位。名单要基于**用户编制**算，
            # 结果也一定在编制之内（领域还会再校验一次）。
            touched = tuple(item.question_id for item in answer_set.answers) + tuple(
                answer_set.skipped
            )
            active_seats = narrow_seats(
                seats=round_.seats,
                catalog=self._catalog,
                questionnaire=questionnaire,
                touched=touched,
            )
            round_.submit_answers(answer_set, active_seats=active_seats)  # 领域把关（R6）
            for answer in answer_set.answers:
                question = by_id[str(answer.question_id)]
                eid = ledger.record(
                    kind=EvidenceKind.CARD_ANSWER,
                    text=answer_text(question, answer),
                    source=EvidenceSource(round_id=round_id, question_id=answer.question_id),
                    now=now,
                )
                slug = question.target.feature_slug
                if (
                    question.kind is QuestionKind.DECISION
                    and slug is not None
                    and NEW_FEATURE_YES in answer.selected
                    and slug not in round_.authorized_new_features
                ):
                    round_.authorize_new_feature(slug, basis=eid)
            self._evidence_writer.save(ledger)
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


class AttachMaterials:
    """把搜索框里点中的块「带进本轮」：pin 材料 + 记 manual_chunks。

    **不发 E 号**：E 号在投影时发（P2）。这里只把块放进证据池；
    下一次 `RoundContextPreparer.prepare` 会先装 manual、再按预算发号。

    状态闸：只在还没出草案时允许。出了草案再带料，改不了已经生成的那一份——
    与其让用户以为生效了，不如 409 说清"等下一轮"。
    """

    ATTACHABLE_STEPS = frozenset(
        {Step.STARTED, Step.CLAIMS_READY, Step.SEATS_RUNNING, Step.AWAITING_ANSWERS}
    )

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        materials: MaterialStore,
        uow: UnitOfWork,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._materials = materials
        self._uow = uow

    async def __call__(
        self, *, round_id: RoundId, material_id: str, chunk_ids: Sequence[str]
    ) -> None:
        wanted = tuple(dict.fromkeys(item.strip() for item in chunk_ids if item.strip()))
        if not wanted:
            raise ShapeError("要带进的块不能为空")
        if len(wanted) > MAX_MANUAL_CHUNKS_PER_CALL:
            raise ShapeError(
                f"一次最多带 {MAX_MANUAL_CHUNKS_PER_CALL} 块",
                count=len(wanted),
            )

        with self._uow:
            round_ = self._rounds.get(round_id)
            # 状态与材料都在事务里读：材料可能在"读到它"和"pin 它"之间被删/重解析。
            self._ensure_attachable(round_)
            packet = self._materials.get(material_id)
            if packet is None or packet.project_id != str(round_.project_id):
                raise UnknownMaterial(material_id)
            if packet.parsed.origin == WEB_ORIGIN:
                # 联网结果一条一个号、走 WEB_BUDGET；混进材料池会破"编号==进提示词的块"。
                raise WebMaterialNotAttachable(material_id=material_id)
            known = {chunk.chunk_id for chunk in packet.parsed.chunks}
            outside = [item for item in wanted if item not in known]
            if outside:
                raise ShapeError("这些块不属于这份材料", chunk_ids=outside)
            already = {ref.chunk_id for ref in round_.materials} | {
                item.chunk_id for item in round_.manual_chunks
            }
            fresh = [
                ManualChunk(material_id=material_id, chunk_id=item)
                for item in wanted
                if item not in already
            ]
            if not fresh:
                return  # 幂等：已经在本轮（投影过或已手动带过）
            if len(round_.manual_chunks) + len(fresh) > MAX_MANUAL_CHUNKS:
                raise ShapeError(
                    f"本轮最多手动带 {MAX_MANUAL_CHUNKS} 块",
                    count=len(round_.manual_chunks) + len(fresh),
                )
            round_.pin_materials((*round_.pinned_materials, material_id))
            round_.note_manual_chunks((*round_.manual_chunks, *fresh))
            self._round_writer.save(round_)

    def _ensure_attachable(self, round_: Round) -> None:
        if round_.step not in self.ATTACHABLE_STEPS:
            raise RoundNotAttachable(round_id=round_.round_id, step=round_.step)


class ConfirmDraft:
    """唯一写入路径的入口。编排层、席位、整合者都拿不到 `DocumentWriter`。"""

    def __init__(
        self,
        *,
        committer: ProposalCommitter,
        rounds: RoundReader,
        clock: Clock,
        events: EventPublisher,
        digest_repo: DigestRepository = NO_DIGESTS,
    ) -> None:
        self._committer = committer
        self._rounds = rounds
        self._clock = clock
        self._events = events
        self._digest_repo = digest_repo

    async def __call__(
        self,
        *,
        round_id: RoundId,
        accepted: Sequence[str],
        accepted_memory: Sequence[str] = (),
    ) -> CommitResult:
        result = self._committer.commit(
            round_id=round_id,
            accepted=tuple(ProposalId(item) for item in accepted),
            now=self._clock.now(),
            # 记忆与文档同一次确认、同一个事务（M6）：勾了才写，没勾就什么都不发生
            accepted_memory=tuple(accepted_memory),
        )
        # L4: 生成轮次摘要（事务外）
        self._build_and_save_digest(round_id, accepted)
        round_ = self._rounds.get(round_id)
        self._events.publish(
            Event(
                name=DOC_CHANGED,
                project_id=str(round_.project_id),
                round_id=str(round_id),
                payload={
                    "version_no": result.version.version_no,
                    "written_block_ids": [
                        str(block_id) for block_id in result.written_block_ids
                    ],
                    "summary": result.version.summary,
                },
                occurred_at=self._clock.now(),
            )
        )
        _publish(self._events, self._clock, round_)
        return result

    def _build_and_save_digest(self, round_id, accepted) -> None:
        """生成并保存 L4 轮次摘要。"""
        round_ = self._rounds.get(round_id)
        if round_.draft is None:
            return
        confirmed = [p for p in round_.draft.proposals if p.proposal_id in accepted]
        digest = build_round_digest(
            confirmed, str(round_id), str(round_.project_id)
        )
        self._digest_repo.save(digest)


class ForgetMemory:
    """忘掉一条记忆（记忆抽屉上的删除按钮，M6）。

    这是**唯一一条不走确认卡**的记忆写入：用户要能立刻收回一条自己后悔记下的东西，
    而"等下一轮 Curator 提议删它"要等到下次出草案。代价与边界都写清楚：

    - 只删**一条**（批量删改仍然只走 Curator 提议 + 确认）；
    - 删不掉（id 不存在）→ `UnknownMemoryEntry`（404），不假装删掉了；
    - 走**同一个 `MemoryWriter`**（守卫 `test_write_path.py` 钉着），照样一个事务。
    """

    def __init__(
        self,
        *,
        memory: MemoryReader,
        memory_writer: MemoryWriter,
        uow: UnitOfWork,
        clock: Clock,
    ) -> None:
        self._memory = memory
        self._memory_writer = memory_writer
        self._uow = uow
        self._clock = clock

    async def __call__(self, *, project_id: ProjectId, memory_id: str) -> None:
        with self._uow:
            memory = self._memory.get(project_id)
            self._memory_writer.save(memory.delete(memory_id, now=self._clock.now()))


class GetMemoryUsage:
    """最近 N 轮一次没引用过的记忆 id（记忆抽屉的「久未使用」标签读它）。

    只读：只注入 `MemoryReader` / `WorkspaceIndex` / `RoundReader`，拿不到任何写端口——
    所以它不可能改东西（与 `ListRounds` 同一结构保证）。
    """

    def __init__(
        self,
        *,
        memory: MemoryReader,
        index: WorkspaceIndex,
        rounds: RoundReader,
    ) -> None:
        self._memory = memory
        self._index = index
        self._rounds = rounds

    async def __call__(self, *, project_id: ProjectId) -> tuple[str, ...]:
        heads = self._index.list_rounds(project_id, limit=MEMORY_USAGE_WINDOW)
        order = tuple(reversed([head.round_id for head in heads]))
        cited_by_round: dict[str, frozenset[str]] = {}
        for head in heads:
            round_ = self._rounds.get(RoundId(head.round_id))
            if round_.draft is None:
                cited_by_round[head.round_id] = frozenset()
                continue
            questions = tuple(
                question
                for form in round_.questionnaire_history
                for question in form.questions
            )
            cited_by_round[head.round_id] = frozenset(
                str(eid) for eid in cited_eids(round_.draft.proposals, questions)
            )
        unused = unused_entries(
            self._memory.get(project_id).entries,
            cited_by_round,
            window=MEMORY_USAGE_WINDOW,
            order=order,
        )
        return tuple(sorted(unused))


class RetryRound:
    """**继续这一轮**：失败就先 `resume`；没失败就直接接着跑。

    为什么允许对"没失败"的轮调用：关页面 / 进程崩了以后，轮停在 `seats_running`，
    它不是 failed 状态，但同样需要接着跑（计划 §3.7）。两种场面收在一个动作里，
    界面就只有一个"继续"按钮。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        with self._uow:
            round_ = self._rounds.get(round_id)
            if round_.step is Step.FAILED:
                round_.resume()
                self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


class DigRound:
    """用户主动"继续深挖"：从草案卡回到提问（P14 C6）。

    与 `RetryRound` 同形状——一个事务里做**领域动作** + 发事件。**不碰文档**：
    草案从没被确认过（写入只在确认那一刻发生），所以没有任何回滚要担心。
    真正的"接着跑"由图的 `dig → seats_running → seat_round → integrate` 路由完成。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        with self._uow:
            round_ = self._rounds.get(round_id)
            round_.dig()
            self._round_writer.save(round_)
        _publish(self._events, self._clock, round_)


MANUAL_PREFERENCE_BLOCK = str(PREFERENCE_AREA_ID)

EDIT_SUMMARY = {
    WriteOp.ADD: "加一条",
    WriteOp.REPLACE: "改一条",
    WriteOp.REMOVE: "删一条",
}


@dataclass(frozen=True, slots=True)
class EditResult:
    version_no: int
    written_block_ids: tuple[str, ...]
    entry_id: str
    document: DocumentView


class EditEntry:
    """用户直接编辑文档（F17）：改 / 加 / 删条目，一次改动一版（`trigger=manual`）。

    它和 AI 写入只差两点：

    1. **来源是 `manual`**：手改内容也进账本，有自己的编号（`04-未决.md` D1 的建议版），
       所以文档里每条内容都查得到"这句话是谁说的"。
    2. **改过的块打上 `manually_edited`**：之后 AI 的改写与删除会被 `BlockManuallyEdited` 拦下来，
       不会把你亲手写的东西悄悄覆盖掉——这就是"卡片改的与文档改的谁优先"的答案。
       （M11 的例外：用户显式勾选的评测提案可覆盖，见 `02` §11.0.48。）

    写入仍然只有一条路：这里只负责准备指令与编号，落盘交给 `ProposalCommitter.apply_manual`。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        committer: ProposalCommitter,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
    ) -> None:
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._committer = committer
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._events = events

    async def __call__(
        self,
        *,
        project_id: ProjectId,
        op: WriteOp,
        entry_id: str | None = None,
        block_id: str | None = None,
        target: Target | None = None,
        text: str = "",
    ) -> EditResult:
        with self._uow:
            document: Document = self._documents.get(project_id)
            ledger = self._evidence.get(project_id)
            now = self._clock.now()
            resolved, existing = _resolve_manual_target(
                document, op=op, entry_id=entry_id, block_id=block_id, requested=target
            )
            # 手改也要有出处：编号先发，内容才落得进去（GB1）。
            manual_eid = ledger.record(
                kind=EvidenceKind.MANUAL,
                text=_manual_note(
                    op,
                    existing_text=None if existing is None else existing.text,
                    text=text,
                ),
                source=EvidenceSource(),
                now=now,
            )
            if op is WriteOp.ADD:
                final_entry_id = EntryId(self._ids.new("entry"))
                refs = (manual_eid,)
                instruction_text = text
            else:
                assert existing is not None  # _resolve_manual_target 保证
                final_entry_id = existing.entry_id
                # 保留原来的出处，再加上"你自己改的"这一条：新旧都能查。
                refs = tuple(existing.refs) + (manual_eid,)
                # 删除也要有一条非空文本（写入指令的硬要求）；记录被删掉的是什么。
                instruction_text = text if text.strip() else existing.text

            instruction = WriteInstruction(
                instruction_id=f"manual-{manual_eid}",
                entry_id=final_entry_id,
                op=op,
                target=resolved,
                text=instruction_text,
                refs=refs,
                kind=_manual_kind(document, block_id=block_id) if op is WriteOp.ADD else None,
                manual=True,
            )
            result: CommitResult = self._committer.apply_manual(
                project_id=project_id,
                instruction=instruction,
                now=now,
                summary=f"手工{EDIT_SUMMARY[op]}：{text.strip() or '（删除）'}"[:120],
            )
            self._evidence_writer.save(ledger)
            view = self._documents.get(project_id).view()
        self._events.publish(
            Event(
                name=DOC_CHANGED,
                project_id=str(project_id),
                payload={
                    "version_no": result.version.version_no,
                    "written_block_ids": [str(item) for item in result.written_block_ids],
                    "summary": result.version.summary,
                },
                occurred_at=now,
            )
        )
        return EditResult(
            version_no=result.version.version_no,
            written_block_ids=tuple(str(item) for item in result.written_block_ids),
            entry_id=str(final_entry_id),
            document=view,
        )


def _resolve_manual_target(
    document: Document,
    *,
    op: WriteOp,
    entry_id: str | None,
    block_id: str | None,
    requested: Target | None = None,
) -> tuple[Target, object | None]:
    """手改要落到哪个 target、动的是哪一条。

    ADD 有两种给法，二选一：

    1. `requested`（复述卡那条路用的形状）：`{feature_slug, field}` → 落到"某个功能的某个字段"，
       字段块**不存在就由 domain 自己建**（F8-①）；`{section_id, field}` → 落到章节槽；
       `{section_id}` → 整章；偏好区 → 偏好区。
    2. `block_id` 三种给法：`preference-area`（偏好区）、某个块的 id（往这块加）、
       某个章节 id（`s1`…`s13`：这一章还没有块，那就新开一段）。
    """

    # 章节 id 按**这一份文档的模板**认（不是全局 13 章表）：手改的章节列表与工作区一致。
    adapter = adapt(document.template)
    if op is WriteOp.ADD:
        if requested is not None:
            return _target_of_request(requested, adapter), None
        if block_id is None:
            raise ShapeError("加一条要先说加到哪一块")
        if block_id == MANUAL_PREFERENCE_BLOCK:
            return Target.preference(), None
        if adapter.has(block_id):
            return Target.body(block_id), None
        block = next((item for item in document.blocks if str(item.block_id) == block_id), None)
        if block is None:
            raise ShapeError(f"找不到块 {block_id}", block_id=block_id)
        return _target_of_block(block, adapter), None

    if entry_id is None:
        raise ShapeError("改或删要先说改哪一条")
    for entry in document.preference_entries:
        if str(entry.entry_id) == entry_id:
            return Target.preference(), entry
    for block in document.blocks:
        entry = block.entry(EntryId(entry_id))
        if entry is not None:
            return _target_of_block(block, adapter), entry
    raise ShapeError(f"找不到条目 {entry_id}", entry_id=entry_id)


def _target_of_request(requested: Target, adapter: TemplateAdapter) -> Target:
    """把接口层给的 target 归一成"写哪"。功能字段这一支由 domain 建块（F8-①）。

    成员校验（这一章在不在模板里、功能挂不挂得住）在下一行统一做——
    归一（分派到哪个构造器）与校验（这套模板认不认）是两件事。
    """

    if requested.zone is Zone.PREFERENCE:
        return Target.preference()
    if requested.feature_slug is not None and requested.field is not None:
        target = Target.feature_field(
            requested.feature_slug, requested.field, home=adapter.feature_home
        )
    elif requested.section_id is None:
        raise ShapeError("target 说不清要落到哪")
    elif requested.field is not None:
        target = Target.body(requested.section_id, field=requested.field)
    else:
        target = Target.body(requested.section_id)
    adapter.check_target(target.section_id, target.feature_slug, target.field)
    return target


def _target_of_block(block, adapter: TemplateAdapter) -> Target:  # type: ignore[no-untyped-def]
    """把一个既有块还原成"改它等于写到哪"（手改 / 回退都要）。"""

    if block.field is not None and block.feature_slug is not None:
        return Target.feature_field(
            block.feature_slug, block.field, home=adapter.feature_home
        )
    if block.field is not None and block.parent_id is not None:
        # 章节槽（s8 的六块）：手改要落回那个槽，别退化成"整章"
        return Target.body(str(block.parent_id), field=block.field)
    if block.kind is BlockKind.FEATURE and block.feature_slug is not None:
        return Target.body(adapter.feature_home, feature_slug=block.feature_slug)
    section_id = str(block.parent_id or block.block_id)
    return Target.body(section_id)


def _manual_kind(document: Document, *, block_id: str | None) -> BlockKind | None:
    """往章节级块里加内容时，跟着被点的块保持同一种 kind（清单还是清单）。"""

    # 偏好区不是块；章节 id 也不能照抄——章节根块的 kind 是 section，
    # 抄下来会造出第二个章节根（D1：章节集必须不多不少，这条真踩过）。
    if block_id is None or block_id == MANUAL_PREFERENCE_BLOCK:
        return None
    if adapt(document.template).has(block_id):
        return None
    block = next((item for item in document.blocks if str(item.block_id) == block_id), None)
    return None if block is None else block.kind


def _manual_note(op: WriteOp, *, existing_text: str | None, text: str) -> str:
    if op is WriteOp.REMOVE:
        return f"手工删除：{existing_text or ''}".strip()
    if op is WriteOp.REPLACE:
        return f"手工改写：{text.strip()}"
    return f"手工新增：{text.strip()}"


class CreateProject:
    """建项目 = 按选定模板建一份空骨架文档（计划 §3.5：v1 不建 Project 聚合）。

    `template_id` 缺省 standard：老客户端只传 title 照样能建，行为与今天逐字节一致。
    """

    def __init__(
        self,
        *,
        factory: ProjectFactory,
        index: WorkspaceIndex,
        clock: Clock,
        ids: IdGenerator,
        templates: TemplateRepository,
    ) -> None:
        self._factory = factory
        self._index = index
        self._clock = clock
        self._ids = ids
        self._templates = templates

    async def __call__(
        self, *, title: str, template_id: str = STANDARD_TEMPLATE_ID
    ) -> ProjectSummaryView:
        template = self._templates.get(template_id).template
        project_id = ProjectId(self._ids.new("p"))
        self._factory.create(
            project_id=project_id, title=title, now=self._clock.now(), template=template
        )
        return self._index.summary(project_id)


class CreateTemplate:
    """往模板库里加一套自定义模板：`validate` 过才进库，不过的连 id 都不占。

    id 服务端盖（`tpl-` + 随机）：调用方不许自选——自选会撞 builtin（standard/light）。
    """

    def __init__(self, *, templates: TemplateRepository, ids: IdGenerator) -> None:
        self._templates = templates
        self._ids = ids

    async def __call__(self, *, title: str, sections: Sequence[Mapping[str, object]]) -> str:
        template = template_from_dict(
            {
                "template_id": self._ids.new("tpl"),
                "title": title,
                "sections": list(sections),
            }
        )
        self._templates.save(template, builtin=False)
        return template.template_id


class ListTemplates:
    """列出库里全部模板（builtin 在前，供创建项目时选）。"""

    def __init__(self, *, templates: TemplateRepository) -> None:
        self._templates = templates

    async def __call__(self) -> tuple[TemplateView, ...]:
        return tuple(
            template_view(entry.template, builtin=entry.builtin)
            for entry in self._templates.list()
        )


class GetTemplate:
    """取一套模板的全文（创建页预览 / 调试用）。"""

    def __init__(self, *, templates: TemplateRepository) -> None:
        self._templates = templates

    async def __call__(self, *, template_id: str) -> TemplateView:
        entry = self._templates.get(template_id)
        return template_view(entry.template, builtin=entry.builtin)


class UpdateTemplate:
    """把一套模板的整份定义换掉（`02` §11.0.70）。

    **改就是全量替换，不做增量**：模板定义是一份声明，增量改要么留一堆 diff 状态，
    要么就得有一个"局部合并"的语义——那是一套新的数据契约，而界面上改的就是一份
    草稿的全量提交（`06` §2.3）。没有增量需求就不造这个轮子。

    安全性有两条：内置锁死（409，`BuiltinTemplateProtected`）；`template_from_dict`
    自己会 `validate`，非法定义抛 `BadTemplateDefinition` **而且不落库**（先算再写，
    不用先写再回滚）。
    """

    def __init__(self, *, templates: TemplateRepository) -> None:
        self._templates = templates

    async def __call__(
        self, *, template_id: str, title: str, sections: Sequence[Mapping[str, object]]
    ) -> TemplateView:
        if self._templates.get(template_id).builtin:
            raise BuiltinTemplateProtected(template_id)
        template = template_from_dict(
            {"template_id": template_id, "title": title, "sections": list(sections)}
        )
        self._templates.save(template, builtin=False)
        # 存进去的就是刚算出来那份，直接投影回去（照 `CreateTemplate` 之后回读同一份的
        # 做法，但这里不必再读一次库）
        return template_view(template, builtin=False)


class DeleteTemplate:
    """删掉一套自建模板（`02` §11.0.70）。

    **不影响任何已建项目**：项目行里存的是模板的全文 JSON 快照，不是 id 引用，两张表
    没有外键。真实后果只有一个——以后不能用这个 id 建新项目。
    **刻意不做**引用计数与"有项目在用就拒绝"：那是在快照模型上凭空加锁。
    """

    def __init__(self, *, templates: TemplateRepository) -> None:
        self._templates = templates

    async def __call__(self, *, template_id: str) -> None:
        if self._templates.get(template_id).builtin:
            raise BuiltinTemplateProtected(template_id)
        self._templates.delete(template_id)


class GetWorkspace:
    def __init__(
        self,
        *,
        documents: DocumentReader,
        rounds: RoundReader,
        evidence: EvidenceReader,
        index: WorkspaceIndex,
        catalog: SeatCatalog,
        tools: ToolCatalog,
        available_tool_keys: frozenset[str],
        checker: ReadinessChecker,
        materials: MaterialStore,
        memory: MemoryReader,
    ) -> None:
        self._documents = documents
        self._rounds = rounds
        self._evidence = evidence
        self._index = index
        self._catalog = catalog
        self._tools = tools
        self._available_tool_keys = available_tool_keys
        self._checker = checker
        self._materials = materials
        self._memory = memory

    async def __call__(self, *, project_id: ProjectId) -> WorkspaceView:
        document = self._documents.get(project_id)
        round_id = self._index.latest_round_id(project_id)
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        round_ = None if round_id is None else self._rounds.get(round_id)
        ledger = None if round_ is None else self._evidence.get(project_id)
        view = document.view()
        web_tool = web_search_tool(self._tools, keys=self._available_tool_keys)
        return workspace_view(
            project=self._index.summary(project_id),
            document=view,
            round_=round_,
            seat_titles=titles,
            catalog=self._catalog,
            ledger=ledger,
            materials=() if round_ is None else _materials_of(round_, store=self._materials),
            web_search_available=web_tool is not None,
            web_search_network_access=False if web_tool is None else web_tool.network_access,
            readiness=self._checker.check(view, adapter=adapt(document.template)),
            memory=self._memory.get(project_id).entries,
        )


class UploadMaterial:
    """上传一份材料：**解析在事务外、落库在事务内**（与模型调用同一条纪律）。

    - 上限在**读 body 之前**就判（路由层用 `file.size`），这里再兜一层；
    - 同一份字节重复上传 → 复用（存储层按 `(project, sha256)` 去重），不占两次预算；
    - 抽不出文字 / 类型不支持 → `UnsupportedMaterial`（明确报错，不静默存空）。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        materials: MaterialStore,
        parser: MaterialParser,
        clock: Clock,
        uow: UnitOfWork,
        vectors: MaterialVectorIndex = NO_VECTORS,
    ) -> None:
        self._documents = documents
        self._materials = materials
        self._parser = parser
        self._clock = clock
        self._uow = uow
        self._vectors = vectors

    async def __call__(
        self, *, project_id: ProjectId, filename: str, media_type: str, data: bytes
    ) -> MaterialSummaryView:
        if len(data) > MAX_MATERIAL_BYTES:
            raise MaterialTooLarge(
                f"这份材料太大了（{len(data) // 1024 // 1024}MB > "
                f"{MAX_MATERIAL_BYTES // 1024 // 1024}MB）",
                filename=filename,
            )
        parsed = self._parser.parse(filename=filename, media_type=media_type, data=data)
        digest = hashlib.sha256(data).hexdigest()
        with self._uow:
            self._documents.get(project_id)      # 项目不存在就地失败
            existing = self._materials.find_by_sha(project_id, digest)
            if existing is None and self._materials.count_for_project(
                project_id
            ) >= MAX_MATERIALS_PER_PROJECT:
                raise MaterialTooLarge(
                    f"这个项目的材料已经到上限（{MAX_MATERIALS_PER_PROJECT} 份）",
                    project_id=str(project_id),
                )
            packet = existing or self._materials.put(
                project_id=project_id,
                parsed=parsed,
                sha256=digest,
                content=data,
                now=self._clock.now(),
            )
        # 向量在**主事务提交之后**另起小事务写（ONNX 推理 ~20ms/块，不许占着写锁）。
        # 崩溃窗口由 `migrate()` 的幂等回填兜底。
        if existing is None:
            self._vectors.write(packet.material_id)
        return material_summary_view(packet, current=self._parser.identities())


class FetchMaterial:
    """按网址抓一份网页材料：**抓取在事务外、落库在事务内**（与上传、检索同一条纪律）。

    与"联网检索"分开：那是分析者提名、用户勾选之后的**搜线索**（300 字摘要），
    这是用户**点名的一个页面**——抓正文、块级，跟上传的材料走同一条链。

    同一个网址再抓一次 = **刷新**（照 `P19` 的两条路）：

    - **没被引用过**（没发过编号）→ 就地换内容与块：`material_id`、去重键、
      `created_at` 都不动（界面"这一轮选了哪几份"是按 id 记的，不受影响）；
    - **被引用过** → **另出一份**（变体键 `fetch:{网址}|{新字节的 sha256 前 12 位}`），
      旧的冻结留着——那些块挂着编号，改内容就等于改指编号。

    为什么不能永远幂等复用：网页是会改的。点了"再抓一次"却拿到上次的副本，
    用户只会以为这个功能坏了。抓不到就 `ToolCallFailed` 往上走（HTTP 502），**库里一行都不留**。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        materials: MaterialStore,
        parser: MaterialParser,
        fetcher: WebFetcher,
        index: WorkspaceIndex,
        clock: Clock,
        uow: UnitOfWork,
        vectors: MaterialVectorIndex = NO_VECTORS,
    ) -> None:
        self._documents = documents
        self._materials = materials
        self._parser = parser
        self._fetcher = fetcher
        self._index = index
        self._clock = clock
        self._uow = uow
        self._vectors = vectors

    async def __call__(self, *, project_id: ProjectId, url: str) -> MaterialSummaryView:
        # **归属先于副作用**（Task 6 fix round 2）：先按 owner 读项目，读不到即 `UnknownProject`
        # （404）——绝不先出网。否则指过来的别人的项目 id 也会先发一次抓取请求再去鉴权
        # （无数据泄漏、不落库，但有出网副作用，不该发生）。
        self._documents.get(project_id)
        # 抓取在**事务外**（网络可能很慢，别占着写事务）；抓不到就照实报，不写垃圾
        page = await self._fetcher.fetch(url=url)
        # 解析同样在事务外，而且**走 MaterialParser 那一条真实分派**——于是抓 PDF / Word /
        # Excel 与上传它们走的是同一份切块、同一批上限、同一句"抽不出就说抽不出"。
        # 出处三件套（origin / source_ref / title）由本用例补：fetcher 管网络，
        # parser 管字节→文字，用例管"这份材料是谁的、从哪来"（`dataclasses.replace`
        # 是为了不动 `MaterialParser` 端口形状——那是把用例知识塞进端口）。
        try:
            drafted = self._parser.parse(
                filename=page.title or page.final_url,
                media_type=page.media_type,
                data=page.content,
            )
        except UnsupportedMaterial as error:
            # 解析器的报错只知道"哪份文件"（这里传给它的是标题）——抓取这一路要把**网址**
            # 说给人听，否则用户看见"导出权限读不了"却不知道是哪个链接
            raise UnsupportedMaterial(
                f"这个网址读不出正文（{error.message}）：{page.final_url}",
                source_ref=page.final_url,
            ) from error
        parsed = replace(
            drafted,
            origin=FETCH_ORIGIN,
            source_ref=page.final_url,
            title=page.title or page.final_url,
            warnings=(*_redirect_note(url, page.final_url), *drafted.warnings),
        )
        # 去重键用**最终地址**（2026-10-20 核验实测：原来用原地址、而 `source_ref`
        # 写最终地址 → 同一页（http 跳 https）会存出**两份材料、两个 id、同一个出处**）。
        # 键与出处必须**同源**：用户下次抓那个最终地址，才找得到同一份。
        digest = _fetch_key(page.final_url)
        with self._uow:
            existing = self._materials.find_by_sha(project_id, digest)
            if existing is None:
                if self._materials.count_for_project(
                    project_id
                ) >= MAX_MATERIALS_PER_PROJECT:
                    raise MaterialTooLarge(
                        f"这个项目的材料已经到上限（{MAX_MATERIALS_PER_PROJECT} 份）",
                        project_id=str(project_id),
                    )
                packet = self._materials.put(
                    project_id=project_id,
                    parsed=parsed,
                    sha256=digest,
                    content=page.content,
                    now=self._clock.now(),
                )
            elif self._index.material_references(project_id).get(existing.material_id, ()):
                # 发过编号：不就地改（那些编号指着它里面的块）——另出一份，旧的冻结留着
                packet = self._materials.put(
                    project_id=project_id,
                    parsed=parsed,
                    sha256=_refetch_key(page.final_url, page.content),
                    content=page.content,
                    now=self._clock.now(),
                )
            else:
                packet = self._materials.refresh(
                    existing.material_id, parsed=parsed, content=page.content
                )
                # 就地刷新换了正文：旧向量作废，同事务删掉（不跑推理），提交后重算。
                self._vectors.drop(packet.material_id)
        # 推理在主事务之外：新抓与变体按各自 id 补，就地刷新按原 id 重算。
        self._vectors.write(packet.material_id)
        return material_summary_view(packet, current=self._parser.identities())


def _redirect_note(url: str, final_url: str) -> tuple[str, ...]:
    """抓的时候跳转过才说一句（`FetchedPage` 只报最终地址，跳没跳要跟原话比）。"""

    if final_url == url:
        return ()
    return (f"这个网址跳转过（最终地址：{final_url}）",)


def _fetch_key(url: str) -> str:
    """按网址去重：同一个网址重复抓**不新增一行**（抓的就是那一页）。

    与检索那条路的 `web:{url}` 分开算：一条是"用户点名、我抓的整页"，
    一条是"搜回来的线索"，两者同时存在是对的。
    """

    return hashlib.sha256(f"fetch:{url}".encode()).hexdigest()


def _refetch_key(url: str, content: bytes) -> str:
    """同址再抓、但旧的那份已经发过编号时的**变体键**：`fetch:{网址}|{新字节指纹}`。

    带内容指纹是为了幂等——连点两次、页面没变，就还是那一份，不会生出第三份。
    """

    fingerprint = hashlib.sha256(content).hexdigest()[:12]
    return hashlib.sha256(f"fetch:{url}|{fingerprint}".encode()).hexdigest()


class ListMaterials:
    """这个项目有哪些材料（**只回摘要**，块正文一个字都不进列表）。"""

    def __init__(
        self, *, materials: MaterialStore, parser: MaterialParser, index: WorkspaceIndex
    ) -> None:
        self._materials = materials
        self._parser = parser
        self._index = index

    async def __call__(self, *, project_id: ProjectId) -> tuple[MaterialSummaryView, ...]:
        return list_material_views(
            project_id=project_id,
            materials=self._materials,
            parser=self._parser,
            index=self._index,
        )


def list_material_views(
    *,
    project_id: ProjectId,
    materials: MaterialStore,
    parser: MaterialParser,
    index: WorkspaceIndex,
) -> tuple[MaterialSummaryView, ...]:
    """材料列表的**唯一一份**口径：摘要（仓储给）+ "被哪些编号引用过"（索引给）。

    引用那一列（`eids`）是 `P19` 的冻结判据：**空 = 没被用过 = 可以删、可以就地重解析**。
    它不进材料表——真相源是轮次 payload（`02` §11.0.10："跨轮的真相源只有文档与轮次"）。
    """

    references = index.material_references(project_id)

    def actual_eids(values: tuple[str, ...]) -> tuple[str, ...]:
        # 引用列里混着 `手勾` / `手动带料` 哨兵（冻结判据需要它们）；
        # `MaterialSummaryView.eids` 的契约是**编号**，不能把哨兵当 E 号显示。
        return tuple(
            value
            for value in values
            if len(value) > 1 and value.startswith("E") and value[1:].isdigit()
        )

    return tuple(
        replace(
            item,
            eids=actual_eids(references.get(item.material_id, ())),
            frozen=bool(references.get(item.material_id, ())),
        )
        for item in materials.list_for_project(project_id, current=parser.identities())
    )


class DeleteMaterial:
    """删一份材料：**只有没被引用过的才准删**（`P19`：编号发出即冻结）。

    为什么判据这么硬：编号（`E7`）指向的是**材料里的块**。删了它，`E7` 就指向空气；
    而证据链是这个项目的底线（`01` §6：每条内容都要能点回原话）。所以"删"只在
    **没有任何一轮引用过它**时允许——那时候它连一个编号都没有，删了不留痕。

    返回**刷新后的材料列表**：界面就地在列表上操作，一次拿到真相，不用自己猜。
    """

    def __init__(
        self,
        *,
        materials: MaterialStore,
        parser: MaterialParser,
        index: WorkspaceIndex,
        uow: UnitOfWork,
        vectors: MaterialVectorIndex = NO_VECTORS,
    ) -> None:
        self._materials = materials
        self._parser = parser
        self._index = index
        self._uow = uow
        self._vectors = vectors

    async def __call__(
        self, *, project_id: ProjectId, material_id: str
    ) -> tuple[MaterialSummaryView, ...]:
        packet = self._materials.get(material_id)
        if packet is None or packet.project_id != str(project_id):
            raise UnknownMaterial(material_id)
        eids = self._index.material_references(project_id).get(material_id, ())
        if eids:
            raise MaterialFrozen(material_id, eids=eids, action="删")
        with self._uow:
            # vec 表是影子表，没有 CASCADE 兜底：**先删向量再删块**——反过来的话
            # 块已被 CASCADE 带走，`drop()` 的子查询就找不到任何 chunk_id，删了个空。
            self._vectors.drop(material_id)
            self._materials.delete(material_id)
        return list_material_views(
            project_id=project_id,
            materials=self._materials,
            parser=self._parser,
            index=self._index,
        )


class ReparseMaterial:
    """用**当前解析器**把这份材料的原始字节重读一遍（`P19`）。

    两条路，取决于"有没有发过编号"：

    - **没被引用过**：就地重读（同一个 `material_id`、同一个去重键、原始字节不动）。
      id 不变很要紧——前端"这一轮选了哪几份"是按 id 记的。
    - **被引用过**：**不就地改**（那些块挂着 `E7`、`E12`，改形状 = 改指编号），
      改为**另出一份**（变体键 `reparse:{旧id}|{parser}@{version}`），旧的留作冻结记录。
      变体键让"连点两次"幂等——不会生出第三份。

    联网搜回来的材料（`origin="web_search"`）不给重解析：它的"原始字节"是当时的网页快照，
    要新的就重新搜。
    """

    def __init__(
        self,
        *,
        materials: MaterialStore,
        parser: MaterialParser,
        index: WorkspaceIndex,
        uow: UnitOfWork,
        clock: Clock,
        vectors: MaterialVectorIndex = NO_VECTORS,
    ) -> None:
        self._materials = materials
        self._parser = parser
        self._index = index
        self._uow = uow
        self._clock = clock
        self._vectors = vectors

    async def __call__(
        self, *, project_id: ProjectId, material_id: str
    ) -> tuple[MaterialSummaryView, ...]:
        packet = self._materials.get(material_id)
        if packet is None or packet.project_id != str(project_id):
            raise UnknownMaterial(material_id)
        raw = self._materials.raw_bytes(material_id)
        if raw is None:
            raise UnsupportedMaterial(
                "这份材料没有留下原始字节，没法重解析（重新上传一份吧）",
                material_id=str(material_id),
            )
        if packet.parsed.origin == WEB_ORIGIN:
            raise UnsupportedMaterial(
                "联网检索回来的材料不重解析——它是当时的网页快照，要新的就重新搜一次",
                material_id=str(material_id),
                origin=packet.parsed.origin,
            )
        # 解析在**事务外**（可能很慢，别占着写事务）；抽不出字就照实报，不写垃圾
        parsed = self._parser.parse(
            filename=packet.parsed.title,
            media_type=packet.parsed.media_type,
            data=raw,
        )
        frozen = tuple(self._index.material_references(project_id).get(material_id, ()))
        with self._uow:
            if frozen:
                variant_id = self._materials.put(
                    project_id=project_id,
                    parsed=parsed,
                    sha256=_reparse_key(material_id, parsed),
                    content=raw,
                    now=self._clock.now(),
                ).material_id
                # 变体是**另一份材料**：新 id 提交后补它的向量，旧的留着（冻结记录）。
            else:
                # 就地换块 = 块 id 与正文都可能变：旧向量作废，**先删向量再换块**
                # （反过来块的旧行已没，`drop()` 删了个空），提交后再重算。
                self._vectors.drop(material_id)
                self._materials.replace_chunks(material_id, parsed)
        # 推理一律在主事务之外：变体按新 id 补，就地按原 id 重算。
        self._vectors.write(variant_id if frozen else material_id)
        return list_material_views(
            project_id=project_id,
            materials=self._materials,
            parser=self._parser,
            index=self._index,
        )


def _reparse_key(material_id: str, parsed: ParsedMaterial) -> str:
    """重解析出来的那份的**去重键**：`reparse:{旧id}|{parser}@{version}`。

    为什么要变体键：`materials` 上有 `UNIQUE (project_id, sha256)`，原始字节的键被**旧的那份**占着；
    而变体键同时给了幂等（同一个旧材料 + 同一个解析器 → 同一个键 → 不会再存一份）。
    """

    return hashlib.sha256(
        f"reparse:{material_id}|{parsed.parser_id}@{parsed.parser_version}".encode()
    ).hexdigest()


class SearchProject:
    """在这个项目里搜（F6）：文档条目 / 功能 / 出处编号 / 最近几轮的席位发言。

    只读三处：文档聚合、账本、轮次索引投影。**没有写端口**，所以它改不了任何东西；
    也**不建索引表**——现查现算（`02` §11.0.10：跨轮的真相源只有文档与轮次）。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        evidence: EvidenceReader,
        index: WorkspaceIndex,
        materials: MaterialRetriever,
    ) -> None:
        self._documents = documents
        self._evidence = evidence
        self._index = index
        self._materials = materials

    async def __call__(self, *, project_id: ProjectId, query: str) -> SearchResultsView:
        document = self._documents.get(project_id).view()
        ledger = self._evidence.get(project_id)
        # 空查询连检索器都不问（省一次扫表；`search_hits` 那边也会早早回空）
        material_hits = (
            ()
            if query.strip() == ""
            else self._materials.search(
                project_id=project_id, query=query, limit=SEARCH_LIMIT
            )
        )
        results = search_hits(
            query,
            document=document,
            evidence=tuple((str(item.eid), item.text) for item in ledger.evidences()),
            opinions=self._index.recent_opinions(project_id),
            materials=material_hits,
        )
        # **检索观测不在这里**：分解（词面/语义各出了多少候选、纯词面命中几个）
        # 只有检索器知道 → 观测行挪到了 `SqliteHybridRetriever.search`
        # （2026-10-22 批 13，R8 扩到每次检索 + R9）。
        return results


class ListRounds:
    """这个项目走过的轮次（F4 + "上一轮的主张"）。

    只读：只注入 `WorkspaceIndex`（查询投影），拿不到任何写端口——所以它不可能改东西。
    跨轮的真相源仍是轮次表本身，这里只读 payload 的头部几个键。
    """

    def __init__(self, *, index: WorkspaceIndex) -> None:
        self._index = index

    async def __call__(
        self, *, project_id: ProjectId, limit: int = 20
    ) -> tuple[RoundHeadView, ...]:
        return self._index.list_rounds(project_id, limit=limit)


class GetSchema:
    """现行结构：13 章（含说明与槽）、功能字段表、三处上限、三个讨论角色。

    纯函数、不注入任何仓储——"读 schema 不可能开事务"是结构上的保证，不靠纪律
    （与只读用例同源：它们拿不到写端口）。

    `discussion_roles` 与 `personas` 都是**构造时注入的元组**（不是仓储）：
    两张表随进程加载一次，读 schema 只是把它们摆出来，所以这里依然拿不到任何 IO 端口。
    `templates` 同理：库表在装配时读一次（模板库只增不改，读时快照足够）。
    """

    def __init__(
        self,
        *,
        discussion_roles: tuple = (),
        personas: tuple = (),
        templates: tuple = (),
        tools: ToolCatalog | None = None,
        available_tool_keys: frozenset[str] = frozenset(),
    ) -> None:
        self._discussion_roles = discussion_roles
        self._personas = personas
        self._templates = templates
        # 工具目录不是仓储：只回答"这个进程里联网能不能用、名额几条"。
        self._tools = tools
        self._available_tool_keys = available_tool_keys

    async def __call__(self) -> SchemaView:
        return schema_view(
            discussion_roles=self._discussion_roles,
            personas=self._personas,
            templates=self._templates,
            max_web_executions=(
                0
                if self._tools is None
                else web_execution_limit(
                    self._tools, keys=self._available_tool_keys
                )
            ),
        )





class ExportMarkdown:
    """交付物：纯读 + 纯渲染。它是"能不能交出去"这句话的落地形态（`01` §7.3）。"""

    def __init__(
        self,
        *,
        documents: DocumentReader,
        evidence: EvidenceReader,
        checker: ReadinessChecker,
        clock: Clock,
    ) -> None:
        self._documents = documents
        self._evidence = evidence
        self._checker = checker
        self._clock = clock

    async def __call__(self, *, project_id: ProjectId) -> Deliverable:
        document = self._documents.get(project_id)
        view = document.view()
        return render_markdown(
            view=view,
            readiness=self._checker.check(view, adapter=adapt(document.template)),
            versions=document.versions,
            ledger=self._evidence.get(project_id),
            exported_at=self._clock.now(),
            adapter=adapt(document.template),
        )


class ListProjects:
    def __init__(self, *, index: WorkspaceIndex) -> None:
        self._index = index

    async def __call__(self) -> tuple[ProjectSummaryView, ...]:
        return self._index.list_summaries()


class GetVersions:
    """列版本。读聚合里的 `versions`（快照也读出来；单项目几百版以内没问题）。"""

    def __init__(self, *, documents: DocumentReader) -> None:
        self._documents = documents

    async def __call__(self, *, project_id: ProjectId) -> VersionListView:
        document: Document = self._documents.get(project_id)
        return VersionListView(
            versions=tuple(version_summary_view(item) for item in document.versions),
            current=document.current_version.version_no,
        )


class GetVersionDiff:
    """两个版本的差异：纯函数算（`domain/authoring/diff.py`），这一层只负责取两个版本。"""

    def __init__(self, *, documents: DocumentReader) -> None:
        self._documents = documents

    async def __call__(
        self, *, project_id: ProjectId, from_version_no: int, to_version_no: int
    ) -> DiffView:
        document: Document = self._documents.get(project_id)
        # 版本号不存在 → UnknownVersion → 404（api/errors.py 的映射）
        before = document.version(from_version_no)
        after = document.version(to_version_no)
        return diff_view(before, after, adapter=adapt(document.template))


class RollbackVersion:
    """回到某一版：写入只经过 `ProposalCommitter`，写完推 `doc.changed` 让界面重读。"""

    def __init__(
        self,
        *,
        committer: ProposalCommitter,
        documents: DocumentReader,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._committer = committer
        self._documents = documents
        self._clock = clock
        self._events = events

    async def __call__(self, *, project_id: ProjectId, version_no: int) -> RollbackResult:
        result: CommitResult = self._committer.rollback_to(
            project_id=project_id,
            version_no=version_no,
            now=self._clock.now(),
            summary=f"回到 v{version_no}",
        )
        view = self._documents.get(project_id).view()
        self._events.publish(
            Event(
                name=DOC_CHANGED,
                project_id=str(project_id),
                payload={
                    "version_no": result.version.version_no,
                    "written_block_ids": [str(item) for item in result.written_block_ids],
                    "summary": result.version.summary,
                },
                occurred_at=self._clock.now(),
            )
        )
        return RollbackResult(version_no=result.version.version_no, document=view)


class GetEvidence:
    """按编号查处方：原话、来路（哪一轮 / 哪个席位 / 哪道题）、链条、以及"现在被谁用着"。

    只读：账本 + 文档投影。单项目几千条以内是 O(n) 扫描，不建索引（索引层是另一件事）。
    """

    def __init__(self, *, documents: DocumentReader, evidence: EvidenceReader) -> None:
        self._documents = documents
        self._evidence = evidence

    async def __call__(self, *, project_id: ProjectId, eid: str) -> EvidenceLookupView:
        ledger = self._evidence.get(project_id)
        evidence = next((item for item in ledger.evidences() if str(item.eid) == eid), None)
        if evidence is None:
            raise UnknownEvidenceId(eid)
        view = self._documents.get(project_id).view()
        return evidence_lookup_view(
            evidence,
            chain=ledger.chain(evidence.eid),
            used_by=used_by_entries(view, evidence.eid),
        )


class AbandonRound:
    """放弃这一轮（`01` F15）：先写状态，再取消正在跑的任务。

    顺序不能反：先取消的话，取消回来的那一刻状态还是"在跑"，界面会闪一下旧状态；
    而且**写法只有这一处**——取消本身不写任何业务状态，它只让模型调用停下来。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
        canceller: RoundCanceller,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._uow = uow
        self._clock = clock
        self._events = events
        self._canceller = canceller
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    async def __call__(
        self,
        *,
        round_id: RoundId,
        reason: str = "用户放弃了这一轮",
        owner: UserId | None = None,
    ) -> RoundView:
        if self._leases is not None and owner is None:
            raise ApplicationError(
                "放弃轮次缺少 owner 上下文",
                code="missing_owner_scope",
            )
        scope_token = None
        token = None
        try:
            with self._uow:
                if self._leases is not None:
                    holder = f"abandon:{uuid.uuid4().hex}"
                    token = self._leases.force_acquire(
                        LeaseKind.ROUND,
                        str(round_id),
                        owner,
                        holder,
                        wall_clock_ms(),
                        self._lease_ttl_ms,
                    )
                    scope = LeaseScope(holder=holder, owner=owner)
                    scope.bind(token)
                    scope_token = set_lease_scope(scope)
                round_ = self._rounds.get(round_id)
                round_.abandon(reason)
                self._round_writer.save(round_)
                if self._leases is not None and token is not None:
                    # 同一 UoW 内释放：提交前别人看不到"无租约"的中间态。
                    self._leases.release(
                        token.kind,
                        token.target_id,
                        UserId(token.user_id),
                        token.holder,
                        token.token,
                    )
        finally:
            if scope_token is not None:
                clear_lease_scope(scope_token)
        _publish(self._events, self._clock, round_)

        # 写完了再取消：取消会把那个任务炸在 await 点，而它不会碰业务状态。
        await self._canceller.cancel(round_id)
        return round_view(self._rounds.get(round_id))


class GetRound:
    def __init__(
        self,
        *,
        rounds: RoundReader,
        evidence: EvidenceReader,
        catalog: SeatCatalog,
        tools: ToolCatalog,
        available_tool_keys: frozenset[str],
        materials: MaterialStore,
    ) -> None:
        self._rounds = rounds
        self._evidence = evidence
        self._catalog = catalog
        self._tools = tools
        self._available_tool_keys = available_tool_keys
        self._materials = materials

    async def __call__(self, *, round_id: RoundId) -> RoundView:
        titles = {seat.seat_id: seat.title for seat in self._catalog.all()}
        round_ = self._rounds.get(round_id)
        web_tool = web_search_tool(self._tools, keys=self._available_tool_keys)
        return round_view(
            round_,
            seat_titles=titles,
            catalog=self._catalog,
            ledger=self._evidence.get(round_.project_id),
            materials=_materials_of(round_, store=self._materials),
            web_search_available=web_tool is not None,
            web_search_network_access=False if web_tool is None else web_tool.network_access,
        )


class GetRoundCalls:
    """这一轮的外部调用轨迹（模型 + 工具，时间序）——单轮读的现成形状的延伸。

    归属校验**先**走 `rounds.get(round_id)`：别人的轮次自然 404，与 `GetRound` 同一条路。
    """

    def __init__(self, *, rounds: RoundReader, call_log: RoundCallLog) -> None:
        self._rounds = rounds
        self._call_log = call_log

    async def __call__(self, *, round_id: RoundId) -> RoundCallsView:
        round_ = self._rounds.get(round_id)
        entries = self._call_log.round_calls(str(round_.round_id))
        return RoundCallsView(
            round_id=str(round_.round_id),
            calls=entries,
            model_calls=sum(1 for entry in entries if entry.kind == "model"),
            model_failed=sum(
                1 for entry in entries if entry.kind == "model" and not entry.ok
            ),
            tool_calls=sum(1 for entry in entries if entry.kind == "tool"),
            tool_failed=sum(1 for entry in entries if entry.kind == "tool" and not entry.ok),
        )


class SkipSeat:
    """用户手动跳过一个还没交回的席位（`02` §11.0.67 用户手动跳过）。

    abandon 式直调，**不走 `runner.run()`**——走 runner 会被 `RoundBusy` 拦下，
    而"跑中点跳过"撞上"跑中锁"正是这个用例本身，409 等于功能不存在。

    并发正确性（证明写在 `tests/application/test_skip_seat.py` 模块文档串）：
    读验写在同一 uow 内做 fresh-read，SQLite `BEGIN IMMEDIATE` 写串行保证
    后提交者看到先提交者的状态——交回先提交则这里拒（`SeatAlreadyReviewed`），
    跳过先提交则迟交在 `_review_one` 汇合处被丢弃。两序都闭合，无 TOCTOU。

    回包是**全量 view**（经组合的 `GetRound`，与 graph runner 同款）：
    前端直接拿动作回包渲染（`useWorkspaceData.abandon` 体例，无二次重读），
    裸 view 的包标题会退化成 raw id——那正是这次要显示"已跳过"的那一行。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        uow: UnitOfWork,
        events: EventPublisher,
        clock: Clock,
        viewer: GetRound,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._uow = uow
        self._events = events
        self._clock = clock
        self._viewer = viewer
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    async def __call__(
        self, *, round_id: RoundId, seat_id: str, owner: UserId | None = None
    ) -> RoundView:
        if self._leases is not None and owner is None:
            raise ApplicationError(
                "跳过席位缺少 owner 上下文",
                code="missing_owner_scope",
            )
        scope_token = None
        forced = None
        try:
            if self._leases is not None:
                # 跑中跳过**不抢 action lease**：借用当前持有者的 token 写这一条控制信息。
                # 没有活租约（进程崩过 / 停在 seats_running）才接管一把，写完立刻释放。
                token = self._leases.current_token(
                    LeaseKind.ROUND, str(round_id), owner, now_ms=wall_clock_ms()
                )
                if token is None:
                    forced = self._leases.force_acquire(
                        LeaseKind.ROUND,
                        str(round_id),
                        owner,
                        f"skip:{uuid.uuid4().hex}",
                        wall_clock_ms(),
                        self._lease_ttl_ms,
                    )
                    token = forced
                scope = LeaseScope()
                scope.bind(token)
                scope_token = set_lease_scope(scope)
            with self._uow:
                round_ = self._rounds.get(round_id)
                if seat_id in round_.skipped_seats:
                    # 幂等：已经跳过，直接返回 view——`note_dropped` 是追加语义，
                    # 不 guard 会多记一条留痕。
                    pass
                else:
                    if any(
                        review.seat_id == seat_id
                        for review in round_.current_reviews
                    ):
                        # 本趟已交回：拒。不是 UX 选项，是正确性必需（见类文档串）。
                        raise SeatAlreadyReviewed(
                            f"席位 {seat_id} 本趟已经交回，跳过没有意义",
                            seat_id=seat_id,
                        )
                    # 非成员 → UnknownSeat；非跑中 → IllegalStepTransition——都复用，一字不动。
                    round_.skip_seat(seat_id)
                    round_.note_dropped(
                        (
                            DroppedItem(
                                handle=seat_id,
                                reason=f"用户跳过了席位 {seat_id}，本轮不再请它",
                                source=DropSource.USER_SKIPPED,
                                seat_id=seat_id,
                                pass_index=round_.seat_round_count,
                                packet_id=None,
                            ),
                        )
                    )
                    self._round_writer.save(round_)
        finally:
            if forced is not None:
                # 接管来的锁只活到这次控制写结束；借用的锁**绝不释放**（那是长动作的）。
                self._leases.release(
                    forced.kind,
                    forced.target_id,
                    UserId(forced.user_id),
                    forced.holder,
                    forced.token,
                )
            if scope_token is not None:
                clear_lease_scope(scope_token)
        _publish(self._events, self._clock, round_)
        return await self._viewer(round_id=round_id)
class GetDocument:
    def __init__(self, *, documents: DocumentReader) -> None:
        self._documents = documents

    async def __call__(self, *, project_id: ProjectId) -> DocumentView:
        return self._documents.get(project_id).view()


def dry_run(
    document: Document,
    proposals: Sequence[Proposal],
    *,
    round_: Round,
    now: datetime,
) -> tuple[str, ...]:
    """在内存深拷贝上跑一次 apply：预校验 + 算出"写入后还剩哪些必填缺口"。

    真是唯一真相源：干跑用的是 `Document.apply` 本尊，不是第二套规则。

    返回的头一项是**补齐度**（有功能树时）：用户点确认前先看见"离可交付还差多少"（F10）；
    后面才是逐字段的"仍然为空"。
    """

    probe = deepcopy(document)
    if proposals:
        probe.apply(
            tuple(to_write_instruction(proposal) for proposal in proposals),
            now=now,
            summary="干跑：不落库",
            authorized_new_features=round_.authorized_new_features,
        )
    gaps = tuple(f"{gap.feature_slug}·{gap.title} 仍然为空" for gap in probe.gaps())
    progress = _feature_progress(probe)
    return gaps if progress is None else (progress, *gaps)


def _feature_progress(probe: Document) -> str | None:
    """草案卡上的补齐度：**必填**功能字段填了几个（F10）。纯投影，不改任何东西。

    口径与就绪度同一本账（`REQUIRED_FEATURE_FIELDS`，6 个）：`gaps()` 数出来的就是它们的缺口，
    所以"已填 = 总数 − 缺口数"天然对得上，不需要第二套算法。
    没有功能树时返回 `None`——空文档报一句 `0/0` 只会让人以为出了什么大事。
    """

    features = probe.features()
    if not features:
        return None
    total = len(features) * len(REQUIRED_FEATURE_FIELDS)
    missing = len(probe.gaps())
    return f"s5 已填 {total - missing}/{total} 个必填字段（还有 {missing} 个空着）"


def _system_defaults(
    round_: Round, *, ledger: EvidenceLedger, now: datetime
) -> dict[QuestionId, EvidenceId]:
    """给跳过的题补一条 system_default 出处（编号在账本里，proposal 才有东西可引用）。

    文本用**出题那一批**的题面（`questionnaire_history`）：拿当前批反查会把上一批的跳过题
    静默丢掉，或者写上另一道题的题面（计划 §0.1 第 4 处）。
    """

    questionnaire = round_.questionnaire
    if questionnaire is None:
        return {}
    by_key = {
        (str(form.form_id), str(question.question_id)): question
        for form in round_.questionnaire_history
        for question in form.questions
    }
    defaults: dict[QuestionId, EvidenceId] = {}
    for answer_set in round_.answer_sets:
        for question_id in answer_set.skipped:
            question = by_key.get((str(answer_set.form_id), str(question_id)))
            if question is None or question_id in defaults:
                continue
            defaults[question_id] = ledger.record(
                kind=EvidenceKind.SYSTEM_DEFAULT,
                text=f"{question.title}（跳过，未决定）",
                source=EvidenceSource(
                    round_id=round_.round_id,
                    rationale="跳过的题按题面写明的后果落成开放问题",
                ),
                now=now,
            )
    return defaults


def external_material_text(
    round_: Round, *, store: MaterialStore, query: str = ""
) -> str:
    """把这一轮带进来的材料渲染成提示词那一段（三个智能体共用同一个渲染器）。

    没有任何材料时返回 `context.NO_MATERIAL_TEXT`——**不留空节**（空节会让模型以为材料被省略了）。

    上传材料与联网检索**分两段渲染**（排面与预算都不一样）：前者一段一份材料、块级编号；
    后者一条一段（编号 + 标题 + URL + 摘要）。两段都有就都带上，各说各的。

    `query` 非空时按它选块到 `MATERIAL_BUDGET`（模拟调研/评测用：主链路已经有
    `RoundContextPreparer` 的每步投影；这两个旁路只需要"别把整池塞进提示词"）。
    不传 query 也**会**按文档序选块到预算（Analyzer 的 SEED 材料也可能很长）；
    差别只在排序：有 query 走相关粗排，没 query 走文档序。
    """

    by_material: dict[str, dict[str, EvidenceId]] = {}
    for ref in round_.materials:
        by_material.setdefault(ref.material_id, {})[ref.chunk_id] = ref.eid
    uploads: list[tuple[MaterialPacket, dict[str, EvidenceId]]] = []
    found: list[tuple[MaterialPacket, list[MaterialChunk], EvidenceId]] = []
    missing: list[str] = []
    selected_web = (
        None
        if round_.web_selection is None
        else frozenset(round_.web_selection)
    )
    for material_id, eids in by_material.items():
        packet = store.get(material_id)
        if packet is None or packet.project_id != str(round_.project_id):
            # 材料不在了 / 串项目：不编内容，但要说清少了几份
            missing.append(material_id)
            continue
        if packet.parsed.origin == WEB_ORIGIN:
            # 联网结果：正常口径是**一条结果 = 一个编号**（多段共享它），渲染时把
            # **这一条结果被选中的每一段**都拼出来。`eids` 是 {块 id: 编号}。
            # 旧脏数据可能一块一个号：此时固定取**文档序第一个选中块**的编号，
            # 与 `RoundContextPreparer` 同一条选择规则（M1）。
            # 新 payload 还受本步 `web_selection` 约束：没选中的旧块只当历史留着。
            picked = [
                chunk
                for chunk in packet.parsed.chunks
                if chunk.chunk_id in eids
                and (
                    selected_web is None
                    or (material_id, chunk.chunk_id) in selected_web
                )
            ]
            if picked and eids:
                found.append((packet, picked, eids[picked[0].chunk_id]))
        else:
            uploads.append((packet, eids))

    selected_uploads, selected_count, pool_count = _select_uploads(
        uploads, query=query
    )
    parts: list[str] = []
    if uploads:
        if selected_count:
            parts.append(
                material_header(
                    selected_count,
                    pool_count,
                    stage="按主题" if query.strip() else "本轮",
                )
            )
            parts.append(render_materials(selected_uploads).text)
        else:
            # 有材料但一块都没装下：说清楚，别写"这一轮没有外部材料"。
            parts.append(f"（材料池里有 {pool_count} 块；本步没有选中）")
    if found:
        parts.append(render_web_results(found).text)
    if missing:
        parts.append(
            missing_line(len(missing), missing[:3], more=len(missing) > 3)
        )
    # **被预算挤掉的材料也要说给模型听**（2026-10-21 批 11）：不写这一句，
    # 模型会以为自己看到的就是全部材料 → 回答里会说"材料里没有相关内容"，
    # 而那份材料其实在库里（只是预算没带进来）。
    # 一条 drop 可能代表多份材料（StartRound 的 `missed` 是聚合留痕），
    # 所以按"手勾了但一块都没进"的材料数说，不按 drop 条目数说。
    selected_materials = {ref.material_id for ref in round_.materials}
    missed_materials = [
        material_id
        for material_id in dict.fromkeys(round_.pinned_materials)
        if material_id not in selected_materials and material_id not in missing
    ]
    if missed_materials:
        parts.append(budget_drop_line(len(missed_materials)))
    return "\n\n".join(parts) if parts else NO_MATERIAL_TEXT


def _select_uploads(
    uploads: Sequence[tuple[MaterialPacket, dict[str, EvidenceId]]],
    *,
    query: str,
) -> tuple[list[tuple[MaterialPacket, dict[str, EvidenceId]]], int, int]:
    """把这一轮的材料按预算选一遍（三条调用路径共用：Analyzer / 模拟 / 未来旁路）。

    返回 `(selected_entries, selected_chunks, pool_chunks)`。渲染用**原始 packet**
    （好让"共 M 块"说的是整份材料）；只有**有编号的块**参赛，但成本按完整 packet
    算——PDF 的父块会把整页兄弟块一起渲染，先裁包再算成本会让预算失真。
    `query=""` 时按文档序取（Analyzer 那条路）。
    """

    pool_count = sum(len(eids) for _, eids in uploads)
    candidates = {
        packet.material_id: frozenset(eids) for packet, eids in uploads
    }
    picked, _ = select_chunks(
        [packet for packet, _ in uploads],
        query=query,
        budget=MATERIAL_BUDGET,
        section_overhead=SECTION_OVERHEAD,
        candidates=candidates,
    )
    eids_by_material = {packet.material_id: eids for packet, eids in uploads}
    selected: dict[str, dict[str, EvidenceId]] = {}
    for material_id, chunk in picked:
        eid = eids_by_material[material_id].get(chunk.chunk_id)
        if eid is not None:
            selected.setdefault(material_id, {})[chunk.chunk_id] = eid
    entries = [
        (packet, selected[packet.material_id])
        for packet, _ in uploads
        if packet.material_id in selected
    ]
    return entries, sum(len(eids) for _, eids in entries), pool_count


def _materials_of(round_: Round, *, store: MaterialStore) -> tuple[MaterialPacket, ...]:
    """这一轮引用过的材料包（按引用顺序，去重）——给 `RoundView.materials` 用。"""

    seen: dict[str, MaterialPacket] = {}
    for ref in round_.materials:
        if ref.material_id in seen:
            continue
        packet = store.get(ref.material_id)
        if packet is not None:
            seen[ref.material_id] = packet
    return tuple(seen.values())


def authorize_named_features(
    round_: Round, claims: Sequence[Claim], *, document: Document
) -> None:
    """用户原话点名的功能才算授权（D3 的第一条路径，见计划 §3.4）。"""

    existing = {block.feature_slug for block in document.features()}
    for claim in claims:
        slug = claim.target.feature_slug
        if slug is None or slug in existing or slug in round_.authorized_new_features:
            continue
        if claim.eid is None:
            raise ShapeError("主张还没有编号，谈不上授权", claim_id=claim.claim_id)
        round_.authorize_new_feature(slug, basis=claim.eid)


def _apply_claim_edits(round_: Round, edits: Sequence[ClaimEdit]) -> tuple[Claim, ...]:
    edits_by_id = {edit.claim_id: edit for edit in edits}
    claims: list[Claim] = []
    for claim in round_.claims:
        edit = edits_by_id.pop(claim.claim_id, None)
        if edit is None:
            claims.append(claim)
            continue
        try:
            label = ClaimLabel(edit.label)
        except ValueError as error:
            raise ShapeError(f"未知标签：{edit.label}", claim_id=claim.claim_id) from error
        claims.append(replace(claim, label=label, target=edit.target))
    if edits_by_id:
        raise ShapeError("纠正里出现了不存在的 claim_id", claim_ids=sorted(edits_by_id))
    return tuple(claims)


def _publish(
    events: EventPublisher,
    clock: Clock,
    round_: Round,
    *,
    name: str = ROUND_UPDATED,
    payload: dict[str, object] | None = None,
) -> None:
    events.publish(
        Event(
            name=name,
            project_id=str(round_.project_id),
            round_id=str(round_.round_id),
            payload=payload
            or {
                "step": str(round_.step),
                "waiting_on": str(round_.waiting_on),
                "seat_round_count": round_.seat_round_count,
            },
            occurred_at=clock.now(),
        )
    )


def fail_round(
    *,
    rounds: RoundReader,
    round_writer: RoundWriter,
    uow: UnitOfWork,
    events: EventPublisher,
    clock: Clock,
    round_id: RoundId,
    reason: str,
    hint: str | None = None,
) -> None:
    """整轮失败：记原因、发 generation.failed。**不改文档、不落半截意见。**

    `hint` 只有**报文类**失败才传（P13 C1）：重试时它会变成第一次尝试的 `validation_error`。
    """

    with uow:
        round_ = rounds.get(round_id)
        round_.fail(reason, hint=hint)
        round_writer.save(round_)
    events.publish(
        Event(
            name=GENERATION_FAILED,
            project_id=str(round_.project_id),
            round_id=str(round_id),
            payload={"reason": reason, "at_step": str(round_.failure.at_step)},
            occurred_at=clock.now(),
        )
    )


# ── 探索：调研（M8）────────────────────────────────────────────────────────────

#: 调研报文两段式上限（重装 1 次，与 claims / 席位同一条口径）
MAX_INQUIRY_ATTEMPTS = 2
#: 模拟调研同款两段式（包不合格重装 1 次；条目级违规走丢条，不整包重装）
MAX_SIM_INQUIRY_ATTEMPTS = 2
#: R8：一轮最多跑 2 次模拟调研（显式点击才花调用，到顶就停，不排队）
MAX_SIM_INQUIRIES_PER_ROUND = 2
#: 过一次目台最多回几趟探索（列表投影的截断值；这不是"存了多少"，是"给多少"）
MAX_EXPLORATION_LIST = 20


def _publish_exploration(
    events: EventPublisher, clock: Clock, exploration: Exploration
) -> None:
    events.publish(
        Event(
            name=EXPLORATION_UPDATED,
            project_id=str(exploration.project_id),
            payload={
                "exploration_id": str(exploration.exploration_id),
                "status": str(exploration.status),
            },
            occurred_at=clock.now(),
        )
    )


def _candidate_of(exploration: Exploration, candidate_id: str):  # type: ignore[no-untyped-def]
    for item in exploration.candidates():
        if item.candidate_id == candidate_id:
            return item
    raise ShapeError(f"这一趟没有这条候选：{candidate_id}", candidate_id=candidate_id)


class RunInquiry:
    """发起并跑完一趟调研（M8 唯一的探索能力）。

    两段式（与 `StartRound` 同款）：`create` 只碰数据库、中间没有 await
    （所以在事件循环里是原子的——忙碌检查不会被两次并发请求同时穿过）；
    `execute` 才调模型（事务外），两次都过不了校验就落 `FAILED` 并返回失败视图。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        explorations: ExplorationReader,
        exploration_writer: ExplorationWriter,
        evidence: EvidenceReader,
        memory: MemoryReader,
        uow: UnitOfWork,
        inquirer: Inquirer,
        checker: ReadinessChecker,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._documents = documents
        self._explorations = explorations
        self._exploration_writer = exploration_writer
        self._evidence = evidence
        self._memory = memory
        self._uow = uow
        self._inquirer = inquirer
        self._checker = checker
        self._clock = clock
        self._ids = ids
        self._events = events
        # 生产路径：INSERT 与 acquire 同一个 UoW（`create` 里）；不注入时只做旧行为。
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    async def create(
        self,
        *,
        project_id: ProjectId,
        scope: ExplorationScope,
        owner: UserId | None = None,
    ) -> StartedExploration:
        document = self._documents.get(project_id)
        view = document.view()
        if not document_has_content(view):
            raise ShapeError("文档还是空的：先倒一段想法（或等第一轮写完），再来调研")
        if scope.kind is ScopeKind.ENTRY:
            entry_focus(view, scope.ref or "")
        exploration_id = ExplorationId(self._ids.new("x"))
        with self._uow:
            exploration = Exploration.start(
                exploration_id=exploration_id,
                project_id=project_id,
                scope=scope,
                now=self._clock.now(),
            )
            self._exploration_writer.save(exploration)
            # 同一 UoW：INSERT 与租约一起提交。token 从 1 开始；被占用时
            # `acquire_exploration_lease` 抛 `ExplorationBusy`，整笔回滚。
            token = acquire_exploration_lease(
                leases=self._leases,
                project_id=project_id,
                exploration_id=exploration_id,
                owner=owner,
                holder=f"exploration:{uuid.uuid4().hex}",
                ttl_ms=self._lease_ttl_ms,
            )
        _publish_exploration(self._events, self._clock, exploration)
        return StartedExploration(exploration_id=exploration_id, token=token)

    async def clear_stale(
        self, project_id: ProjectId, *, owner: UserId | None = None
    ) -> None:
        """把"挂着 running 但进程里没有任务"的僵壳就地判放弃（进程崩过）。

        只有 runner 确认过内存里没有这趟任务才能调它——否则会把真在跑的探索杀掉。
        判据是**行上的租约**：活租约（别的 worker 正在跑）一律不动；过期或老库
        `lease_expires_at IS NULL` 的才清。查询走 `list_running`（不截断 20 条），
        每条都在自己的 UoW 里重新读状态并对租约做条件复核。
        """

        for item in self._explorations.list_running(project_id, owner=owner):
            scope_token = None
            token = None
            try:
                with self._uow:
                    stored = self._explorations.get(item.exploration_id)
                    if stored.status is not ExplorationStatus.RUNNING:
                        continue  # 另一个 worker 已经清过/跑完了
                    if self._leases is not None:
                        if owner is None:
                            raise ApplicationError(
                                "清理探索僵壳缺少 owner 上下文",
                                code="missing_owner_scope",
                            )
                        holder = f"stale:{uuid.uuid4().hex}"
                        # 条件复核：只接管**过期/无过期时间**的行；活租约（另一个
                        # worker 正在跑）直接跳过——不把它翻成 409，让 create/resume
                        # 在唯一索引上给出真正的原因。
                        token = self._leases.acquire(
                            LeaseKind.EXPLORATION,
                            str(item.exploration_id),
                            owner,
                            holder,
                            wall_clock_ms(),
                            self._lease_ttl_ms,
                        )
                        if token is None:
                            continue
                        scope = LeaseScope(holder=holder, owner=owner)
                        scope.bind(token)
                        scope_token = set_lease_scope(scope)
                    stored.abandon("上一次运行没跑完（进程可能重启过）")
                    self._exploration_writer.save(stored)
                    if token is not None:
                        # 同一 UoW 内释放：提交前别人看不到"无租约"的中间态。
                        self._leases.release(
                            token.kind,
                            token.target_id,
                            UserId(token.user_id),
                            token.holder,
                            token.token,
                        )
            finally:
                if scope_token is not None:
                    clear_lease_scope(scope_token)
            _publish_exploration(self._events, self._clock, stored)

    async def execute(
        self, exploration_id: ExplorationId, *, retry_hint: str | None = None
    ) -> ExplorationView:
        exploration = self._explorations.get(exploration_id)
        document = self._documents.get(exploration.project_id)
        view = document.view()
        focus: str | None = None
        if exploration.scope.kind is ScopeKind.ENTRY:
            try:
                focus = entry_focus(view, exploration.scope.ref or "")
            except UnknownEntryId:
                focus = None  # 条目在调研期间被删了：不装作看得见
        window = document_window(
            view, readiness=self._checker.check(view, adapter=adapt(document.template))
        )
        document_state = window.text
        memory_text = render_memory(self._memory.get(exploration.project_id).entries)
        scope_text = scope_line(exploration.scope, focus=focus)
        ledger = self._evidence.get(exploration.project_id)
        # **不变量**（`02` §11.0.61）：`known` = 这次提示词里看得见的编号，
        # 不是整个账本。假设的 `based_on` 只能引它真看见的那些。
        known_eids = window.known_eids(ledger) | (
            visible_eids(scope_text, memory_text) & window.ledger_ids(ledger)
        )

        repair = retry_hint or ""
        outcome = None
        last: Exception | None = None
        for attempt in range(1, MAX_INQUIRY_ATTEMPTS + 1):
            try:
                outcome = await self._inquirer.run(
                    scope=scope_text,
                    document_state=document_state,
                    project_memory=memory_text,
                    exploration_id=str(exploration_id),
                    known_eids=known_eids,
                    validation_error=repair,
                    packet_slot=f"a{attempt}",
                    # 最后一次尝试：不存在的编号摘掉、不判死整趟（`02` §11.0.2）；
                    # 上面几次仍抛错，给它重装自纠的机会。
                    lenient=attempt == MAX_INQUIRY_ATTEMPTS,
                )
                validate_inquiry(
                    outcome.hypotheses, outcome.blind_spots, known_eids=known_eids
                )
                break
            except ModelCallFailed as error:
                # 网关自己已经重试过 3 次：到这里就是这趟探索失败，落状态、可重跑
                return self._fail(exploration_id, f"模型调用失败：{error.message}")
            except (PacketParseError, ShapeError) as error:
                outcome = None
                last = error
                if attempt == MAX_INQUIRY_ATTEMPTS:
                    break
                repair = (
                    repair_note(error)
                    if isinstance(error, PacketParseError)
                    else f"上一次的产物没通过校验：{error.message}"
                )
        if outcome is None:
            reason = f"调研两次都过不了校验：{getattr(last, 'message', str(last))}"
            return self._fail(exploration_id, reason)
        with self._uow:
            stored = self._explorations.get(exploration_id)
            stored.complete(
                hypotheses=outcome.hypotheses,
                blind_spots=outcome.blind_spots,
                overflow_count=len(outcome.overflow),
            )
            self._exploration_writer.save(stored)
        _publish_exploration(self._events, self._clock, stored)
        return exploration_view(stored)

    def _fail(self, exploration_id: ExplorationId, reason: str) -> ExplorationView:
        with self._uow:
            stored = self._explorations.get(exploration_id)
            stored.fail(reason)
            self._exploration_writer.save(stored)
        _publish_exploration(self._events, self._clock, stored)
        return exploration_view(stored)


@dataclass(frozen=True, slots=True)
class ResumedExploration:
    """retry 的返回：失败原因（给下一次尝试当 `validation_error`）+ 已绑定的租约。"""

    hint: str | None
    token: LeaseToken | None


class RetryExploration:
    """把失败的一趟恢复成 RUNNING，并把失败原因交给重跑的第一次尝试当 `validation_error`。"""

    def __init__(
        self,
        *,
        explorations: ExplorationReader,
        exploration_writer: ExplorationWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._explorations = explorations
        self._exploration_writer = exploration_writer
        self._uow = uow
        self._clock = clock
        self._events = events
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    async def resume(
        self, exploration_id: ExplorationId, *, owner: UserId | None = None
    ) -> ResumedExploration:
        scope_token = None
        token = None
        try:
            with self._uow:
                stored = self._explorations.get(exploration_id)
                hint = stored.failure.reason if stored.failure is not None else None
                holder = f"retry:{uuid.uuid4().hex}"
                # 同一 UoW：置 RUNNING 之前先 acquire，避免"库里 running 但没人持锁"
                # 的窗口；唯一 running 冲突由仓储统一翻成 ExplorationBusy。
                token = acquire_exploration_lease(
                    leases=self._leases,
                    project_id=stored.project_id,
                    exploration_id=exploration_id,
                    owner=owner,
                    holder=holder,
                    ttl_ms=self._lease_ttl_ms,
                )
                if token is not None:
                    scope = LeaseScope(holder=holder, owner=owner)
                    scope.bind(token)
                    scope_token = set_lease_scope(scope)
                stored.resume()
                self._exploration_writer.save(stored)
        finally:
            if scope_token is not None:
                clear_lease_scope(scope_token)
        _publish_exploration(self._events, self._clock, stored)
        return ResumedExploration(hint=hint, token=token)


class AbandonExploration:
    """放弃这一趟：**先写状态、再取消**（`AbandonRound` 同一条顺序与理由）。

    放弃必须赢过在跑的 worker：同一 UoW 里 `force_acquire` 接管租约、写 ABANDONED、
    再释放；旧 worker 的下一次 save 被 token fence 掉。
    """

    def __init__(
        self,
        *,
        explorations: ExplorationReader,
        exploration_writer: ExplorationWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
        canceller: ExplorationCanceller,
        leases: LeaseStore | None = None,
        lease_ttl_ms: int = LEASE_TTL_SECONDS * 1000,
    ) -> None:
        self._explorations = explorations
        self._exploration_writer = exploration_writer
        self._uow = uow
        self._clock = clock
        self._events = events
        self._canceller = canceller
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms

    async def __call__(
        self, *, exploration_id: ExplorationId, owner: UserId | None = None
    ) -> ExplorationView:
        if self._leases is not None and owner is None:
            raise ApplicationError(
                "放弃探索缺少 owner 上下文",
                code="missing_owner_scope",
            )
        scope_token = None
        token = None
        try:
            with self._uow:
                if self._leases is not None:
                    holder = f"abandon-exploration:{uuid.uuid4().hex}"
                    token = self._leases.force_acquire(
                        LeaseKind.EXPLORATION,
                        str(exploration_id),
                        owner,
                        holder,
                        wall_clock_ms(),
                        self._lease_ttl_ms,
                    )
                    scope = LeaseScope(holder=holder, owner=owner)
                    scope.bind(token)
                    scope_token = set_lease_scope(scope)
                exploration = self._explorations.get(exploration_id)
                exploration.abandon("用户放弃了这一趟调研")
                self._exploration_writer.save(exploration)
                if token is not None:
                    self._leases.release(
                        token.kind,
                        token.target_id,
                        UserId(token.user_id),
                        token.holder,
                        token.token,
                    )
        finally:
            if scope_token is not None:
                clear_lease_scope(scope_token)
        _publish_exploration(self._events, self._clock, exploration)
        await self._canceller.cancel(exploration_id)
        return exploration_view(self._explorations.get(exploration_id))


class KeepCandidate:
    """收下一条候选：`seed` 只记状态；`material` 还会合成一份材料（变体键去重）。"""

    def __init__(
        self,
        *,
        explorations: ExplorationReader,
        exploration_writer: ExplorationWriter,
        materials: MaterialStore,
        uow: UnitOfWork,
        clock: Clock,
        inspiration_repo: InspirationRepository = NO_INSPIRATIONS,
    ) -> None:
        self._explorations = explorations
        self._exploration_writer = exploration_writer
        self._materials = materials
        self._uow = uow
        self._clock = clock
        self._inspiration_repo = inspiration_repo

    async def __call__(
        self, *, exploration_id: ExplorationId, candidate_id: str, as_: str
    ) -> ExplorationView:
        if as_ not in ("seed", "material"):
            raise ShapeError("收下的去向只能是 seed 或 material", as_=as_)
        exploration = self._explorations.get(exploration_id)
        candidate = _candidate_of(exploration, candidate_id)
        if as_ == "material":
            parsed, content = candidate_material(
                candidate, exploration_id=str(exploration_id)
            )
            with self._uow:
                packet = self._materials.put(
                    project_id=exploration.project_id,
                    parsed=parsed,
                    sha256=material_sha(parsed.source_ref),
                    content=content,
                    now=self._clock.now(),
                )
                stored = self._explorations.get(exploration_id)
                stored.keep(candidate_id, as_="material", material_id=packet.material_id)
                self._exploration_writer.save(stored)
            self._store_inspiration(exploration.project_id, candidate)
            return exploration_view(stored)
        with self._uow:
            stored = self._explorations.get(exploration_id)
            stored.keep(candidate_id, as_="seed")
            self._exploration_writer.save(stored)
        self._store_inspiration(exploration.project_id, candidate)
        return exploration_view(stored)

    def _store_inspiration(self, project_id, candidate) -> None:
        """L3: 收下候选后存入灵感档案。"""
        item = InspirationItem(
            item_id=f"ins-expl-{candidate.candidate_id}-{uuid.uuid4().hex[:6]}",
            project_id=str(project_id),
             origin=InspirationOrigin.EXPLORATION,
             statement=candidate.statement,
        )
        self._inspiration_repo.save(item)


class DismissCandidate:
    """删掉一条候选：终态——不进过目台、不回流（`02` 的"看不上就不流"）。"""

    def __init__(
        self,
        *,
        explorations: ExplorationReader,
        exploration_writer: ExplorationWriter,
        uow: UnitOfWork,
    ) -> None:
        self._explorations = explorations
        self._exploration_writer = exploration_writer
        self._uow = uow

    async def __call__(
        self, *, exploration_id: ExplorationId, candidate_id: str
    ) -> ExplorationView:
        with self._uow:
            stored = self._explorations.get(exploration_id)
            stored.dismiss(candidate_id)
            self._exploration_writer.save(stored)
        return exploration_view(stored)


class ListExplorations:
    """过目台与历史读的就是它：一次给全（≤上限），由界面投影出"待过目的那些"。"""

    def __init__(
        self, *, explorations: ExplorationReader, limit: int = MAX_EXPLORATION_LIST
    ) -> None:
        self._explorations = explorations
        self._limit = limit

    async def __call__(self, *, project_id: ProjectId) -> tuple[ExplorationView, ...]:
        return tuple(
            exploration_view(item)
            for item in self._explorations.list_for_project(
                project_id, limit=self._limit
            )
        )


# ── 讨论：自由主题的多轮灵感讨论（M9-T3）────────────────────────────────────

#: 一次给几条线程（列表投影的截断值；这不是"存了多少"，是"给多少"）
MAX_DISCUSSION_LIST = 20


def _publish_discussion(events: EventPublisher, clock: Clock, thread: Discussion) -> None:
    # 事件名 T5 抽成 DISCUSSION_UPDATED 常量；此处先用字面量，payload 与 exploration 同形。
    events.publish(
        Event(
            name="discussion.updated",
            project_id=str(thread.project_id),
            payload={
                "discussion_id": str(thread.discussion_id),
                "status": str(thread.status),
            },
            occurred_at=clock.now(),
        )
    )


def _discussion_role_of(
    role_id: str, *, catalog: SeatCatalog, discussion_roles: tuple
) -> tuple[str, str]:
    """`role_id` →（标题，focus）。`dlg.*` 走三固定，`seat.*` 走席位注册表。

    `discussion_roles` 只按鸭子类型读 `.role_id/.title/.focus`——application 不 import
    infrastructure（分层守卫），形状由 `DiscussionRole` 保证，测试直接构元组。
    """

    if role_id.startswith("dlg."):
        for role in discussion_roles:
            if role.role_id == role_id:
                return role.title, role.focus
        raise UnknownRole(role_id)
    if role_id.startswith("seat."):
        try:
            seat = catalog.by_id(role_id)
        except KeyError as error:
            raise UnknownRole(role_id) from error
        return seat.title, seat.focus
    raise UnknownRole(role_id)


def _render_thread_findings(
    refs: tuple[FindingRef, ...], *, explorations: ExplorationReader
) -> str:
    """按引用把开题底稿读回来渲染（M13）：找不到的不静默，附一行实话。"""

    if not refs:
        return ""
    cache: dict[str, Exploration | None] = {}
    items: list[Hypothesis | BlindSpot] = []
    missing = 0
    for ref in refs:
        if ref.exploration_id not in cache:
            try:
                cache[ref.exploration_id] = explorations.get(
                    ExplorationId(ref.exploration_id)
                )
            except UnknownExploration:
                cache[ref.exploration_id] = None
        exploration = cache[ref.exploration_id]
        if exploration is None:
            missing += 1
            continue
        found = next(
            (
                item
                for item in (*exploration.hypotheses, *exploration.blind_spots)
                if item.local_id == ref.local_id
            ),
            None,
        )
        if found is None:
            missing += 1
        else:
            items.append(found)
    return render_findings(items, missing=missing)


def _question_text(question: QuestionSnapshot | None) -> str:
    if question is None:
        return ""
    if question.why:
        return f"{question.title}\n为什么问：{question.why}"
    return question.title


def _card_of(thread: Discussion, card_id: str) -> Card:
    for card in thread.cards:
        if card.card_id == card_id:
            return card
    raise ShapeError(f"这条讨论没有这张卡片：{card_id}", card_id=card_id)


def _discussion_window(
    documents: DocumentReader,
    checker: ReadinessChecker,
    evidence: EvidenceReader,
    thread: Discussion,
    *,
    history: str,
) -> tuple[str, frozenset[EvidenceId], EvidenceLedger]:
    """讨论调用的公共上下文：文档窗口 + known_eids + ledger（三处共用，同源不漂移）。

    SayToThread / AssembleCards / RunPanelRound 共用——窗口（topic 窄化）与 known
    （窗口 ∩ 账本 ∪ 历史可见 ∩ 账本）必须同一套，否则同一线程两处校验口径漂移
    （`02` §11.0.61：出卡与发言同一窄化）。`history` 由调用方给：say 传含本轮 pending
    的那份，assemble/run 传渲染好的历史。
    """

    document = documents.get(thread.project_id)
    view = document.view()
    window = document_window(
        view, readiness=checker.check(view, adapter=adapt(document.template)), topic=thread.topic
    )
    ledger = evidence.get(thread.project_id)
    known_eids = window.known_eids(ledger) | (
        visible_eids(history) & window.ledger_ids(ledger)
    )
    return window.text, known_eids


class StartDiscussion:
    """开一条讨论线程：角色双查＋同（项目，归一化主题，角色集合）的 open 线程幂等返。

    M18 起收 `role_ids[1..4]`（panel）：单元素即旧 1:1 线程。开聊时所有成员都从第 1 轮在场；
    中途拉人走 `JoinPanel`（下一轮生效）。

    `finding_exploration_id`（M13「先跑一轮调研」）：把那一趟调研的**全部候选**快照成
    开题底稿（只存引用）；候选文本在说的时候读回来注入提示词。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        explorations: ExplorationReader,
        uow: UnitOfWork,
        clock: Clock,
        ids: IdGenerator,
        events: EventPublisher,
        catalog: SeatCatalog,
        discussion_roles: tuple,
    ) -> None:
        self._documents = documents
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._explorations = explorations
        self._uow = uow
        self._clock = clock
        self._ids = ids
        self._events = events
        self._catalog = catalog
        self._discussion_roles = discussion_roles

    def _finding_refs(
        self, project_id: ProjectId, exploration_id: str | None
    ) -> tuple[FindingRef, ...]:
        if exploration_id is None:
            return ()
        exploration = self._explorations.get(ExplorationId(exploration_id))
        if str(exploration.project_id) != str(project_id):
            raise ShapeError("这趟调研不属于这个项目")
        if exploration.status is not ExplorationStatus.DONE:
            raise ShapeError("这趟调研还没跑完，不能当开题底稿")
        return tuple(
            FindingRef(exploration_id=str(exploration.exploration_id), local_id=item.candidate_id)
            for item in exploration.candidates()
        )

    async def __call__(
        self,
        *,
        project_id: ProjectId,
        topic: str,
        role_ids: Sequence[str],
        question: QuestionSnapshot | None,
        finding_exploration_id: str | None = None,
    ) -> ThreadView:
        self._documents.get(project_id)  # 存在性；空文档不拒（与 M8 create 的接地判据差在这里）
        ids = [role_id.strip() for role_id in role_ids]
        if not ids or any(not role_id for role_id in ids):
            raise ShapeError("开聊必须指定角色")
        if len(set(ids)) != len(ids):
            raise ShapeError("同一个角色不能请两次")
        normalized = " ".join(topic.split())  # 与 _clip 同式，不新建函数
        if not normalized:
            raise ShapeError("开聊必须带主题")
        resolved = [
            (role_id, _discussion_role_of(
                role_id, catalog=self._catalog, discussion_roles=self._discussion_roles
            )[0])
            for role_id in ids
        ]
        findings = self._finding_refs(project_id, finding_exploration_id)
        wanted = frozenset(ids)
        for existing in self._discussions.list_for_project(
            project_id, limit=MAX_DISCUSSION_LIST
        ):
            if (
                existing.status is ThreadStatus.OPEN
                and " ".join(existing.topic.split()) == normalized
                and frozenset(member.role_id for member in existing.participants) == wanted
            ):
                # 幂等返已有线程：底稿不追挂（它属于"这次新开的线程"的起手式）
                return thread_view(existing)
        discussion_id = DiscussionId(self._ids.new("d"))
        with self._uow:
            thread = Discussion.start(
                discussion_id=discussion_id,
                project_id=project_id,
                topic=normalized,
                participants=tuple(
                    Participant(role_id=role_id, role_title=title, joined_round=1)
                    for role_id, title in resolved
                ),
                question=question,
                now=self._clock.now(),
                findings=findings,
            )
            self._discussion_writer.save(thread)
        _publish_discussion(self._events, self._clock, thread)
        return thread_view(thread)


class SayToThread:
    """追问一轮：模型跑完、校验通过才原子记一对；失败线程不动。

    线程带开题底稿（M13）时，按引用把候选文本读回来，渲染进提示词（**未经验证**，
    不是依据——硬规矩第 6 条管引用）。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        evidence: EvidenceReader,
        explorations: ExplorationReader,
        uow: UnitOfWork,
        discusser: Discusser,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
        catalog: SeatCatalog,
        discussion_roles: tuple,
    ) -> None:
        self._documents = documents
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._evidence = evidence
        self._explorations = explorations
        self._uow = uow
        self._discusser = discusser
        self._checker = checker
        self._clock = clock
        self._events = events
        self._catalog = catalog
        self._discussion_roles = discussion_roles

    async def __call__(
        self, *, discussion_id: DiscussionId, text: str
    ) -> ThreadView:
        if not text.strip():
            raise ShapeError("追问必须写清内容")
        thread = self._discussions.get(discussion_id)
        # 前置门与领域 say_pair 同一判据：不烧模型（烧了也写不进来）。
        if thread.status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=discussion_id)
        if len(thread.turns) + 2 > MAX_DISCUSSION_TURNS:
            raise ThreadFull(limit=MAX_DISCUSSION_TURNS)
        role_title, role_focus = _discussion_role_of(
            thread.role_id, catalog=self._catalog, discussion_roles=self._discussion_roles
        )
        base = len(thread.turns)
        # 本轮用户的话还没落库（模型先跑、跑通才原子记一对），但**它必须出现在这一趟的
        # 提示词里**——不然角色永远在答上一轮（2026-10-10 真跑抓到的 P0：第 2 次追问
        # 的提示词里只有第 1 次追问的原文）。这个 pending 与 `say_pair` 里构造的
        # 是同一个对象形状，所以直接交给 `render_thread_history`：窗口仍然封顶 6 条，
        # 本轮占一格，"共 N 轮"也算它（跑完就真的有这一轮）。
        pending = Turn(index=base, role="user", text=text, based_on=())
        history = render_thread_history((*thread.turns, pending))
        # 讨论是**有主题信号的**（`thread.topic` 就是发起时选中的那段原文），
        # 所以按它收窄文档——选区与条目入口发起的必然命中，自由开聊回落全文
        # （`02` §11.0.61：有信号才切，没信号不猜）。窗口与 known 走共用 helper，
        # 与 AssembleCards/RunPanelRound 同源（历史里上一轮引过的编号也看得见）。
        document_state, known_eids = _discussion_window(
            self._documents,
            self._checker,
            self._evidence,
            thread,
            history=history,
        )
        question = _question_text(thread.question)
        findings = _render_thread_findings(
            thread.findings, explorations=self._explorations
        )
        try:
            outcome = await self._discusser.say(
                role_title=role_title,
                role_focus=role_focus,
                topic=thread.topic,
                question=question,
                history=history,
                document_state=document_state,
                related_findings=findings,
                discussion_id=str(discussion_id),
                turn_slot=f"s{base}",
                known_eids=known_eids,
            )
            validate_ledger_refs(outcome.refs, known_eids=known_eids)
        except ModelCallFailed:
            # 网关已重试：直接抛，线程不动（照 RunInquiry.execute 同款）。
            raise
        except (PacketParseError, ShapeError) as error:
            repair = (
                repair_note(error)
                if isinstance(error, PacketParseError)
                else f"上一次的产物没通过校验：{error.message}"
            )
            outcome = await self._discusser.say(
                role_title=role_title,
                role_focus=role_focus,
                topic=thread.topic,
                question=question,
                history=history,
                document_state=document_state,
                related_findings=findings,
                discussion_id=str(discussion_id),
                turn_slot=f"s{base + 1}",
                known_eids=known_eids,
                validation_error=repair,
                # 最后一次尝试：不存在的编号摘掉、正文照收（`02` §11.0.2）
                lenient=True,
            )
            # 第二次的账本成员检查失败就地抛（线程不动，照 say 两次失败同款）。
            validate_ledger_refs(outcome.refs, known_eids=known_eids)
        with self._uow:
            stored = self._discussions.get(discussion_id)
            stored.say_pair(
                user_text=text, assistant_text=outcome.text, based_on=outcome.refs
            )
            self._discussion_writer.save(stored)
        _publish_discussion(self._events, self._clock, stored)
        return thread_view(stored)


class RunPanelRound:
    """跑一轮 panel 自主讨论（M18）：本轮在场者并行发言 → 综合 → 落库。

    一次只跑**一轮**：N 轮由前端循环驱动（进度可见、可随时停）。
    失败语义：已落库的 turn 保留（自包含合法），本轮停、无综合；重试补剩余发言者。
    轮内发言并行（gather；各人恰好说一次，显示顺序 = 完成顺序）。
    """

    def __init__(
        self,
        *,
        documents: DocumentReader,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        evidence: EvidenceReader,
        explorations: ExplorationReader,
        uow: UnitOfWork,
        discusser: Discusser,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
        catalog: SeatCatalog,
        discussion_roles: tuple,
    ) -> None:
        self._documents = documents
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._evidence = evidence
        self._explorations = explorations
        self._uow = uow
        self._discusser = discusser
        self._checker = checker
        self._clock = clock
        self._events = events
        self._catalog = catalog
        self._discussion_roles = discussion_roles

    async def __call__(
        self, *, discussion_id: DiscussionId
    ) -> ThreadView:
        thread = self._discussions.get(discussion_id)
        if thread.status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=discussion_id)
        round_no = thread.current_round
        if round_no > MAX_PANEL_ROUNDS:
            raise ThreadFull(limit=MAX_PANEL_ROUNDS)
        in_round = [
            member for member in thread.participants if member.joined_round <= round_no
        ]
        spoken = {
            turn.author
            for turn in thread.turns
            if turn.round == round_no and turn.author not in ("user", "assistant")
        }
        remaining = [member for member in in_round if member.role_id not in spoken]
        history = render_thread_history(thread.turns)
        document_state, known_eids = _discussion_window(
            self._documents,
            self._checker,
            self._evidence,
            thread,
            history=history,
        )
        question = _question_text(thread.question)
        findings = _render_thread_findings(
            thread.findings, explorations=self._explorations
        )
        results = await asyncio.gather(
            *(
                self._speak(
                    member,
                    topic=thread.topic,
                    question=question,
                    history=history,
                    document_state=document_state,
                    findings=findings,
                    discussion_id=discussion_id,
                    round_no=round_no,
                    known_eids=known_eids,
                )
                for member in remaining
            ),
            return_exceptions=True,
        )
        for member, result in zip(remaining, results, strict=True):
            if isinstance(result, BaseException):
                continue
            with self._uow:
                stored = self._discussions.get(discussion_id)
                stored.append_agent_turn(
                    role_id=member.role_id,
                    text=result.text,
                    based_on=result.refs,
                    round=round_no,
                )
                self._discussion_writer.save(stored)
            _publish_discussion(self._events, self._clock, stored)
        failures = [
            (member, result)
            for member, result in zip(remaining, results, strict=True)
            if isinstance(result, BaseException)
        ]
        if failures:
            # 已落库的不动；调用方（前端）重试本轮会补剩余发言者。
            raise failures[0][1]
        await self._synthesize(
            discussion_id=discussion_id,
            topic=thread.topic,
            round_no=round_no,
        )
        return thread_view(self._discussions.get(discussion_id))

    async def _speak(
        self,
        member: Participant,
        *,
        topic: str,
        question: str,
        history: str,
        document_state: str,
        findings: str,
        discussion_id: DiscussionId,
        round_no: int,
        known_eids: frozenset[EvidenceId],
    ) -> SayOutcome:
        role_title, role_focus = _discussion_role_of(
            member.role_id, catalog=self._catalog, discussion_roles=self._discussion_roles
        )
        slot = f"r{round_no}-{member.role_id}"
        try:
            outcome = await self._discusser.say(
                role_title=role_title,
                role_focus=role_focus,
                topic=topic,
                question=question,
                history=history,
                document_state=document_state,
                related_findings=findings,
                discussion_id=str(discussion_id),
                turn_slot=slot,
                known_eids=known_eids,
            )
            validate_ledger_refs(outcome.refs, known_eids=known_eids)
            return outcome
        except ModelCallFailed:
            raise
        except (PacketParseError, ShapeError) as error:
            repair = (
                repair_note(error)
                if isinstance(error, PacketParseError)
                else f"上一次的产物没通过校验：{error.message}"
            )
            outcome = await self._discusser.say(
                role_title=role_title,
                role_focus=role_focus,
                topic=topic,
                question=question,
                history=history,
                document_state=document_state,
                related_findings=findings,
                discussion_id=str(discussion_id),
                turn_slot=f"{slot}-2",
                known_eids=known_eids,
                validation_error=repair,
                # 最后一次尝试：不存在的编号摘掉、正文照收（`02` §11.0.2）
                lenient=True,
            )
            validate_ledger_refs(outcome.refs, known_eids=known_eids)
            return outcome

    async def _synthesize(
        self, *, discussion_id: DiscussionId, topic: str, round_no: int
    ) -> None:
        stored = self._discussions.get(discussion_id)
        titles = {member.role_id: member.role_title for member in stored.participants}
        round_history = "\n".join(
            f"{titles.get(turn.author, turn.author)}：{turn.text}"
            for turn in stored.turns
            if turn.round == round_no
        )
        prior_summaries = "\n".join(
            f"第{summary.index}轮：共识{list(summary.agreed)}；分歧{list(summary.disagreed)}；"
            f"未决{list(summary.open)}"
            for summary in stored.summaries
        )
        outcome = await self._discusser.synthesize(
            topic=topic,
            round_history=round_history,
            prior_summaries=prior_summaries,
            discussion_id=str(discussion_id),
            synthesis_slot=f"y{round_no}",
        )
        with self._uow:
            current = self._discussions.get(discussion_id)
            current.append_summary(
                RoundSummary(
                    index=round_no,
                    agreed=outcome.agreed,
                    disagreed=outcome.disagreed,
                    open=outcome.open,
                )
            )
            self._discussion_writer.save(current)
        _publish_discussion(self._events, self._clock, current)


class JoinPanel:
    """中途拉人进 panel（M18）：下一轮生效，不追补、不重跑。"""

    def __init__(
        self,
        *,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
        catalog: SeatCatalog,
        discussion_roles: tuple,
    ) -> None:
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._uow = uow
        self._clock = clock
        self._events = events
        self._catalog = catalog
        self._discussion_roles = discussion_roles

    async def __call__(self, *, discussion_id: DiscussionId, role_id: str) -> ThreadView:
        # 存在性由 join 报（UnknownDiscussion）；这里不先 get 一次——with 块里拿的是最新。
        role_title, _ = _discussion_role_of(
            role_id, catalog=self._catalog, discussion_roles=self._discussion_roles
        )
        with self._uow:
            stored = self._discussions.get(discussion_id)
            stored.join(role_id=role_id, role_title=role_title)
            self._discussion_writer.save(stored)
        _publish_discussion(self._events, self._clock, stored)
        return thread_view(stored)


class AssembleCards:
    """把讨论总结成 1-3 张卡：repair 1 次；再坏线程不动。"""

    def __init__(
        self,
        *,
        documents: DocumentReader,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        evidence: EvidenceReader,
        uow: UnitOfWork,
        discusser: Discusser,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
        catalog: SeatCatalog,
        discussion_roles: tuple,
    ) -> None:
        self._documents = documents
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._evidence = evidence
        self._uow = uow
        self._discusser = discusser
        self._checker = checker
        self._clock = clock
        self._events = events
        self._catalog = catalog
        self._discussion_roles = discussion_roles

    async def __call__(
        self, *, discussion_id: DiscussionId
    ) -> ThreadView:
        thread = self._discussions.get(discussion_id)
        if thread.status is not ThreadStatus.OPEN:
            raise ThreadClosed(discussion_id=discussion_id)
        role_title, role_focus = _discussion_role_of(
            thread.role_id, catalog=self._catalog, discussion_roles=self._discussion_roles
        )
        # 出卡与发言同一个主题信号、同一条窄化（`02` §11.0.61）。**出卡的 grounding
        # 是硬二选一**（`grounding=document` → `based_on` 必须来自这里），所以
        # 窄化后 `known` 必须同源收缩——否则卡会引一个它没看见的编号。
        thread_text = render_thread_history(thread.turns)
        document_state, known_eids = _discussion_window(
            self._documents,
            self._checker,
            self._evidence,
            thread,
            history=thread_text,
        )
        # slot = 已有 cards 数＋1：cards 只增，单调，撞不了；不用另设计数器。
        base = len(thread.cards) + 1
        try:
            outcome = await self._discusser.assemble(
                role_title=role_title,
                role_focus=role_focus,
                topic=thread.topic,
                thread_text=thread_text,
                document_state=document_state,
                discussion_id=str(discussion_id),
                assemble_slot=f"c{base}",
                known_eids=known_eids,
            )
        except ModelCallFailed:
            raise
        except (PacketParseError, ShapeError) as error:
            repair = (
                repair_note(error)
                if isinstance(error, PacketParseError)
                else f"上一次的产物没通过校验：{error.message}"
            )
            outcome = await self._discusser.assemble(
                role_title=role_title,
                role_focus=role_focus,
                topic=thread.topic,
                thread_text=thread_text,
                document_state=document_state,
                discussion_id=str(discussion_id),
                assemble_slot=f"c{base + 1}",
                known_eids=known_eids,
                validation_error=repair,
                # 最后一次尝试：不存在的编号摘掉、不判死（`02` §11.0.2）
                lenient=True,
            )
        stamped = tuple(
            Card(
                card_id=f"C{len(thread.cards) + index + 1}",
                statement=item.statement,
                grounding=item.grounding,
                based_on=item.based_on,
            )
            for index, item in enumerate(outcome.cards)
        )
        with self._uow:
            stored = self._discussions.get(discussion_id)
            stored.append_cards(stamped)
            self._discussion_writer.save(stored)
        _publish_discussion(self._events, self._clock, stored)
        return thread_view(stored)


class KeepDiscussionCard:
    """收下一张卡：`seed` 只记账；`material` 还会合成一份材料（同 sha 幂等）。

    同去向重复收下幂等返（不再合成/写信）；换去向由领域拒（T5）。不发事件——
    响应体就是真身，与收候选同一个口径。
    """

    def __init__(
        self,
        *,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        materials: MaterialStore,
        uow: UnitOfWork,
        clock: Clock,
        inspiration_repo: InspirationRepository = NO_INSPIRATIONS,
    ) -> None:
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._materials = materials
        self._uow = uow
        self._clock = clock
        self._inspiration_repo = inspiration_repo

    async def __call__(
        self, *, discussion_id: DiscussionId, card_id: str, as_: str
    ) -> ThreadView:
        thread = self._discussions.get(discussion_id)
        card = _card_of(thread, card_id)
        if card.state is CardState.KEPT and card.kept_as == as_:
            return thread_view(thread)
        if as_ == "material":
            parsed, content = discussion_material(card, thread_id=str(discussion_id))
            with self._uow:
                packet = self._materials.put(
                    project_id=thread.project_id,
                    parsed=parsed,
                    sha256=namespaced_material_sha(
                        "discussion-material", parsed.source_ref
                    ),
                    content=content,
                    now=self._clock.now(),
                )
                stored = self._discussions.get(discussion_id)
                stored.keep_card(
                    card_id, as_="material", material_id=packet.material_id
                )
                self._discussion_writer.save(stored)
            self._store_inspiration(thread.project_id, card)
            return thread_view(stored)
        with self._uow:
            stored = self._discussions.get(discussion_id)
            stored.keep_card(card_id, as_="seed")
            self._discussion_writer.save(stored)
        self._store_inspiration(thread.project_id, card)
        return thread_view(stored)

    def _store_inspiration(self, project_id, card) -> None:
        """L3: 收下讨论卡后存入灵感档案。"""
        item = InspirationItem(
            item_id=f"ins-disc-{card.card_id}-{uuid.uuid4().hex[:6]}",
            project_id=str(project_id),
            origin=InspirationOrigin.DISCUSSION,
            statement=card.statement,
        )
        self._inspiration_repo.save(item)


class DismissDiscussionCard:
    """忽略一张卡：终态——不进灵感箱、不回流（T5）。已收下的由领域拒。"""

    def __init__(
        self,
        *,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        uow: UnitOfWork,
    ) -> None:
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._uow = uow

    async def __call__(
        self, *, discussion_id: DiscussionId, card_id: str
    ) -> ThreadView:
        with self._uow:
            stored = self._discussions.get(discussion_id)
            stored.dismiss_card(card_id)
            self._discussion_writer.save(stored)
        return thread_view(stored)


class CloseDiscussion:
    """关掉一条线程：终态幂等（M8 abandon 去 canceller 版，无 runner 可取消）。"""

    def __init__(
        self,
        *,
        discussions: DiscussionReader,
        discussion_writer: DiscussionWriter,
        uow: UnitOfWork,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._discussions = discussions
        self._discussion_writer = discussion_writer
        self._uow = uow
        self._clock = clock
        self._events = events

    async def __call__(
        self, *, discussion_id: DiscussionId
    ) -> ThreadView:
        with self._uow:
            thread = self._discussions.get(discussion_id)
            thread.close()
            self._discussion_writer.save(thread)
        _publish_discussion(self._events, self._clock, thread)
        return thread_view(self._discussions.get(discussion_id))


class ListDiscussions:
    """线程列表：一次给全（≤上限），由界面投影出要看的那些。"""

    def __init__(
        self, *, discussions: DiscussionReader, limit: int = MAX_DISCUSSION_LIST
    ) -> None:
        self._discussions = discussions
        self._limit = limit

    async def __call__(self, *, project_id: ProjectId) -> tuple[ThreadView, ...]:
        return tuple(
            thread_view(item)
            for item in self._discussions.list_for_project(
                project_id, limit=self._limit
            )
        )


def _sim_ref_repair_note(ref_drops: tuple[tuple[str, str], ...]) -> str:
    """引用类问题的重跑指引（P5d 口径：点名第几条 + 要求，不给它抄错串的机会）。"""

    listed = "；".join(f"{handle}：{reason}" for handle, reason in ref_drops)
    return (
        f"上一次的产物有引用问题：{listed}。"
        "anchors 与 based_on 只能引用上面出现过的那种编号（形如 E1、E2）；"
        "s8、s5 这类章节号不是编号，不许当锚点；切片引用要写 local_id（S1、S2）"
    )


def _hypothesis_to_json(hypothesis: Hypothesis, *, slice_id: str) -> dict:
    """领域假设 → 轮次快照 dict（与 `exploration_to_payload` 的 6 键同形 + 挂载）。

    `slice_id` 必须带上：抽屉按它把假设挂回切片下分组，没有它就是一盘散沙。
    """

    return {
        "local_id": hypothesis.local_id,
        "slice_id": slice_id,
        "statement": hypothesis.statement,
        "grounding": hypothesis.grounding.value,
        "based_on": [str(ref) for ref in hypothesis.based_on],
        "falsification": hypothesis.falsification,
        "evidence_needed": hypothesis.evidence_needed,
    }


@dataclass(frozen=True, slots=True)
class KeepSimResult:
    """收下模拟的结果：材料 id + 实际收下的 id（去重保序后）。"""

    material_id: str
    kept: tuple[str, ...]


class RunSimInquiry:
    """跑一趟模拟调研（M10 轮内动作）：只在草案卡上跑，不碰轮次状态。

    与 M8 `execute` 同款两段式：报文不合格重装 1 次；网关失败直接抛（轮次不动、
    不记 runs）。条目级违规（引用落空 / 身份词 / 数字 / 重复）不整包判死：
    给一次重跑机会（带指引），第二次仍犯或命中重复就丢条 + `ASSEMBLY` 留痕
    （2026-10-07 真跑修正：空文档时模型把 `s8` 当锚点，旧实现整包 502，与
    `02` §11.0.2 / §11.0.47 的"丢条＋留痕"口径不符）。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        documents: DocumentReader,
        evidence: EvidenceReader,
        memory: MemoryReader,
        materials: MaterialStore,
        uow: UnitOfWork,
        sim_inquirer: SimInquiryAgent,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._documents = documents
        self._evidence = evidence
        self._memory = memory
        self._materials = materials
        self._uow = uow
        self._sim_inquirer = sim_inquirer
        self._checker = checker
        self._clock = clock
        self._events = events

    async def __call__(self, *, round_id: RoundId) -> None:
        round_ = self._rounds.get(round_id)
        if round_.step is not Step.AWAITING_CONFIRM:
            raise IllegalStepTransition(
                current=round_.step, target=Step.AWAITING_CONFIRM
            )
        if round_.waiting_on is not WaitingOn.DRAFT:
            raise WaitingOnHuman(waiting_on=round_.waiting_on)
        if round_.sim_inquiry_runs >= MAX_SIM_INQUIRIES_PER_ROUND:
            raise TooManySimInquiries(limit=MAX_SIM_INQUIRIES_PER_ROUND)
        document = self._documents.get(round_.project_id)
        view = document.view()
        window = document_window(
            view, readiness=self._checker.check(view, adapter=adapt(document.template))
        )
        document_state = window.text
        memory_text = render_memory(self._memory.get(round_.project_id).entries)
        # 接地三源（`02` §11.0.47）：文档 ＋ 本轮用户的话（带 E 编号）＋ 本轮材料。
        # 只渲染文档时，空文档的项目里模型看不见任何编号——真跑把 s8 当锚点了。
        claims_text = render_claims(round_.claims)
        material_text = external_material_text(
            round_, store=self._materials, query=round_.user_input
        )
        ledger = self._evidence.get(round_.project_id)
        # **不变量**（`02` §11.0.61）：接地三源都算"看得见"，账本其余部分不算。
        known_eids = window.known_eids(ledger) | (
            visible_eids(claims_text, material_text, memory_text)
            & window.ledger_ids(ledger)
        )
        doc_keys = frozenset(
            normalize_statement(entry.text) for entry in iter_document_entries(view)
        )
        open_keys = frozenset(
            normalize_statement(title) for _, title in open_item_keys(document)
        )

        repair = ""
        accepted: tuple | None = None
        pass_index = round_.sim_inquiry_runs + 1
        for attempt in range(1, MAX_SIM_INQUIRY_ATTEMPTS + 1):
            try:
                outcome = await self._sim_inquirer.run(
                    document_state=document_state,
                    claims=claims_text,
                    external_material=material_text,
                    project_memory=memory_text,
                    round_id=str(round_id),
                    known_eids=known_eids,
                    validation_error=repair,
                    packet_slot=f"s{attempt}",
                )
            except ModelCallFailed:
                raise
            except (PacketParseError, ShapeError) as error:
                if attempt == MAX_SIM_INQUIRY_ATTEMPTS:
                    raise
                repair = (
                    repair_note(error)
                    if isinstance(error, PacketParseError)
                    else f"上一次的产物没通过校验：{error.message}"
                )
                continue
            clean, drops, offense = self._sieve(
                outcome,
                doc_keys=doc_keys,
                open_keys=open_keys,
                pass_index=pass_index,
            )
            guidance: list[str] = []
            if outcome.ref_drops:
                guidance.append(_sim_ref_repair_note(outcome.ref_drops))
            if offense is not None:
                guidance.append(offense)
            if guidance and attempt < MAX_SIM_INQUIRY_ATTEMPTS:
                repair = "；".join(guidance)
                continue
            accepted = (clean, drops, outcome.ref_drops)
            break
        assert accepted is not None  # 循环只会 break 或 raise
        (clean_slices, clean_hypotheses, clean_unknowns), drops, ref_drops = accepted
        if not clean_hypotheses and not clean_unknowns:
            raise PacketParseError(
                "模拟筛完没剩可用的假设与盲区（引用落空 / 重复 / 身份词数字全被丢了）",
                purpose=SimInquiryAgent.definition.purpose,
            )
        ref_items = tuple(
            DroppedItem(
                handle=handle,
                reason=reason,
                source=DropSource.ASSEMBLY,
                seat_id=None,
                pass_index=pass_index,
            )
            for handle, reason in ref_drops
        )
        with self._uow:
            stored = self._rounds.get(round_id)
            stored.record_sim_inquiry(
                slices=clean_slices,
                hypotheses=[
                    _hypothesis_to_json(item, slice_id=slice_id)
                    for item, slice_id in clean_hypotheses
                ],
                unknowns=clean_unknowns,
            )
            stored.note_dropped(ref_items + drops)
            self._round_writer.save(stored)
        _publish(self._events, self._clock, stored)

    def _sieve(
        self,
        outcome: SimInquiryOutcome,
        *,
        doc_keys: frozenset[str],
        open_keys: frozenset[str],
        pass_index: int,
    ) -> tuple[tuple, tuple[DroppedItem, ...], str | None]:
        """条目级筛：窄表（身份词/数字）+ 去重。返回（干净三件套，丢条，重跑指引或 None）。

        挂载反查走 packet drafts（只有 draft 才有 slice_id）：`filled_local_id`
        与装配层同一规则，漂移会在测试里 loud fail（`by_id[lid]` 直接 KeyError），
        不静默错配。返回的干净假设带 `(对象, slice_id)` 对，落快照时写进去。
        """

        dropped_slices: set[str] = set()
        drops: list[DroppedItem] = []
        offense: str | None = None
        by_id = {item.local_id: item for item in outcome.hypotheses}

        def drop(handle: str, reason: str) -> None:
            drops.append(
                DroppedItem(
                    handle=handle,
                    reason=reason,
                    source=DropSource.ASSEMBLY,
                    seat_id=None,
                    pass_index=pass_index,
                )
            )

        for item in outcome.slices:
            text = " ".join(
                (item["situation"], item["job"], item["trigger"], item["workaround"])
            )
            hit = narrow_offense(f"切片 {item['local_id']}", text)
            if hit is not None:
                dropped_slices.add(item["local_id"])
                drop(f"模拟切片 {item['local_id']}", hit)
                if offense is None:
                    offense = hit
        clean_hypotheses: list[tuple[Hypothesis, str]] = []
        for index, draft in enumerate(outcome.packet.hypotheses, start=1):
            local_id = filled_local_id(draft.local_id, "H", index)
            if local_id not in by_id:
                # 装配层已把它丢了（引用落空/挂载失败），原因在 `outcome.ref_drops` 里；
                # 这里跳过，不重复记账。
                continue
            if draft.slice_id in dropped_slices:
                drop(
                    f"模拟假设 {local_id}",
                    f"它挂的切片 {draft.slice_id} 被丢了（分组没了，挂载无意义）",
                )
                continue
            hit = narrow_offense(f"假设 {local_id}", draft.statement)
            if hit is not None:
                drop(f"模拟假设 {local_id}", hit)
                if offense is None:
                    offense = hit
                continue
            dup = duplicate_of(
                draft.statement, doc_keys=doc_keys, open_keys=open_keys
            )
            if dup == "document":
                drop(f"模拟假设 {local_id}", "与文档已有内容重复，丢条不问")
                continue
            if dup == "open_item":
                drop(f"模拟假设 {local_id}", "与 s11 活跃未决条目重复，丢条不问")
                continue
            clean_hypotheses.append((by_id[local_id], draft.slice_id))
        clean_unknowns: list = []
        for item in outcome.unknowns:
            hit = narrow_offense(f"盲区 {item['local_id']}", item["statement"])
            if hit is not None:
                drop(f"模拟盲区 {item['local_id']}", hit)
                if offense is None:
                    offense = hit
                continue
            dup = duplicate_of(item["statement"], doc_keys=doc_keys, open_keys=open_keys)
            if dup == "document":
                drop(f"模拟盲区 {item['local_id']}", "与文档已有内容重复，丢条不问")
                continue
            if dup == "open_item":
                drop(f"模拟盲区 {item['local_id']}", "与 s11 活跃未决条目重复，丢条不问")
                continue
            clean_unknowns.append(item)
        clean_slices = [
            item for item in outcome.slices if item["local_id"] not in dropped_slices
        ]
        return (clean_slices, clean_hypotheses, clean_unknowns), tuple(drops), offense

class KeepSimInquiry:
    """收下模拟的勾选为材料：合成一份（多块）＋同 uow 落库＋记 chosen；同选择重复收下幂等。

    切片是分组不能单收；空选择直接拒（不合成空材料占材料位）。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        materials: MaterialStore,
        uow: UnitOfWork,
        clock: Clock,
        inspiration_repo: InspirationRepository = NO_INSPIRATIONS,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._materials = materials
        self._uow = uow
        self._clock = clock
        self._inspiration_repo = inspiration_repo

    async def __call__(
        self, *, round_id: RoundId, chosen: Sequence[str]
    ) -> KeepSimResult:
        kept = tuple(dict.fromkeys(chosen))
        if not kept:
            raise ShapeError("没勾不能收：先在模拟抽屉里勾中假设或盲区")
        round_ = self._rounds.get(round_id)
        hyp_ids = {item["local_id"] for item in round_.sim_hypotheses}
        unk_ids = {item["local_id"] for item in round_.sim_unknowns}
        slice_ids = {item["local_id"] for item in round_.sim_slices}
        for cid in kept:
            if cid in slice_ids:
                raise ShapeError(
                    f"切片是分组，不能单收：{cid}，勾它下面的假设或盲区"
                )
            if cid not in hyp_ids and cid not in unk_ids:
                raise ShapeError(f"这一轮模拟没有这条：{cid}", candidate_id=cid)
        parsed, content = synthesize_sim_material(
            round_id=str(round_id),
            chosen=kept,
            hypotheses=tuple(round_.sim_hypotheses),
            unknowns=tuple(round_.sim_unknowns),
            now=self._clock.now().isoformat(),
        )
        with self._uow:
            packet = self._materials.put(
                project_id=round_.project_id,
                parsed=parsed,
                sha256=namespaced_material_sha("simulation-material", parsed.source_ref),
                content=content,
                now=self._clock.now(),
            )
            stored = self._rounds.get(round_id)
            stored.choose_sim_inquiry(kept)
            self._round_writer.save(stored)
        self._store_inspirations(round_)
        return KeepSimResult(material_id=packet.material_id, kept=kept)

    def _store_inspirations(self, round_) -> None:
        """L3: 收下模拟调研后存入灵感档案。"""
        for hyp in round_.sim_hypotheses:
            item = InspirationItem(
                item_id=f"ins-sim-{hyp['local_id']}-{uuid.uuid4().hex[:6]}",
                project_id=str(round_.project_id),
                 origin=InspirationOrigin.SIM_INQUIRY,
                 statement=hyp["statement"],
             )
            self._inspiration_repo.save(item)


class RunSimReview:
    """跑一趟模拟评测（M11 轮内动作）：只在草案卡上跑，不碰轮次状态。

    与 M10 `RunSimInquiry` 的骨架相同（门控 → 渲染四源 → 并行 → 重装环 → 事务落库），
    三处**有意不同**（口径见 `02` §11.0.48）：

    ① 产物去向不同：不产材料、无 keep——"减"提案直接追加进**本轮草案**（"它提议的"组，
       默认不勾），随确认走唯一写路径；
    ② 失败语义不同：任何 persona 两次仍不过 → **整趟失败**（`Draft.gaps` 一行 + 抛出），
       不做条目级丢条——走查报告是整体，丢一条 friction 会牵连 verdict_because/R1 计数；
    ③ 账目不同：三人并行各带 1 次重装 = 第六本账 ≤6 次调用；每份草案限 1 次（R9）。
    """

    def __init__(
        self,
        *,
        rounds: RoundReader,
        round_writer: RoundWriter,
        documents: DocumentReader,
        evidence: EvidenceReader,
        evidence_writer: EvidenceWriter,
        memory: MemoryReader,
        materials: MaterialStore,
        uow: UnitOfWork,
        personas: tuple,
        simulator: PersonaSimulatorAgent,
        checker: ReadinessChecker,
        clock: Clock,
        events: EventPublisher,
    ) -> None:
        self._rounds = rounds
        self._round_writer = round_writer
        self._documents = documents
        self._evidence = evidence
        self._evidence_writer = evidence_writer
        self._memory = memory
        self._materials = materials
        self._uow = uow
        #: 固定 persona 表（`infrastructure/personas/personas.toml` 装载，装配层注入）。
        #: 鸭子类型读 `.persona_id/.title`——application 不 import infrastructure。
        self._personas = personas
        self._simulator = simulator
        self._checker = checker
        self._clock = clock
        self._events = events
        self._assigner = EvidenceAssigner()

    async def __call__(
        self,
        *,
        round_id: RoundId,
        persona_ids: tuple[str, ...],
        feature_slugs: tuple[str, ...],
    ) -> None:
        round_ = self._rounds.get(round_id)
        if round_.step is not Step.AWAITING_CONFIRM:
            raise IllegalStepTransition(
                current=round_.step, target=Step.AWAITING_CONFIRM
            )
        if round_.waiting_on is not WaitingOn.DRAFT:
            raise WaitingOnHuman(waiting_on=round_.waiting_on)
        if round_.sim_review_runs >= MAX_SIM_REVIEWS_PER_DRAFT:
            raise TooManySimReviews(limit=MAX_SIM_REVIEWS_PER_DRAFT)
        chosen = _review_personas(self._personas, persona_ids)
        if not 1 <= len(feature_slugs) <= MAX_SIM_REVIEW_FEATURES:
            raise ShapeError(
                f"一次评测查 1–{MAX_SIM_REVIEW_FEATURES} 个功能，收到 {len(feature_slugs)} 个"
            )
        if len(set(feature_slugs)) != len(feature_slugs):
            raise ShapeError("同一个功能点两次没有意义，去掉重复的")
        document = self._documents.get(round_.project_id)
        view = document.view()
        anchors = document_anchors(view)
        unknown = [slug for slug in feature_slugs if slug not in anchors.feature_fields]
        if unknown:
            raise ShapeError(f"文档里没有这些功能：{'、'.join(unknown)}")

        # 接地四源（与 M10 同款；评测没有 schema_json 之外的额外上下文）。
        # document_state **只给点名的功能**：机械筛（`sim_review.review_issues`）的合法
        # 引用域本来就在目标功能内（别的内容引了反而是 issue），全文只会添干扰——
        # `07 §7.12` 第 2 项 / 判据①「删优先于排」。节级条目以"未展开 N 条"交代。
        document_state = render_document_state(
            view,
            readiness=self._checker.check(view, adapter=adapt(document.template)),
            feature_slugs=frozenset(feature_slugs),
        )
        memory_text = render_memory(self._memory.get(round_.project_id).entries)
        claims_text = render_claims(round_.claims)
        # 评测只查点名的功能：材料按这些功能名选块（不再把整池塞进提示词）。
        material_text = external_material_text(
            round_,
            store=self._materials,
            query=" ".join(feature_slugs) or round_.user_input,
        )

        results = await asyncio.gather(
            *(
                self._one(
                    persona=persona,
                    feature_slugs=feature_slugs,
                    anchors=anchors,
                    document_state=document_state,
                    claims_text=claims_text,
                    material_text=material_text,
                    memory_text=memory_text,
                    round_id=round_id,
                )
                for persona in chosen
            ),
            return_exceptions=True,
        )
        failure = next(
            (item for item in results if isinstance(item, BaseException)), None
        )
        if failure is not None:
            note = _review_failure_note(failure)
            with self._uow:
                stored = self._rounds.get(round_id)
                stored.augment_draft(gaps=(f"模拟评测未完成（{note}），没有产出——可重跑",))
                self._round_writer.save(stored)
            raise failure

        reviews = [item for item in results if isinstance(item, dict)]
        outcome = evaluate_rules(reviews, anchors=anchors, requested=feature_slugs)
        with self._uow:
            stored = self._rounds.get(round_id)
            ledger = self._evidence.get(stored.project_id)
            citations = list(outcome.citations)
            eids = self._assigner.assign_sim_review(
                [(review_source_ref(str(round_id), item), item.text) for item in citations],
                ledger=ledger,
                round_id=round_id,
                now=self._clock.now(),
            )
            self._evidence_writer.save(ledger)
            by_key = {item.key: eid for item, eid in zip(citations, eids, strict=True)}
            proposals = tuple(
                Proposal(
                    proposal_id=ProposalId(draft.proposal_id),
                    entry_id=EntryId(draft.entry_id),
                    op=draft.op,
                    target=draft.target,
                    text=draft.text,
                    origin=Origin(
                        OriginType.PROPOSED,
                        tuple(by_key[key] for key in draft.ref_keys),
                        rationale=draft.rationale,
                    ),
                    # D5 例外（M11）：评测提案是用户逐条勾选的"减"动作——
                    # 确认时允许改动被手改过的块；但不给它盖"手改"章
                    # （放行与留痕分工不同，见 `02` §11.0.48）。
                    allow_manual_override=True,
                )
                for draft in outcome.proposals
            )
            stored.augment_draft(proposals=proposals)
            stored.record_sim_review(
                {
                    "evaluated_version": view.version_no,
                    "personas": reviews,
                    "advisories": list(outcome.advisories),
                }
            )
            self._round_writer.save(stored)
        _publish(self._events, self._clock, stored)

    async def _one(
        self,
        *,
        persona: object,
        feature_slugs: tuple[str, ...],
        anchors: object,
        document_state: str,
        claims_text: str,
        material_text: str,
        memory_text: str,
        round_id: RoundId,
    ) -> dict:
        """一个 persona 的重装环：调用 → 机械筛 →（有指引重装一次）→ 干净 dict 或抛出。"""

        repair = ""
        for attempt in range(1, MAX_SIM_REVIEW_ATTEMPTS + 1):
            try:
                outcome = await self._simulator.run(
                    persona=persona,
                    feature_slugs=feature_slugs,
                    document_state=document_state,
                    claims=claims_text,
                    external_material=material_text,
                    project_memory=memory_text,
                    round_id=str(round_id),
                    validation_error=repair,
                    packet_slot=f"{persona.persona_id}-a{attempt}",
                )
            except ModelCallFailed:
                raise
            except (PacketParseError, ShapeError) as error:
                if attempt == MAX_SIM_REVIEW_ATTEMPTS:
                    raise
                repair = (
                    repair_note(error)
                    if isinstance(error, PacketParseError)
                    else f"上一次的产物没通过校验：{error.message}"
                )
                continue
            issues = review_issues(
                outcome.review, anchors=anchors, requested=feature_slugs
            )
            if not issues:
                return outcome.review
            if attempt == MAX_SIM_REVIEW_ATTEMPTS:
                raise PacketParseError(
                    f"{persona.persona_id} 的走查两次都没过机械筛："
                    + "；".join(issues[:3]),
                    purpose=PersonaSimulatorAgent.definition.purpose,
                )
            repair = "；".join(issues)
        raise AssertionError("重装环必然 return 或 raise")


def _review_personas(personas: tuple, persona_ids: tuple[str, ...]) -> tuple:
    """按 id 取 persona（鸭子类型；application 不 import infrastructure 的装载器）。"""

    if not 1 <= len(persona_ids) <= MAX_SIM_REVIEW_PERSONAS:
        raise ShapeError(
            f"一次评测选 1–{MAX_SIM_REVIEW_PERSONAS} 个模拟使用者，收到 {len(persona_ids)} 个"
        )
    if len(set(persona_ids)) != len(persona_ids):
        raise ShapeError("同一个模拟使用者选两次没有意义，去掉重复的")
    by_id = {item.persona_id: item for item in personas}
    chosen = []
    for persona_id in persona_ids:
        if persona_id not in by_id:
            raise ShapeError(f"没有这个模拟使用者：{persona_id}")
        chosen.append(by_id[persona_id])
    return tuple(chosen)


def _review_failure_note(failure: BaseException) -> str:
    return getattr(failure, "message", None) or str(failure)


# ── 认证与账号（多用户改造 Task 3）───────────────────────────────────────────
#
# 一个用例一个入口，全部 `async def __call__`。密码学（argon2）**不在这里**：
# `hash_password` / `verify_password` 由装配点注入——application 不 import
# infrastructure（分层守卫），注入的函数就是那层唯一的密码学落点。


@dataclass(frozen=True, slots=True)
class UserView:
    """HTTP 层看到的用户投影。`require_user` 返它（Task 6 复用）。"""

    user_id: UserId
    username: str
    role: str
    status: str


def _user_view(record: UserRecord) -> UserView:
    return UserView(
        user_id=record.user_id,
        username=record.username,
        role=record.role,
        status=record.status,
    )


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _unauthenticated() -> ApplicationError:
    """登录/会话失败只此一句、只此一码——不泄露"用户不存在"还是"密码错"。"""

    return ApplicationError("用户名或密码不正确", code="unauthenticated")


def _rate_limited(retry_after: int) -> ApplicationError:
    """尝试速率超限（429）。和 `_unauthenticated` 分开：那个是凭证问题、
    要对 ghost 与错密码逐字节一致；这个是速率问题，客户端该知道等多久。"""

    return RateLimited(
        f"登录尝试过于频繁，请 {retry_after} 秒后重试",
        retry_after=retry_after,
    )


def _forbidden() -> ApplicationError:
    return ApplicationError("需要管理员权限", code="forbidden")


def _unknown_user(user_id: object) -> ApplicationError:
    return ApplicationError(
        f"找不到这个用户：{user_id}", code="unknown_user", user_id=str(user_id)
    )


def _require_admin(actor: UserView) -> None:
    if actor.role != "admin":
        raise _forbidden()


def _admin_is_protected(target: UserView) -> None:
    """**不许把动作落在 admin 头上**（含自己）。

    为什么是硬拦截而不是只拦"自己"：唯一的 admin 封了自己 → 会话当场吊销 →
    永远进不来，唯一出路是直接改库。两个 admin 互相封一下，系统就没人能进了。
    真要动 admin，得先在库里把它的 `role` 改回去——那是运维动作，不是管理后台的一个按钮。
    """

    if target.role == "admin":
        raise ApplicationError(
            "管理员账号不能被封禁或拒绝；先在库里把它的角色改掉，再来操作",
            code="admin_protected",
            user_id=str(target.user_id),
        )


class LoginThrottle:
    """内存限流闸。**同时是 Redis 版的降级目标**——Redis 连不上时就落回这里。

    三个维度（见 `ports.LoginGuard`）：

    - `_failures`：`{(ip, username): [失败时刻...]}`，失败锁。只记失败，登录成功清掉。
    - `_ip_attempts` / `_user_attempts`：`{(维度值): [尝试时刻...]}`，速率闸。
      每次尝试都记（不论成败），**只靠时间过期恢复**——否则攻击者偶尔猜对一次
      就能把计数洗白。

    **为什么键用元组而不是 `f"{ip}|{username}"`**：拼字符串有歧义，
    `(ip="a|b", user="c")` 与 `(ip="a", user="b|c")` 会变成同一个键，
    攻击者能用用户名里的 `|` 撞掉别人的限额。

    **内存上界**：三个表都限 `max_keys`，按最早入表淘汰（dict 保插入序）。
    海量不同用户名不能把内存撑爆。

    **不接受 `now` 形参**：时间来源是构造时注入的 `Clock`。Redis 版用服务端
    时钟，调用方不该在两处各对一次表。
    """

    def __init__(
        self,
        *,
        max_fails: int,
        lock_minutes: int,
        attempt_window_seconds: int,
        ip_max_attempts: int,
        username_max_attempts: int,
        clock: Clock,
        max_keys: int = 1024,
    ) -> None:
        # 配了 0 会让"超限"判断永远不成立——限流器静默失效是最坏的失效方式。
        for name, value in (
            ("max_fails", max_fails),
            ("lock_minutes", lock_minutes),
            ("attempt_window_seconds", attempt_window_seconds),
            ("ip_max_attempts", ip_max_attempts),
            ("username_max_attempts", username_max_attempts),
        ):
            if value <= 0:
                raise ValueError(f"{name} 必须大于 0，现在是 {value}")
        self._max_fails = max_fails
        self._lock_window = timedelta(minutes=lock_minutes)
        self._attempt_window = timedelta(seconds=attempt_window_seconds)
        self._ip_max = ip_max_attempts
        self._user_max = username_max_attempts
        self._clock = clock
        self._max_keys = max_keys
        self._failures: dict[tuple[str, str], list[datetime]] = {}
        self._ip_attempts: dict[str, list[datetime]] = {}
        self._user_attempts: dict[str, list[datetime]] = {}

    @property
    def max_keys(self) -> int:
        return self._max_keys

    @property
    def entry_count(self) -> int:
        """三个表合起来的条目数（测试用来钉"灌不爆内存"）。"""

        return len(self._failures) + len(self._ip_attempts) + len(self._user_attempts)

    @staticmethod
    def _key(ip: str, username: str) -> tuple[str, str]:
        return (ip.strip(), username.strip())

    def _prune(
        self, table: dict[object, list[datetime]], key: object, window: timedelta
    ) -> list[datetime]:
        """切掉窗口外的条目，并顺带做容量淘汰。"""

        now = self._clock.now()
        cutoff = now - window
        kept = [moment for moment in table.get(key, ()) if moment > cutoff]
        if key not in table and len(table) >= self._max_keys:
            table.pop(next(iter(table)), None)  # dict 保插入序 → 最旧
        table[key] = kept
        return kept

    def _retry_after(self, moments: list[datetime], window: timedelta) -> int:
        """最早一个条目的时刻 + 窗口 = 什么时候能再试。向上取整到秒，且至少 1。"""

        now = self._clock.now()
        oldest = min(moments)
        seconds = (oldest + window - now).total_seconds()
        return max(1, math.ceil(seconds))

    def _hit(
        self, table: dict[object, list[datetime]], key: object, limit: int, window: timedelta
    ) -> LoginVerdict:
        moments = self._prune(table, key, window)
        if len(moments) >= limit:
            return LoginVerdict(True, self._retry_after(moments, window))
        moments.append(self._clock.now())
        return LoginVerdict(False, 0)

    async def locked(self, *, ip: str, username: str) -> LoginVerdict:
        """失败锁是否已命中。**命中时不跑 argon2**——限流了就别白烧 CPU。"""

        moments = self._prune(self._failures, self._key(ip, username), self._lock_window)
        if len(moments) >= self._max_fails:
            return LoginVerdict(True, self._retry_after(moments, self._lock_window))
        return LoginVerdict(False, 0)

    async def record_failure(self, *, ip: str, username: str) -> None:
        self._prune(self._failures, self._key(ip, username), self._lock_window).append(
            self._clock.now()
        )

    async def clear(self, *, ip: str, username: str) -> None:
        """登录成功：**只清失败锁**。尝试闸靠时间过期恢复，见类文档。"""

        self._failures.pop(self._key(ip, username), None)

    async def consume_attempt(self, *, ip: str, username: str) -> LoginVerdict:
        """记一次尝试（不论成败），两个速率维度各扣一个令牌。"""

        window = self._attempt_window
        by_ip = self._hit(self._ip_attempts, ip.strip(), self._ip_max, window)
        # 空 IP 时按用户名退化成单维度（拿不到来源 IP 也得有闸门）
        by_user = self._hit(
            self._user_attempts, username.strip(), self._user_max, window
        )
        if by_ip.limited:
            return by_ip
        if by_user.limited:
            return by_user
        return LoginVerdict(False, 0)


class RegisterUser:
    """开放注册，落一个 pending 用户；重名 409。密码在这里哈希（函数由装配点注入）。"""

    def __init__(
        self, *, users: UserStore, hash_password: Callable[[str], str]
    ) -> None:
        self._users = users
        self._hash = hash_password

    async def __call__(self, *, username: str, password: str) -> UserView:
        name = username.strip()
        if not name:
            raise ShapeError("用户名不能为空")
        if not password:
            raise ShapeError("密码不能为空")
        if self._users.get_by_name(name) is not None:
            raise ApplicationError(
                f"用户名已被占用：{name}", code="username_taken", username=name
            )
        user_id = self._users.register(
            username=name, password_hash=self._hash(password)
        )
        return UserView(user_id=user_id, username=name, role="user", status="pending")


class AuthenticateUser:
    """登录：**先过限流闸** → 查人 → 核对密码 → 状态 active 才发会话。

    失败（查无此人 / 密码错 / 非 active）**同一文案、同一耗时**：不存在的人也对
    构造时备好的哨兵哈希跑一次 `verify_password`，堵住用户名枚举侧信道。

    **限流与凭证是两回事，状态码也分开**：

    - **密码错 / 查无此人 / 失败锁命中** → 401。保持不可枚举——加了限流
      也不能把这个改掉。
    - **尝试超限**（per-IP / per-username）→ 429 + `Retry-After`。这是速率闸
      不是凭证闸，客户端要知道等多久。

    **加了 per-username 尝试闸会不会重新引入用户名枚举**——不会，因为计数打在
    **调用方给的那个字符串**上：查都不查库就知道该不该记，`ghost` 与 `alice`
    被同样对待，429 的行为逐字节一致。

    **限流命中时不跑 argon2**：被限流的请求不该继续烧 CPU（更不该让攻击者
    免费用我们的算力）。
    """

    def __init__(
        self,
        *,
        users: UserStore,
        sessions: SessionStore,
        clock: Clock,
        hash_password: Callable[[str], str],
        verify_password: Callable[[str, str], Awaitable[bool]],
        guard: LoginGuard,
        ttl_days: int,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._clock = clock
        self._verify = verify_password
        self._guard = guard
        self._ttl_days = ttl_days
        self._decoy_hash = hash_password("pmstudio-not-a-real-password")

    async def __call__(
        self, *, username: str, password: str, ip: str = "", user_agent: str = ""
    ) -> tuple[UserView, str]:
        name = username.strip()
        # ① 失败锁：**回 401，与密码错完全同码同文案**——锁住这件事本身
        # 不能变成一个可观测的信号。代价是撞库者要多试几轮才停，
        # 但 401 分不出去正是我们要的。不跑 argon2（省 CPU，也省掉一个
        # 免费爆破的口子）。
        locked = await self._guard.locked(ip=ip, username=name)
        if locked.limited:
            raise _unauthenticated()
        # ② 尝试速率闸：**回 429 + Retry-After**。这是速率问题不是凭证问题，
        # 客户端该知道等多久。``consume_attempt`` 必须在 ① 之后——
        # 已经失败锁定的请求不该再去扣速率闸的令牌。
        attempted = await self._guard.consume_attempt(ip=ip, username=name)
        if attempted.limited:
            raise _rate_limited(attempted.retry_after)

        now = self._clock.now()
        record = self._users.get_by_name(name)
        password_hash = record.password_hash if record is not None else self._decoy_hash
        ok = await self._verify(password_hash, password)
        if not ok or record is None or record.status != "active":
            await self._guard.record_failure(ip=ip, username=name)
            raise _unauthenticated()
        await self._guard.clear(ip=ip, username=name)
        token = secrets.token_urlsafe(32)
        self._sessions.create(
            user_id=record.user_id,
            token_hash=_sha256(token),
            expires_at=now + timedelta(days=self._ttl_days),
            now=now,
            ip=ip,
            user_agent=user_agent,
        )
        return _user_view(record), token


class GetMe:
    """cookie 明文 token → 当前用户。过期 / 超绝对上限 / 吊销 / 用户非 active → 401。

    过期判断在这里（仓储不判）：先比 `created_at + 绝对上限`（滑动续期**动不了**这个锚点），
    再比 `expires_at`，最后按半窗做滑动续期（`§0.11`）。
    """

    def __init__(
        self,
        *,
        users: UserStore,
        sessions: SessionStore,
        clock: Clock,
        ttl_days: int,
        absolute_days: int,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._clock = clock
        self._ttl_days = ttl_days
        self._absolute_days = absolute_days

    async def __call__(self, *, token: str) -> tuple[UserView, bool]:
        """回 `(用户, 本轮是否做了滑动续期)`；续期与否供 `/me` 决定要不要重发 cookie。"""

        if not token:
            raise _unauthenticated()
        now = self._clock.now()
        token_hash = _sha256(token)
        session = self._sessions.get_by_token_hash(token_hash)
        if session is None:
            raise _unauthenticated()
        if now - session.created_at > timedelta(days=self._absolute_days):
            # 绝对上限：从建会话起算，滑动续期只动 expires_at，越不过它——到期即吊销**这一条**
            self._sessions.revoke_by_token_hash(token_hash)
            raise _unauthenticated()
        if session.expires_at <= now:
            raise _unauthenticated()
        record = self._users.get_by_id(session.user_id)
        if record is None or record.status != "active":
            raise _unauthenticated()
        renewed = False
        if now - session.last_seen_at > timedelta(days=self._ttl_days) / 2:
            self._sessions.touch(
                token_hash, now=now, expires_at=now + timedelta(days=self._ttl_days)
            )
            renewed = True
        return _user_view(record), renewed


class LogoutUser:
    """登出只吊销**当前这条**会话（per-device，Ruling 18）；其余设备照旧。幂等。"""

    def __init__(self, *, sessions: SessionStore) -> None:
        self._sessions = sessions

    async def __call__(self, *, token: str) -> None:
        if not token:
            return
        self._sessions.revoke_by_token_hash(_sha256(token))


class ApproveUser:
    """管理员审批：pending → active。仅 admin。"""

    def __init__(self, *, users: UserStore, clock: Clock) -> None:
        self._users = users
        self._clock = clock

    async def __call__(self, *, actor: UserView, user_id: UserId) -> UserView:
        _require_admin(actor)
        if self._users.get_by_id(user_id) is None:
            raise _unknown_user(user_id)
        self._users.set_status(
            user_id,
            "active",
            approved_by=str(actor.user_id),
            approved_at=self._clock.now(),
        )
        updated = self._users.get_by_id(user_id)
        if updated is None:
            raise _unknown_user(user_id)
        return _user_view(updated)


class RejectUser:
    """管理员拒绝：pending → rejected（此后登录一律 401）。仅 admin。"""

    def __init__(self, *, users: UserStore) -> None:
        self._users = users

    async def __call__(self, *, actor: UserView, user_id: UserId) -> UserView:
        _require_admin(actor)
        target = self._users.get_by_id(user_id)
        if target is None:
            raise _unknown_user(user_id)
        _admin_is_protected(_user_view(target))
        self._users.set_status(user_id, "rejected")
        updated = self._users.get_by_id(user_id)
        if updated is None:
            raise _unknown_user(user_id)
        return _user_view(updated)


class BanUser:
    """管理员封禁：置 banned 并**当场吊销全部会话**——下一次请求即 401。仅 admin。"""

    def __init__(self, *, users: UserStore, sessions: SessionStore) -> None:
        self._users = users
        self._sessions = sessions

    async def __call__(self, *, actor: UserView, user_id: UserId) -> UserView:
        _require_admin(actor)
        target = self._users.get_by_id(user_id)
        if target is None:
            raise _unknown_user(user_id)
        _admin_is_protected(_user_view(target))
        self._users.set_status(user_id, "banned")
        self._sessions.revoke_user(user_id)
        updated = self._users.get_by_id(user_id)
        if updated is None:
            raise _unknown_user(user_id)
        return _user_view(updated)


class StatsView:
    """一个用户的统计投影（`/api/auth/stats` 的一行）。"""

    __slots__ = (
        "user_id",
        "username",
        "role",
        "status",
        "projects",
        "rounds",
        "calls",
        "failed",
        "avg_duration_ms",
        "tool_calls",
        "tool_failed",
        "tool_avg_duration_ms",
        "total_tokens",
        "active_sessions",
        "last_seen_at",
        "created_at",
        "approved_at",
        "approved_by",
    )

    def __init__(self, **kwargs: object) -> None:
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name))


class GetStats:
    """管理员看每人的活动。**只读**：不返回 `user_id` 之外的身份凭据，也**不给**删除入口。

    左是**全部用户**（按注册先后）——用量/项目/会话只补数不裁人，否则"某人没数据"
    会被误读成"这个人不存在"。数据本身来自读模型仓储，用例只做身份校验与拼装。
    """

    def __init__(self, *, users: UserStore, stats: object, clock: Clock) -> None:
        self._users = users
        self._stats = stats
        self._clock = clock

    async def __call__(self, *, actor: UserView) -> tuple[StatsView, ...]:
        _require_admin(actor)
        now = self._clock.now().isoformat()
        rows = self._stats.rows(now=now)  # type: ignore[attr-defined]
        out: list[StatsView] = []
        for row in rows:
            # 用户名从 users 表取（那是唯一有名字的地方）；读模型里没有的账号跳过——
            # 两者同库同事务，正常不会发生，但不许为一个理论缺口编出空行。
            record = self._users.get_by_id(UserId(row.user_id))  # type: ignore[attr-defined]
            if record is None:
                continue
            out.append(
                StatsView(
                    user_id=str(row.user_id),  # type: ignore[attr-defined]
                    username=record.username,
                    role=row.role,  # type: ignore[attr-defined]
                    status=row.status,  # type: ignore[attr-defined]
                    projects=row.projects,  # type: ignore[attr-defined]
                    rounds=row.rounds,  # type: ignore[attr-defined]
                    calls=row.calls,  # type: ignore[attr-defined]
                    failed=row.failed,  # type: ignore[attr-defined]
                    avg_duration_ms=row.avg_duration_ms,  # type: ignore[attr-defined]
                    tool_calls=row.tool_calls,  # type: ignore[attr-defined]
                    tool_failed=row.tool_failed,  # type: ignore[attr-defined]
                    tool_avg_duration_ms=row.tool_avg_duration_ms,  # type: ignore[attr-defined]
                    total_tokens=row.total_tokens,  # type: ignore[attr-defined]
                    active_sessions=row.active_sessions,  # type: ignore[attr-defined]
                    last_seen_at=row.last_seen_at,  # type: ignore[attr-defined]
                    created_at=row.created_at,  # type: ignore[attr-defined]
                    approved_at=row.approved_at,  # type: ignore[attr-defined]
                    approved_by=row.approved_by,  # type: ignore[attr-defined]
                )
            )
        return tuple(out)


class ListUsers:
    """管理员看全部用户（基础字段）。仅 admin。"""

    def __init__(self, *, users: UserStore) -> None:
        self._users = users

    async def __call__(self, *, actor: UserView) -> tuple[UserView, ...]:
        _require_admin(actor)
        return tuple(_user_view(record) for record in self._users.list_all())


@dataclass(frozen=True, slots=True)
class UsageView:
    """一个用户的用量投影：用户名 + 其项目下轮次的模型调用行数（Task 8 / Ruling 7）。"""

    user_id: UserId
    username: str
    calls: int


class GetUsage:
    """管理员看每个用户的模型调用数。仅 admin。

    口径：按 owner 聚合其轮次的 `model_calls` 行数（join 取，`model_calls` 不加列）。
    没有调用的用户计 0——左侧是**全部用户**（`users.list_all`），用量只补数不裁人。
    """

    def __init__(self, *, users: UserStore, usage: UsageStore) -> None:
        self._users = users
        self._usage = usage

    async def __call__(self, *, actor: UserView) -> tuple[UsageView, ...]:
        _require_admin(actor)
        counts = self._usage.count_model_calls_by_owner()
        return tuple(
            UsageView(
                user_id=record.user_id,
                username=record.username,
                calls=counts.get(record.user_id, 0),
            )
            for record in self._users.list_all()
        )
