"""唯一组装点。infrastructure 在这里被注入到 application，别处不许 new 实现。"""
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from pmstudio.api.app import ApiServices, BackgroundResource, create_app
from pmstudio.api.auth import AuthServices
from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.discusser import Discusser
from pmstudio.application.agents.inquirer import Inquirer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.memory_curator import MemoryCurator
from pmstudio.application.agents.persona_simulator import PersonaSimulatorAgent
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.agents.sim_inquirer import SimInquiryAgent
from pmstudio.application.ports import Clock, LoginGuard, WebFetcher, WebSearchGateway
from pmstudio.application.use_cases import (
    AbandonExploration,
    AbandonRound,
    ApproveUser,
    AssembleCards,
    AttachMaterials,
    AuthenticateUser,
    BanUser,
    CloseDiscussion,
    ConfirmDraft,
    CreateProject,
    CreateTemplate,
    DecideWebRequest,
    DeleteMaterial,
    DeleteTemplate,
    DigRound,
    DismissCandidate,
    DismissDiscussionCard,
    EditEntry,
    ExportMarkdown,
    FetchMaterial,
    ForgetMemory,
    GetDocument,
    GetEvidence,
    GetMe,
    GetMemoryUsage,
    GetRound,
    GetRoundCalls,
    GetSchema,
    GetStats,
    GetTemplate,
    GetUsage,
    GetVersionDiff,
    GetVersions,
    GetWorkspace,
    IntegrateRound,
    JoinPanel,
    KeepCandidate,
    KeepDiscussionCard,
    KeepSimInquiry,
    ListDiscussions,
    ListExplorations,
    ListMaterials,
    ListProjects,
    ListRounds,
    ListTemplates,
    ListUsers,
    LoginThrottle,
    LogoutUser,
    RecoverWebRequest,
    RegisterUser,
    RejectUser,
    ReparseMaterial,
    RetryExploration,
    RetryRound,
    RollbackVersion,
    RoundContextPreparer,
    RunInquiry,
    RunPanelRound,
    RunSeatRound,
    RunSimInquiry,
    RunSimReview,
    SayToThread,
    SearchProject,
    SkipSeat,
    StartDiscussion,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
    UpdateTemplate,
    UploadMaterial,
    web_search_tool,
)
from pmstudio.application.web_research import WebResearch
from pmstudio.bootstrap.maintenance import MaterialMaintenance
from pmstudio.bootstrap.settings import AppSettings
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.null_repos import NoOpVectorIndex
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.services.committer import ProposalCommitter
from pmstudio.domain.services.scope_guard import ScopeGuard
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.harness.events import InProcessEventBus
from pmstudio.harness.model.contracts import ModelGateway
from pmstudio.harness.model.providers.litellm import LiteLLMGateway
from pmstudio.harness.prompt import PROMPTS_DIR, FilePromptLibrary
from pmstudio.harness.tools.registry import DEFINITIONS_DIR, TomlToolCatalog
from pmstudio.infrastructure.discussion.roles import load_discussion_roles
from pmstudio.infrastructure.discussion.runner import DiscussionRunner
from pmstudio.infrastructure.graph.exploration_runner import ExplorationRunner
from pmstudio.infrastructure.graph.graph import RoundRunner
from pmstudio.infrastructure.personas.catalog import load_personas
from pmstudio.infrastructure.postgres.database import PgConnection
from pmstudio.infrastructure.postgres.unit_of_work import PgUnitOfWork
from pmstudio.infrastructure.seats.catalog import TomlSeatCatalog
from pmstudio.infrastructure.security import (
    configure_gate,
    hash_password,
    verify_password_async,
)
from pmstudio.infrastructure.sqlite import owner_scope
from pmstudio.infrastructure.sqlite.call_log import SqliteRoundCallLog
from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.sqlite.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.sqlite.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.sqlite.leases import SqliteLeaseStore
from pmstudio.infrastructure.sqlite.materials import (
    SqliteFtsRetriever,
    SqliteHybridRetriever,
    SqliteMaterialStore,
)
from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder
from pmstudio.infrastructure.sqlite.observations_repo import SqliteObservationRepository
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDiscussionRepository,
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteExplorationRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteTemplateRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.tokenize import warm as warm_tokenizer
from pmstudio.infrastructure.sqlite.tool_calls import SqliteToolCallRecorder
from pmstudio.infrastructure.sqlite.usage import (
    SqliteAdminStatsRepository,
    SqliteUsageRepository,
)
from pmstudio.infrastructure.sqlite.users import (
    SqliteSessionRepository,
    SqliteUserRepository,
)
from pmstudio.infrastructure.templates.loader import seed_builtin_templates
from pmstudio.infrastructure.tools.extract import LocalMaterialParser
from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher
from pmstudio.infrastructure.tools.tavily import TavilySearchGateway

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
#: 提示词与工具声明目录**从 harness 那边 import**，不在这里拼：
#: 路径跟着它指向的资源走，搬目录时改一处就够（自己拼的话，搬完只会有"碰巧用到
#: 那一处"的调用者报错，其余静默指向空目录）。
#: 席位目录暂时还住在外层，所以 `SEATS_DIR` 仍由这里拼——**它迟早也要跟着搬**
#: （`tests/architecture/test_resource_paths.py` 那条守卫盯着这件事）。
SEATS_DIR = PACKAGE_ROOT / "infrastructure" / "seats" / "definitions"
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
class UuidIdGenerator:
    def new(self, prefix: str) -> str:
        return f"{prefix}-{uuid.uuid4().hex[:8]}"
