"""唯一组装点。infrastructure 在这里被注入到 application，别处不许 new 实现。"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI

from pmstudio.api.app import ApiServices, create_app
from pmstudio.application.agents.analyzer import ClaimAnalyzer
from pmstudio.application.agents.integrator import Integrator
from pmstudio.application.agents.seat import SeatReviewer
from pmstudio.application.ports import ModelGateway
from pmstudio.application.use_cases import (
    ConfirmDraft,
    CreateProject,
    EditEntry,
    GetDocument,
    GetRound,
    GetWorkspace,
    IntegrateRound,
    ListProjects,
    RetryRound,
    RunSeatRound,
    StartRound,
    SubmitAnswers,
    SubmitOpening,
)
from pmstudio.bootstrap.settings import Settings
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
from pmstudio.infrastructure.sqlite.project_factory import SqliteProjectFactory
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
    SqliteWorkspaceIndex,
)
from pmstudio.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PACKAGE_ROOT / "infrastructure" / "llm" / "prompts"
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
    index = SqliteWorkspaceIndex(connection)
    uow = SqliteUnitOfWork(connection)

    prompts = FilePromptLibrary(PROMPTS_DIR)
    if gateway is None:
        gateway = LiteLLMGateway(
            model=settings.model, api_key=settings.api_key, api_base=settings.api_base
        )
    catalog = TomlSeatCatalog(SEATS_DIR)
    assigner = EvidenceAssigner()
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
        clock=clock,
        ids=ids,
        events=bus,
    )
    submit_opening = SubmitOpening(
        rounds=rounds,
        round_writer=rounds,
        catalog=catalog,
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
        clock=clock,
        events=bus,
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
        clock=clock,
        ids=ids,
        events=bus,
    )
    submit_answers = SubmitAnswers(
        rounds=rounds,
        round_writer=rounds,
        evidence=evidences,
        evidence_writer=evidences,
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
    get_round = GetRound(rounds=rounds, evidence=evidences, catalog=catalog)
    runner = RoundRunner(
        start_round=start_round,
        submit_opening=submit_opening,
        run_seat_round=run_seat_round,
        integrate_round=integrate_round,
        submit_answers=submit_answers,
        confirm_draft=confirm_draft,
        retry_round=retry_round,
        get_round=get_round,
    )
    services = ApiServices(
        runner=runner,
        create_project=CreateProject(factory=factory, index=index, clock=clock, ids=ids),
        workspace=GetWorkspace(
            documents=documents, rounds=rounds, evidence=evidences, index=index, catalog=catalog
        ),
        list_projects=ListProjects(index=index),
        get_round=get_round,
        get_document=GetDocument(documents=documents),
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
        stream=bus,
        model_name=settings.model,
    )
    return Container(settings=settings, app=create_app(services))


def build_app() -> FastAPI:
    """`uvicorn pmstudio.bootstrap.container:build_app --factory` 的入口。"""

    return build_container(Settings.from_env()).app
