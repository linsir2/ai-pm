"""FastAPI 应用：唯一职责是把 HTTP 变成用例调用，把错误变成状态码。"""

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pmstudio.api import auth, routes
from pmstudio.api.errors import (
    body_for,
    is_busy_error,
    is_storage_full_error,
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
    GetRound,
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
    ReparseMaterial,
    RollbackVersion,
    SayToThread,
    SearchProject,
    StartDiscussion,
    UpdateTemplate,
    UploadMaterial,
)
from pmstudio.domain.shared.errors import DomainError
from pmstudio.harness.errors import ApplicationError, DatabaseBusy, StorageFull
from pmstudio.harness.events import EventStream

#: 等锁超时告诉客户端等多久（秒）。**常量而非配置**：这是一句提示，而我们并不
#: 真知道竞争写要多久；做成旋钮等于让人瞎调。理由见 `DatabaseBusy` 的文档串。
DATABASE_BUSY_RETRY_SECONDS = 3


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


class BackgroundResource(Protocol):
    """有生命周期的后台资源。**只给一个方法存在的接口不值得抽**——但它有两个
    （start/stop）且"谁创建谁负责关"是这里唯一要表达的东西，所以留在类型里。"""

    async def start(self) -> None: ...
    async def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ApiServices:
    runner: RoundOrchestrator
    dig_round: DigRound
    abandon_round: AbandonRound
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
    stream: EventStream
    model_name: str


def create_app(
    services: ApiServices,
    *,
    background: BackgroundResource | None = None,
) -> FastAPI:
    """`background` 是有生命周期的后台资源（当前只有 Redis 扇出总线）。

    **全仓原本零 lifespan**，P4 加它是因为：没有它，`--reload` 每热重载一次就泄漏
    一个 reader 任务 + 一组 Redis 连接，`uvicorn --workers` 关停时也一样。

    默认 `None` 是为了**测试那 9 个 `create_app(services)` 调用点一行都不用改**。
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if background is not None:
            await background.start()
        try:
            yield
        finally:
            if background is not None:
                await background.stop()

    app = FastAPI(title="PM Studio", version="0.2.0", lifespan=lifespan)
    app.state.services = services
    app.include_router(routes.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")

    @app.exception_handler(DomainError)
    @app.exception_handler(ApplicationError)
    async def _handle(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, (DomainError, ApplicationError))
        return _error_response(error)

    @app.exception_handler(sqlite3.OperationalError)
    async def _handle_sqlite(request: Request, error: Exception) -> JSONResponse:
        """**SQLite 等锁超时**（高并发 P5b）。

        多 worker 把写并发提上来之后，`BEGIN IMMEDIATE` 等锁 10 秒拿不到就抛
        `OperationalError`。改前它落到 FastAPI 默认处理，成一个**裸 500**——
        而 500 传达的信息是错的（"你自己或数据坏了"），客户端只会重试到天荒地老。

        只有**等锁超时**才转 503：`no such table`、`readonly database` 那些是代码
        bug 或部署错，回 503 等于让客户端干等一个永远不会好的结果。
        """

        assert isinstance(error, sqlite3.OperationalError)
        if is_busy_error(error):
            return _error_response(
                DatabaseBusy("数据库正忙，请稍后重试", retry_after=DATABASE_BUSY_RETRY_SECONDS)
            )
        if is_storage_full_error(error):
            # 507，**故意不带 Retry-After**：那要等有人在删文件，
            # 客户端等不了那件事发生。判据见 `StorageFull` 的文档串。
            return _error_response(
                StorageFull(
                    "磁盘空间不足，这次改动没能保存——请腾出空间后重试。"
                    "已经确认写进去的内容不受影响。"
                )
            )
        # 其余（`no such table` / `readonly database`）：代码 bug 或部署错，形状照常，内码 500。
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "数据库写入失败", "details": {}},
        )

    return app