class Container:
    def __init__(self, *, settings: AppSettings, app: FastAPI) -> None:
        self.settings = settings
        self.app = app
def _model_gateway(
    settings: AppSettings, *, recorder: SqliteModelCallRecorder | None = None
) -> LiteLLMGateway:
    """造生产用的模型网关。

    **为什么抽成函数**：`build_container` 里原来内联这一段，
    而 `scripts/mcp_probe.py` 需要**同一个**网关（不能自己再造一个——
    那样留样、韧性参数、供应商参数会各走各的，测出来的不是生产行为）。
    一处实现、两处调用。
    """
    return LiteLLMGateway(
        model=settings.model,
        api_key=settings.api_key,
        api_base=settings.api_base,
        timeout=settings.model_timeout,
        recorder=recorder,
        model_params=settings.model_params,
    )


def _web_search_gateway(
    settings: AppSettings, *, tools: TomlToolCatalog, keys: frozenset[str]
) -> TavilySearchGateway | None:
    """两件事都成立才造网关：**目录说这个工具可用**（`requires` 都配上了）
    且密钥确实在手上。缺一件就是 None——用例那边会把"勾了但没有工具"当场拒掉，
    而不是拿一个空 key 去请求，更不是假装搜过。"""
    if web_search_tool(tools, keys=keys) is None or settings.tavily_api_key is None:
        return None
    return TavilySearchGateway(api_key=settings.tavily_api_key)
def _build_event_bus(settings: AppSettings):  # noqa: ANN202 —— 返回两种实现之一
    """按配置选事件总线：Redis 跨进程扇出，或进程内总线。

    **不做"Redis 配错就起不来"**：与登录限流同一条规矩——Redis 连不上时
    `RedisFanoutEventBus` 自己会降级到进程内总线，所以这里只在**配都没配**时
    直接给进程内的（单 worker 开发够用）。
    """
    if not settings.redis_url:
        return InProcessEventBus()

    from pmstudio.infrastructure.redis_bus import RedisFanoutEventBus

    return RedisFanoutEventBus(
        url=settings.redis_url,
        socket_timeout=settings.redis_timeout,
        max_connections=settings.redis_max_connections,
    )


