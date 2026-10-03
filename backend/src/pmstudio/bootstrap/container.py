"""唯一组装点。infrastructure 在这里被注入到 application，别处不许 new 实现。"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from pmstudio.api.app import ApiServices, create_app
from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.ports import ModelGateway, WebFetcher
from pmstudio.application.use_cases import (
    AbandonRound,
    ConfirmDraft,
    CreateProject,
    DeleteMaterial,
    DigRound,
    EditEntry,
    ExportMarkdown,
    FetchMaterial,
    GetDocument,
    GetEvidence,
    GetReadiness,
    GetRound,
    GetSchema,
    GetVersionDiff,
    GetVersions,
    GetWorkspace,
    IntegrateRound,
    ListMaterials,
    ListProjects,
    ListRounds,
    ReparseMaterial,
    RetryRound,
    RollbackVersion,
    RunSeatRound,
    SearchProject,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
    UploadMaterial,
    web_search_tool,
)
from pmstudio.bootstrap.settings import Settings
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.services.assigner import EvidenceAssigner
from pmstudio.domain.services.committer import ProposalCommitter
from pmstudio.domain.services.scope_guard import ScopeGuard
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.infrastructure.events.in_process import InProcessEventBus
from pmstudio.infrastructure.graph.graph import RoundRunner
from pmstudio.infrastructure.llm.litellm_gateway import LiteLLMGateway
from pmstudio.infrastructure.llm.prompt_library import FilePromptLibrary
from pmstudio.infrastructure.seats.catalog import TomlSeatCatalog
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.materials import SqliteLikeRetriever, SqliteMaterialStore
from pmstudio.infrastructure.sqlite.model_calls import SqliteModelCallRecorder
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteMemoryRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from pmstudio.infrastructure.tools.catalog import TomlToolCatalog
from pmstudio.infrastructure.tools.extract import LocalMaterialParser
from pmstudio.infrastructure.tools.fetch import HttpxWebFetcher
from pmstudio.infrastructure.tools.tavily import TavilySearchGateway

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PACKAGE_ROOT / "infrastructure" / "llm" / "prompts"
SEATS_DIR = PACKAGE_ROOT / "infrastructure" / "seats" / "definitions"
TOOLS_DIR = PACKAGE_ROOT / "infrastructure" / "tools" / "definitions"


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

    documents = SqliteDocumentRepository(connection)
    rounds = SqliteRoundRepository(connection)
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
    # 工具目录：`requires` 没配上的工具**直接不可用**（不是"无密钥降级"）
    tools = TomlToolCatalog(TOOLS_DIR)
    available_tool_keys = settings.available_tool_keys()
    web_search = _web_search_gateway(settings, tools=tools, keys=available_tool_keys)
    assigner = EvidenceAssigner()
    readiness = ReadinessChecker()
    analyzer = ClaimAnalyzer(model=gateway, prompts=prompts, catalog=catalog)
    reviewer = SeatReviewer(model=gateway, prompts=prompts)
    integrator = Integrator(model=gateway, prompts=prompts)
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
    )
    confirm_draft = ConfirmDraft(committer=committer, rounds=rounds, clock=clock, events=bus)
    retry_round = RetryRound(
        rounds=rounds, round_writer=rounds, uow=uow, clock=clock, events=bus
    )
    dig_round = DigRound(rounds=rounds, round_writer=rounds, uow=uow, clock=clock, events=bus)
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
        create_project=CreateProject(factory=factory, index=index, clock=clock, ids=ids),
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
        get_schema=GetSchema(),
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
        get_readiness=GetReadiness(documents=documents, checker=readiness),
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
        stream=bus,
        model_name=settings.model,
    )
    return Container(settings=settings, app=create_app(services))


def build_app() -> FastAPI:
    """`uvicorn pmstudio.bootstrap.container:build_app --factory` 的入口。"""

    return build_container(Settings.from_env()).app
