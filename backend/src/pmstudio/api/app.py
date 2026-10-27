"""FastAPI 应用：唯一职责是把 HTTP 变成用例调用，把错误变成状态码。"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pmstudio.api import auth, routes
from pmstudio.api.errors import (
    body_for,
    error_for_sqlstate,
    status_for,
)
from pmstudio.application.ports import DiscussionRunner, ExplorationOrchestrator, RoundOrchestrator
from pmstudio.application.use_cases import (
    AbandonExploration,
    AbandonRound,
    AssembleCards,
    CloseDiscussion,
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
    GetMemoryUsage,
    GetRound,
    GetRoundCalls,
    GetSchema,
    GetTemplate,
    GetVersionDiff,
    GetVersions,
    GetWorkspace,
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
    RecoverWebRequest,
    ReparseMaterial,
    RollbackVersion,
    SayToThread,
    SearchProject,
    SkipSeat,
    StartDiscussion,
    UpdateTemplate,
    UploadMaterial,
)
from pmstudio.domain.shared.errors import DomainError
from pmstudio.harness.errors import ApplicationError
from pmstudio.harness.events import EventStream


def _error_response(error: DomainError | ApplicationError) -> JSONResponse:
    """把领域/应用错误变成 HTTP 响应。**两个 handler 共用一份**，不复制
    `Retry-After` 那段——复制了就多一处会漂移的地方。"""

    headers = {}
    retry_after = getattr(error, "retry_after", None)
    # **凡是"我们主动拒绝、过一会儿再来"的都带 Retry-After**（429 速率闸、
    # 503 卸载 / 503 数据库忙）。只给一半的话，客户端在另一种上只能硬重试。
    #
    # 507 磁盘满**故意不在这个元组里**——它要等有人在删文件，那个头兑现不起。
    # 这行条件就是那个承诺的开关，所以别把 507 加进来。
    if status_for(error) in (429, 503) and retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_for(error), content=body_for(error), headers=headers
    )


@runtime_checkable
class BackgroundResource(Protocol):
    """有生命周期的后台资源。**只给一个方法存在的接口不值得抽**——但它有两个
    （start/stop）且"谁创建谁负责关"是这里唯一要表达的东西，所以留在类型里。

    `@runtime_checkable` 是给装配点用的：`container` 要判"这东西有没有生命周期"
    才决定交不交给 lifespan。**非 runtime 的 Protocol 直接 `isinstance` 会 TypeError**，
    少了这个装饰器那道守卫就当场炸。"""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    runner: RoundOrchestrator
    recover_web_request: RecoverWebRequest
    dig_round: DigRound
    abandon_round: AbandonRound
    skip_seat: SkipSeat
    exploration_runner: ExplorationOrchestrator
    abandon_exploration: AbandonExploration
    keep_candidate: KeepCandidate
    dismiss_candidate: DismissCandidate
    list_explorations: ListExplorations
    start_discussion: StartDiscussion
    say_to_thread: SayToThread
    assemble_cards: AssembleCards
    join_panel: JoinPanel
    discussion_runner: DiscussionRunner
    keep_card: KeepDiscussionCard
    dismiss_card: DismissDiscussionCard
    keep_sim_inquiry: KeepSimInquiry
    close_discussion: CloseDiscussion
    list_discussions: ListDiscussions
    create_project: CreateProject
    create_template: CreateTemplate
    get_template: GetTemplate
    list_templates: ListTemplates
    update_template: UpdateTemplate
    delete_template: DeleteTemplate
    workspace: GetWorkspace
    list_projects: ListProjects
    get_round: GetRound
    get_round_calls: GetRoundCalls
    get_schema: GetSchema
    list_rounds: ListRounds
    search: SearchProject
    upload_material: UploadMaterial
    list_materials: ListMaterials
    fetch_material: FetchMaterial
    delete_material: DeleteMaterial
    reparse_material: ReparseMaterial
    get_document: GetDocument
    export_markdown: ExportMarkdown
    edit_entry: EditEntry
    get_versions: GetVersions
    get_version_diff: GetVersionDiff
    rollback_version: RollbackVersion
    get_evidence: GetEvidence
    forget_memory: ForgetMemory
    memory_usage: GetMemoryUsage
    stream: EventStream
    model_name: str


def create_app(
    services: ApiServices,
    *,
    background: BackgroundResource | None = None,
    maintenance: BackgroundResource | None = None,
    readiness: Callable[[], object] | None = None,
) -> FastAPI:
    """`background` 是有生命周期的后台资源（当前只有 Redis 扇出总线）。

    `maintenance` 是**第二个**同类资源（材料维护：把块结构已过时的材料重切重算）——
    单独一个参数而不是合成一个，是因为它们的**生命周期不同**：总线要跟着进程走，
    维护任务跑完一遍就结束（`start()` 里只起一个后台任务）。
    默认 `None` → 测试那 9 个 `create_app(services)` 调用点一行都不用改。

    **全仓原本零 lifespan**，P4 加它是因为：没有它，`--reload` 每热重载一次就泄漏
    一个 reader 任务 + 一组 Redis 连接，`uvicorn --workers` 关停时也一样。

    默认 `None` 是为了**测试那 9 个 `create_app(services)` 调用点一行都不用改**。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if background is not None:
            await background.start()
        if maintenance is not None:
            await maintenance.start()
        try:
            yield
        finally:
            if maintenance is not None:
                await maintenance.stop()
            if background is not None:
                await background.stop()

    app = FastAPI(title="PM Studio", version="0.2.0", lifespan=lifespan)
    app.state.services = services
    #: `/api/ready` 的探针。`None` = 没接线，ready 明确返回 503（不是假装健康）。
    app.state.readiness = readiness
    app.include_router(routes.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")

    @app.exception_handler(DomainError)
    @app.exception_handler(ApplicationError)
    async def _handle(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, (DomainError, ApplicationError))
        return _error_response(error)

    @app.exception_handler(psycopg.Error)
    async def _handle_postgres(request: Request, error: Exception) -> JSONResponse:
        """**PostgreSQL SQLSTATE → HTTP**（只有这一处映射，不解析错误文本）。"""

        assert isinstance(error, psycopg.Error)
        mapped = error_for_sqlstate(error)
        if mapped is not None:
            return _error_response(mapped)
        return JSONResponse(
            status_code=500,
            content={
                "code": "internal_error",
                "message": "数据库操作失败",
                "details": {},
            },
        )

    return app