def _build_login_guard(settings: AppSettings, clock: Clock) -> LoginGuard:
    """按配置选登录限流闸：Redis 共享计数，或进程内计数。

    **不做"Redis 配错就起不来"**：限流器不可用与限流器不存在是两回事，
    后者更糟。Redis 连接失败时 `RedisLoginGuard` 自己会降级到进程内计数，
    所以这里只在**配都没配**时退回内存闸。
    """
    limits = {
        "max_fails": settings.login_max_fails,
        "lock_minutes": settings.login_lock_minutes,
        "attempt_window_seconds": settings.login_attempt_window_seconds,
        "ip_max_attempts": settings.login_ip_max_attempts,
        "username_max_attempts": settings.login_username_max_attempts,
    }
    if not settings.redis_url:
        return LoginThrottle(clock=clock, **limits)

    from limits.aio.storage import RedisStorage
    from limits.aio.strategies import MovingWindowRateLimiter

    from pmstudio.infrastructure.redis_limiter import RedisLoginGuard

    storage = RedisStorage(
        # `async+` 前缀 = 用 redis-py 的 asyncio 客户端；不写会默认走 coredis
        #（另一个库，行为和超时配置都不在我们手里）。
        f"async+{settings.redis_url}",
        implementation="redispy",
        socket_timeout=settings.redis_timeout,
        socket_connect_timeout=settings.redis_timeout,
        max_connections=settings.redis_max_connections,
    )
    return RedisLoginGuard(
        limiter=MovingWindowRateLimiter(storage),
        fallback=LoginThrottle(clock=clock, **limits),
        **limits,
    )


