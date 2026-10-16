"""唯一组装点。infrastructure 在这里被注入到 application，别处不许 new 实现。"""
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from pmstudio.api.app import ApiServices, create_app
from pmstudio.api.auth import AuthServices
from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.discusser import Discusser
from pmstudio.application.agents.inquirer import Inquirer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.memory_curator import MemoryCurator
from pmstudio.application.agents.persona_simulator import PersonaSimulatorAgent
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.agents.sim_inquirer import SimInquiryAgent
from pmstudio.application.ports import WebFetcher
from pmstudio.application.use_cases import (
    AbandonExploration,
    AbandonRound,
    ApproveUser,
    AssembleCards,
    AuthenticateUser,
    BanUser,
    CloseDiscussion,
    ConfirmDraft,
    CreateProject,
    CreateTemplate,
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
    GetRound,
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
    RegisterUser,
    RejectUser,
    ReparseMaterial,
    RetryExploration,
    RetryRound,
    RollbackVersion,
    RunInquiry,
    RunPanelRound,
    RunSeatRound,
    RunSimInquiry,
    RunSimReview,
    SayToThread,
    SearchProject,
    StartDiscussion,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
    UpdateTemplate,
    UploadMaterial,
    web_search_tool,
)
from pmstudio.bootstrap.settings import Settings
from pmstudio.domain.authoring.readiness import ReadinessChecker
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
from pmstudio.infrastructure.seats.catalog import TomlSeatCatalog
from pmstudio.infrastructure.security import hash_password, verify_password
from pmstudio.infrastructure.sqlite import database, owner_scope
from pmstudio.infrastructure.sqlite.digest_repo import SqliteDigestRepository
from pmstudio.infrastructure.sqlite.inspiration_repo import SqliteInspirationRepository
from pmstudio.infrastructure.sqlite.materials import SqliteLikeRetriever, SqliteMaterialStore
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
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
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
    def __init__(self, *, settings: Settings, app: FastAPI) -> None:
        self.settings = settings
        self.app = app
def _web_search_gateway(
    settings: Settings, *, tools: TomlToolCatalog, keys: frozenset[str]
) -> TavilySearchGateway | None:
    """两件事都成立才造网关：**目录说这个工具可用**（`requires` 都配上了）
    且密钥确实在手上。缺一件就是 None——用例那边会把"勾了但没有工具"当场拒掉，
    而不是拿一个空 key 去请求，更不是假装搜过。"""
    if web_search_tool(tools, keys=keys) is None or settings.tavily_api_key is None:
        return None
    return TavilySearchGateway(api_key=settings.tavily_api_key)
def _seed_first_admin(users: SqliteUserRepository, settings: Settings) -> None:
    """首管理员种子：**users 表为空**且 env 给了用户名+密码才建（幂等，非空直接跳过）。

    存量库（已有用户）不动；没配 env 就不建——不写"默认口令"那种假降级。
    """
    if users.list_all():
        return
    if not (settings.first_admin_username and settings.first_admin_password):
        return
    admin_id = users.register(
        username=settings.first_admin_username,
        password_hash=hash_password(settings.first_admin_password),
        role="admin",
    )
    users.set_status(admin_id, "active")