def build_container(
    settings: AppSettings,
    *,
    gateway: ModelGateway | None = None,
    web_search: WebSearchGateway | None = None,
    fetcher: WebFetcher | None = None,
) -> Container:
    """唯一组装点。
    `gateway` / `web_search` / `fetcher` 是**给测试用的注入口**（端到端测试注入脚本化
    网关与工具替身）。生产路径（`build_app`）永远自己 new 真实现，不提供"用假实现"的
    开关——这一点由 `tests/architecture/test_no_fakes_in_production.py` 与
    `tests/bootstrap/test_container.py::test_container_has_no_fake_model_switch` 守着。
    """
    # 登录哈希隔离舱（高并发 P1）：容量来自 settings，**必须在事件循环跑起来之前**设。
    # argon2 每次约 64MB，这个数同时是内存上限——登录风暴排队，而不是把机器打死。
    configure_gate(settings.login_hash_concurrency)

    # 中文分词预热（检索 P1）：jieba 词典首次 `cut` 要 ~0.6s——组装时加载一次，
    # 别落在首个搜索/上传请求里。幂等，多个测试进程各付一次（本就如此）。
    warm_tokenizer()

    # 向量 embedder 单一实例：懒加载（首次 `embed_many` 才下载/载入模型），
    # 迁移回填与向量索引共用同一个，避免同一进程里装两份模型内存。
    embedder = FastEmbedEmbedder()
    connection = PgConnection(settings.database_url)
    clock = SystemClock()
    ids = UuidIdGenerator()
    bus = _build_event_bus(settings)
    users = SqliteUserRepository(connection)
    sessions = SqliteSessionRepository(connection)
    # 管理端用量读模型（Task 8 / Ruling 7）：只读 join，无写能力
    usage = SqliteUsageRepository(connection)
    # 管理后台那一屏的读模型（`06` §2.4）：每用户一行，只读，同样无写能力
    admin_stats = SqliteAdminStatsRepository(connection)
    documents = SqliteDocumentRepository(connection)
    # 轮 / 探索的 DB 租约（P3b 多 worker）：同一个 store 交给两个仓储，
    # 保证 fencing 与 runner 心跳用的是同一套 SQL/口径。
    leases = SqliteLeaseStore(connection)
    rounds = SqliteRoundRepository(connection, leases=leases)
    explorations = SqliteExplorationRepository(connection, leases=leases)
    discussions = SqliteDiscussionRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    materials = SqliteMaterialStore(connection, ids=ids)
    # 向量索引在 T5 迁到 pgvector；T1 只立运行时骨架，先保持 NoOp。
    vector_index = NoOpVectorIndex()
    # 材料检索（检索 P1 + 阶段 2）：FTS5 词法 + jieba 预分词，向量路再叠一路 RRF 融合。
    # 换实现只改这一行（`P18` C-2c-1）——`MaterialRetriever` 协议的升级位就在这儿。
    # `SqliteHybridRetriever` 内部对向量路做了降级：扩展缺失/推理失败就退回纯 FTS5，
    # 检索永远有结果，所以这里不需要再分叉。
    material_search = SqliteHybridRetriever(connection, embedder=embedder)
    #: 顶栏搜索**只做关键词**（P1.1）：不碰 embedding、不碰融合、不占向量预算。
    #: 轮内上下文仍用 `material_search`（hybrid）——两条路各有各的消费者。
    search_lookup = SqliteFtsRetriever(connection)
    parser = LocalMaterialParser()
    # 按网址抓网页（P27）：真 httpx，护栏在实现里（只抓公开网页、限 3 跳 / 15s / 5MB）。
    # 与解析器一样，换实现（无头浏览器 / 更强的正文抽取）只改这一行。
    if fetcher is None:
        fetcher = HttpxWebFetcher()
    index = SqliteWorkspaceIndex(connection)
    memory = SqliteMemoryRepository(connection)
    observations_repo = SqliteObservationRepository(connection)
    digest_repo = SqliteDigestRepository(connection)
    inspiration_repo = SqliteInspirationRepository(connection)
    template_repo = SqliteTemplateRepository(connection)
    # 内置模板 seed（幂等 upsert）：TOML 是定义源，表是运行时源。
    # 改 TOML 发版即改内置；自定义模板（builtin=0）id 不同，seed 碰不到。
    # 老项目行内存的是全文快照，库里怎么变都不影响它们。
    seed_builtin_templates(template_repo)
    uow = PgUnitOfWork(connection)
    prompts = FilePromptLibrary(PROMPTS_DIR)
    if gateway is None:
        # 留样默认关（`PMSTUDIO_TRACE_MODEL=1` 才接）：库会长，且它是排查材料不是业务事实
        recorder = SqliteModelCallRecorder(connection) if settings.trace_model else None
        gateway = _model_gateway(settings, recorder=recorder)
    # 工具留样与模型留样同一条纪律：默认不写（`PMSTUDIO_TRACE_MODEL=1` 才接）。
    # **必须在 `if gateway is None:` 块外造**：`gateway` 被注入时（端到端测试）那个
    # 局部变量不存在，照抄块内写法会 `NameError`。
    tool_recorder = SqliteToolCallRecorder(connection) if settings.trace_model else None
    catalog = TomlSeatCatalog(SEATS_DIR)
    discussion_roles = load_discussion_roles()
    personas = load_personas()
    # 工具目录：`requires` 没配上的工具**直接不可用**（不是"无密钥降级"）
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    available_tool_keys = settings.available_tool_keys()
    if web_search is None:
        web_search = _web_search_gateway(settings, tools=tools, keys=available_tool_keys)
    assigner = EvidenceAssigner()
    readiness = ReadinessChecker()
    analyzer = ClaimAnalyzer(model=gateway, prompts=prompts, catalog=catalog)
    reviewer = SeatReviewer(model=gateway, prompts=prompts)
    inquirer = Inquirer(model=gateway, prompts=prompts)
    sim_inquirer = SimInquiryAgent(model=gateway, prompts=prompts)
    persona_simulator = PersonaSimulatorAgent(model=gateway, prompts=prompts)
    discusser = Discusser(model=gateway, prompts=prompts)
    integrator = Integrator(model=gateway, prompts=prompts)
    curator = MemoryCurator(model=gateway, prompts=prompts)
    factory = SqliteProjectFactory(connection, ids=ids)
    start_round = StartRound(
        documents=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        analyzer=analyzer,
        assigner=assigner,
        checker=readiness,
        clock=clock,
        ids=ids,
        events=bus,
        materials=materials,
        explorations=explorations,
        discussions=discussions,
        # **同一个**检索器也交给建轮：建轮时用它宽召回、再按分数收敛进预算（stage 2）。
        # 不另造一个实例——两处口径必须一致（换实现也只改 `material_search` 那一行）。
        retriever=material_search,
        # 建轮 INSERT 与 acquire 同 UoW（P3b）：跑起来之前就有一把 DB lease。
        leases=leases,
    )
    # 联网执行层：opening 与席位/整合共用同一个实例（单 worker 口径）。
    web_research = WebResearch(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=materials,
        tools=tools,
        available_tool_keys=available_tool_keys,
        web_search=web_search,
        index=index,
        uow=uow,
        clock=clock,
        vectors=vector_index,
        tool_recorder=tool_recorder,
    )
    submit_opening = SubmitOpening(
        rounds=rounds,
        round_writer=rounds,
        catalog=catalog,
        tools=tools,
        available_tool_keys=available_tool_keys,
        web_search=web_search,
        evidence=evidences,
        evidence_writer=evidences,
        materials=materials,
        # 判"这份材料被哪些编号引用过"（被引用过的不就地改写——编号指着它里面的块）
        index=index,
        uow=uow,
        clock=clock,
        events=bus,
        # 联网结果也要有向量（否则"换个说法搜它"搜不到——外部来源恰恰最需要语义召回）
        vectors=vector_index,
        # 工具留样（可观测性 v2）：默认 None = 什么都不记
        tool_recorder=tool_recorder,
        web_research=web_research,
    )
    # 每步投影：席位轮/整合共用同一个实例（无状态，只读装配依赖）。
    round_context = RoundContextPreparer(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        materials=materials,
        retriever=material_search,
        uow=uow,
        clock=clock,
    )
    run_seat_round = RunSeatRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        reviewer=reviewer,
        catalog=catalog,
        assigner=assigner,
        checker=readiness,
        clock=clock,
        events=bus,
        materials=materials,
        memory=memory,
        observations_repo=observations_repo,
        preparer=round_context,
        web_research=web_research,
        leases=leases,
    )
    integrate_round = IntegrateRound(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        uow=uow,
        integrator=integrator,
        catalog=catalog,
        index=index,
        checker=readiness,
        clock=clock,
        ids=ids,
        events=bus,
        materials=materials,
        curator=curator,
        memory=memory,
        observations_repo=observations_repo,
        digest_repo=digest_repo,
        inspiration_repo=inspiration_repo,
        preparer=round_context,
        web_research=web_research,
    )
    submit_answers = SubmitAnswers(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        catalog=catalog,
        uow=uow,
        clock=clock,
        events=bus,
    )
    committer = ProposalCommitter(
        documents=documents,
        writer=documents,
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        scope=ScopeGuard(),
        traceability=TraceabilityChecker(),
        uow=uow,
        # 跨轮记忆（M6）：与文档**同一个事务**写；`ids` 只用来给新条目发 id
        memory=memory,
        memory_writer=memory,
        ids=ids,
    )
    confirm_draft = ConfirmDraft(
        committer=committer,
        rounds=rounds,
        clock=clock,
        events=bus,
        digest_repo=digest_repo,
    )
    retry_round = RetryRound(
        rounds=rounds, round_writer=rounds, uow=uow, clock=clock, events=bus
    )
    dig_round = DigRound(rounds=rounds, round_writer=rounds, uow=uow, clock=clock, events=bus)
    run_sim_inquiry = RunSimInquiry(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        memory=memory,
        materials=materials,
        uow=uow,
        sim_inquirer=sim_inquirer,
        checker=readiness,
        clock=clock,
        events=bus,
    )
    keep_sim_inquiry = KeepSimInquiry(
        rounds=rounds,
        round_writer=rounds,
        materials=materials,
        uow=uow,
        clock=clock,
        inspiration_repo=inspiration_repo,
    )
    run_sim_review = RunSimReview(
        rounds=rounds,
        round_writer=rounds,
        documents=documents,
        evidence=evidences,
        evidence_writer=evidences,
        memory=memory,
        materials=materials,
        uow=uow,
        personas=personas,
        simulator=persona_simulator,
        checker=readiness,
        clock=clock,
        events=bus,
    )
    get_round = GetRound(
        rounds=rounds,
        evidence=evidences,
        catalog=catalog,
        tools=tools,
        available_tool_keys=available_tool_keys,
        materials=materials,
    )
    # 这一轮的外部调用轨迹（模型 + 工具，时间序）：归属由用例先 `rounds.get` 判
    get_round_calls = GetRoundCalls(
        rounds=rounds, call_log=SqliteRoundCallLog(connection)
    )
    attach_materials = AttachMaterials(
        rounds=rounds, round_writer=rounds, materials=materials, uow=uow
    )
    decide_web_request = DecideWebRequest(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        tools=tools,
        available_tool_keys=available_tool_keys,
        web_research=web_research,
        uow=uow,
        clock=clock,
        events=bus,
    )
    recover_web_request = RecoverWebRequest(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
        tools=tools,
        available_tool_keys=available_tool_keys,
        web_research=web_research,
        uow=uow,
        clock=clock,
        events=bus,
    )
    runner = RoundRunner(
        start_round=start_round,
        submit_opening=submit_opening,
        run_seat_round=run_seat_round,
        integrate_round=integrate_round,
        submit_answers=submit_answers,
        confirm_draft=confirm_draft,
        retry_round=retry_round,
        dig_round=dig_round,
        get_round=get_round,
        run_sim_inquiry=run_sim_inquiry,
        run_sim_review=run_sim_review,
        attach_materials=attach_materials,
        decide_web_request=decide_web_request,
        recover_web_request=recover_web_request,
        # 跨进程互斥的唯一真相源；心跳 30s / TTL 90s（`application/lease.py`）。
        leases=leases,
    )
    run_inquiry = RunInquiry(
        documents=documents,
        explorations=explorations,
        exploration_writer=explorations,
        evidence=evidences,
        memory=memory,
        uow=uow,
        inquirer=inquirer,
        checker=readiness,
        clock=clock,
        ids=ids,
        events=bus,
        # create 在同一 UoW INSERT + acquire；不注入就退化成无租约。
        leases=leases,
    )
    retry_exploration = RetryExploration(
        explorations=explorations,
        exploration_writer=explorations,
        uow=uow,
        clock=clock,
        events=bus,
        # resume 同 UoW 置 RUNNING + acquire。
        leases=leases,
    )
    exploration_runner = ExplorationRunner(
        run_inquiry=run_inquiry,
        retry_exploration=retry_exploration,
        explorations=explorations,
        # 心跳续租 / 失租 409 的唯一真相源。
        leases=leases,
    )
    start_discussion = StartDiscussion(
        documents=documents,
        discussions=discussions,
        discussion_writer=discussions,
        explorations=explorations,
        uow=uow,
        clock=clock,
        ids=ids,
        events=bus,
        catalog=catalog,
        discussion_roles=discussion_roles,
    )
    say_to_thread = SayToThread(
        documents=documents,
        discussions=discussions,
        discussion_writer=discussions,
        evidence=evidences,
        explorations=explorations,
        uow=uow,
        discusser=discusser,
        checker=readiness,
        clock=clock,
        events=bus,
        catalog=catalog,
        discussion_roles=discussion_roles,
    )
    assemble_cards = AssembleCards(
        documents=documents,
        discussions=discussions,
        discussion_writer=discussions,
        evidence=evidences,
        uow=uow,
        discusser=discusser,
        checker=readiness,
        clock=clock,
        events=bus,
        catalog=catalog,
        discussion_roles=discussion_roles,
    )
    run_panel_round = RunPanelRound(
        documents=documents,
        discussions=discussions,
        discussion_writer=discussions,
        evidence=evidences,
        explorations=explorations,
        uow=uow,
        discusser=discusser,
        checker=readiness,
        clock=clock,
        events=bus,
        catalog=catalog,
        discussion_roles=discussion_roles,
    )
    join_panel = JoinPanel(
        discussions=discussions,
        discussion_writer=discussions,
        uow=uow,
        clock=clock,
        events=bus,
        catalog=catalog,
        discussion_roles=discussion_roles,
    )
    discussion_runner = DiscussionRunner(
        run_panel_round=run_panel_round, discussions=discussions
    )
    services = ApiServices(
        runner=runner,
        recover_web_request=recover_web_request,
        dig_round=dig_round,
        abandon_round=AbandonRound(
            rounds=rounds,
            round_writer=rounds,
            uow=uow,
            clock=clock,
            events=bus,
            canceller=runner,
            # force_acquire + 同一 UoW 写 ABANDONED：旧 worker 的下一次 save 会被 fence。
            leases=leases,
        ),
        skip_seat=SkipSeat(
            rounds=rounds,
            skips=rounds,
            uow=uow,
            events=bus,
            clock=clock,
            viewer=get_round,
        ),
        exploration_runner=exploration_runner,
        abandon_exploration=AbandonExploration(
            explorations=explorations,
            exploration_writer=explorations,
            uow=uow,
            clock=clock,
            events=bus,
            canceller=exploration_runner,
            # force_acquire + 同一 UoW 写 ABANDONED：旧 worker 的下一次 save 被 fence。
            leases=leases,
        ),
        keep_candidate=KeepCandidate(
            explorations=explorations,
            exploration_writer=explorations,
            materials=materials,
            uow=uow,
            clock=clock,
            inspiration_repo=inspiration_repo,
        ),
        dismiss_candidate=DismissCandidate(
            explorations=explorations,
            exploration_writer=explorations,
            uow=uow,
        ),
        list_explorations=ListExplorations(explorations=explorations),
        start_discussion=start_discussion,
        say_to_thread=say_to_thread,
        assemble_cards=assemble_cards,
        join_panel=join_panel,
        discussion_runner=discussion_runner,
        keep_card=KeepDiscussionCard(
            discussions=discussions,
            discussion_writer=discussions,
            materials=materials,
            uow=uow,
            clock=clock,
            inspiration_repo=inspiration_repo,
        ),
        dismiss_card=DismissDiscussionCard(
            discussions=discussions,
            discussion_writer=discussions,
            uow=uow,
        ),
        keep_sim_inquiry=keep_sim_inquiry,
        close_discussion=CloseDiscussion(
            discussions=discussions,
            discussion_writer=discussions,
            uow=uow,
            clock=clock,
            events=bus,
        ),
        list_discussions=ListDiscussions(discussions=discussions),
        create_project=CreateProject(
            factory=factory, index=index, clock=clock, ids=ids, templates=template_repo
        ),
        create_template=CreateTemplate(templates=template_repo, ids=ids),
        list_templates=ListTemplates(templates=template_repo),
        get_template=GetTemplate(templates=template_repo),
        update_template=UpdateTemplate(templates=template_repo),
        delete_template=DeleteTemplate(templates=template_repo),
        workspace=GetWorkspace(
            documents=documents,
            rounds=rounds,
            evidence=evidences,
            index=index,
            catalog=catalog,
            tools=tools,
            available_tool_keys=available_tool_keys,
            checker=readiness,
            materials=materials,
            memory=memory,
        ),
        list_projects=ListProjects(index=index),
        get_round=get_round,
        get_round_calls=get_round_calls,
        # 复用 175 行已加载的那份角色表（同一进程只 `load_discussion_roles()` 一次）
        get_schema=GetSchema(
            discussion_roles=discussion_roles,
            personas=personas,
            tools=tools,
            available_tool_keys=available_tool_keys,
            # 取 `.template`：`schema_view` 按鸭子类型读 DocumentTemplate（.template_id/
            # .title/.sections[]），/schema 只投影结构、不需要 builtin 标记。
            templates=tuple(entry.template for entry in template_repo.list()),
        ),
        list_rounds=ListRounds(index=index),
        search=SearchProject(
            documents=documents, evidence=evidences, index=index, materials=search_lookup
        ),
        upload_material=UploadMaterial(
            documents=documents,
            materials=materials,
            parser=parser,
            clock=clock,
            uow=uow,
            vectors=vector_index,
        ),
        list_materials=ListMaterials(materials=materials, parser=parser, index=index),
        fetch_material=FetchMaterial(
            documents=documents,
            materials=materials,
            parser=parser,
            fetcher=fetcher,
            index=index,
            clock=clock,
            uow=uow,
            vectors=vector_index,
        ),
        delete_material=DeleteMaterial(
            materials=materials, parser=parser, index=index, uow=uow, vectors=vector_index
        ),
        reparse_material=ReparseMaterial(
            materials=materials,
            parser=parser,
            index=index,
            uow=uow,
            clock=clock,
            vectors=vector_index,
        ),
        get_document=GetDocument(documents=documents),
        export_markdown=ExportMarkdown(
            documents=documents, evidence=evidences, checker=readiness, clock=clock
        ),
        edit_entry=EditEntry(
            documents=documents,
            evidence=evidences,
            evidence_writer=evidences,
            committer=committer,
            uow=uow,
            clock=clock,
            ids=ids,
            events=bus,
        ),
        get_versions=GetVersions(documents=documents),
        get_version_diff=GetVersionDiff(documents=documents),
        rollback_version=RollbackVersion(
            committer=committer, documents=documents, clock=clock, events=bus
        ),
        get_evidence=GetEvidence(documents=documents, evidence=evidences),
        forget_memory=ForgetMemory(
            memory=memory, memory_writer=memory, uow=uow, clock=clock
        ),
        memory_usage=GetMemoryUsage(memory=memory, index=index, rounds=rounds),
        stream=bus,
        model_name=settings.model,
    )
    # 登录限流闸（高并发 P3）。**配了 Redis 就跨 worker 共享一份计数**：
    # 进程内计数在多 worker 下等于"除以 N"——4 个 worker 各给 5 次就是 20 次，
    # 而且重启清零。没配 Redis 就用进程内的（单 worker 开发够用，dev.py 会警告）。
    login_guard = _build_login_guard(settings, clock)

    auth_services = AuthServices(
        register_user=RegisterUser(users=users, hash_password=hash_password),
        authenticate_user=AuthenticateUser(
            users=users,
            sessions=sessions,
            clock=clock,
            hash_password=hash_password,
            verify_password=verify_password_async,
            guard=login_guard,
            ttl_days=settings.session_ttl_days,
        ),
        get_me=GetMe(
            users=users,
            sessions=sessions,
            clock=clock,
            ttl_days=settings.session_ttl_days,
            absolute_days=settings.session_absolute_days,
        ),
        logout=LogoutUser(sessions=sessions),
        approve_user=ApproveUser(users=users, clock=clock),
        reject_user=RejectUser(users=users),
        ban_user=BanUser(users=users, sessions=sessions),
        list_users=ListUsers(users=users),
        get_usage=GetUsage(users=users, usage=usage),
        get_stats=GetStats(users=users, stats=admin_stats, clock=clock),
        users=users,
        sessions=sessions,
        cookie_max_age=settings.session_ttl_days * 86400,
    )
    # 只有**真有生命周期**的总线才交给 lifespan。`BackgroundResource` 表达的是
    # "谁创建谁负责关"——进程内总线没有可关的东西，它本来就不是；照传会让 lifespan
    # 去 `await bus.start()`（不存在）而把服务整个带崩。
    background = bus if isinstance(bus, BackgroundResource) else None
    # **材料维护**（批 7）：把块结构已过时的材料按新块宽重切、重算向量。
    # 原始字节在库 → 不需要网络；后台跑 → 不阻塞启动。
    maintenance = MaterialMaintenance(
        connection=connection,
        parser=parser,
        store=materials,
        vectors=vector_index,
        owner_scope=owner_scope,
        index=index,
    )
    app = create_app(services, background=background, maintenance=maintenance)
    app.state.auth = auth_services
    # owner 上下文由 api 层（`require_user` / SSE 归属过滤）经 `app.state` 取用：
    # api 不许 import infrastructure（分层守卫），装配点把实现注进去（依赖倒置）。
    app.state.owner_scope = owner_scope
    return Container(settings=settings, app=app)
def build_app() -> FastAPI:
    """`uvicorn pmstudio.bootstrap.container:build_app --factory` 的入口。"""
    return build_container(AppSettings.from_env()).app