def build_container(settings: Settings, *, gateway: ModelGateway | None = None) -> Container:
    """唯一组装点。
    `gateway` 是**唯一**给测试用的注入口（端到端测试注入脚本化网关）。
    生产路径（`build_app`）永远自己 new 真网关，不提供"用假模型"的开关——
    这一点由 `tests/architecture/test_no_fakes_in_production.py` 与
    `tests/bootstrap/test_container.py::test_container_has_no_fake_model_switch` 守着。
    """
    connection = database.connect(settings.db_path)
    clock = SystemClock()
    ids = UuidIdGenerator()
    bus = InProcessEventBus()
    users = SqliteUserRepository(connection)
    # 次序是契约（Ruling 13）：先种首 admin，再回填存量 owner，最后熔断。
    # 反过来（connect 里熔断）会让"存量内容 + users 空 + FIRST_ADMIN"的首次升级永远卡住。
    _seed_first_admin(users, settings)
    database.backfill_owners(connection)
    database.assert_owners_backfilled(connection)
    sessions = SqliteSessionRepository(connection)
    # 管理端用量读模型（Task 8 / Ruling 7）：只读 join，无写能力
    usage = SqliteUsageRepository(connection)
    # 管理后台那一屏的读模型（`06` §2.4）：每用户一行，只读，同样无写能力
    admin_stats = SqliteAdminStatsRepository(connection)
    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
    explorations = SqliteExplorationRepository(connection)
    discussions = SqliteDiscussionRepository(connection)
    evidences = SqliteEvidenceRepository(connection)
    materials = SqliteMaterialStore(connection, ids=ids)
    # 材料检索 v1：LIKE（中文可靠、零索引）。换 FTS5 / 向量只改这一行（`P18` C-2c-1）
    material_search = SqliteLikeRetriever(connection)
    parser = LocalMaterialParser()
    # 按网址抓网页（P27）：真 httpx，护栏在实现里（只抓公开网页、限 3 跳 / 15s / 5MB）。
    # 与解析器一样，换实现（无头浏览器 / 更强的正文抽取）只改这一行。
    fetcher: WebFetcher = HttpxWebFetcher()
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
    uow = SqliteUnitOfWork(connection)
    prompts = FilePromptLibrary(PROMPTS_DIR)
    if gateway is None:
        # 留样默认关（`PMSTUDIO_TRACE_MODEL=1` 才接）：库会长，且它是排查材料不是业务事实
        recorder = SqliteModelCallRecorder(connection) if settings.trace_model else None
        gateway = LiteLLMGateway(
            model=settings.model,
            api_key=settings.api_key,
            api_base=settings.api_base,
            timeout=settings.model_timeout,
            recorder=recorder,
            model_params=settings.model_params,
        )
    catalog = TomlSeatCatalog(SEATS_DIR)
    discussion_roles = load_discussion_roles()
    personas = load_personas()
    # 工具目录：`requires` 没配上的工具**直接不可用**（不是"无密钥降级"）
    tools = TomlToolCatalog(DEFINITIONS_DIR)
    available_tool_keys = settings.available_tool_keys()
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
        uow=uow,
        clock=clock,
        events=bus,
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
    )
    retry_exploration = RetryExploration(
        explorations=explorations,
        exploration_writer=explorations,
        uow=uow,
        clock=clock,
        events=bus,
    )
    exploration_runner = ExplorationRunner(
        run_inquiry=run_inquiry,
        retry_exploration=retry_exploration,
        explorations=explorations,
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
        dig_round=dig_round,
        abandon_round=AbandonRound(
            rounds=rounds,
            round_writer=rounds,
            uow=uow,
            clock=clock,
            events=bus,
            canceller=runner,
        ),
        exploration_runner=exploration_runner,
        abandon_exploration=AbandonExploration(
            explorations=explorations,
            exploration_writer=explorations,
            uow=uow,
            clock=clock,
            events=bus,
            canceller=exploration_runner,
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
        # 复用 175 行已加载的那份角色表（同一进程只 `load_discussion_roles()` 一次）
        get_schema=GetSchema(
            discussion_roles=discussion_roles,
            personas=personas,
            # 取 `.template`：`schema_view` 按鸭子类型读 DocumentTemplate（.template_id/
            # .title/.sections[]），/schema 只投影结构、不需要 builtin 标记。
            templates=tuple(entry.template for entry in template_repo.list()),
        ),
        list_rounds=ListRounds(index=index),
        search=SearchProject(
            documents=documents, evidence=evidences, index=index, materials=material_search
        ),
        upload_material=UploadMaterial(
            documents=documents, materials=materials, parser=parser, clock=clock, uow=uow
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
        ),
        delete_material=DeleteMaterial(
            materials=materials, parser=parser, index=index, uow=uow
        ),
        reparse_material=ReparseMaterial(
            materials=materials, parser=parser, index=index, uow=uow, clock=clock
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
        stream=bus,
        model_name=settings.model,
    )
    auth_services = AuthServices(
        register_user=RegisterUser(users=users, hash_password=hash_password),
        authenticate_user=AuthenticateUser(
            users=users,
            sessions=sessions,
            clock=clock,
            hash_password=hash_password,
            verify_password=verify_password,
            throttle=LoginThrottle(
                max_fails=settings.login_max_fails,
                lock_minutes=settings.login_lock_minutes,
            ),
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
    app = create_app(services)
    app.state.auth = auth_services
    # owner 上下文由 api 层（`require_user` / SSE 归属过滤）经 `app.state` 取用：
    # api 不许 import infrastructure（分层守卫），装配点把实现注进去（依赖倒置）。
    app.state.owner_scope = owner_scope
    return Container(settings=settings, app=app)
def build_app() -> FastAPI:
    """`uvicorn pmstudio.bootstrap.container:build_app --factory` 的入口。"""
    return build_container(Settings.from_env()).app
