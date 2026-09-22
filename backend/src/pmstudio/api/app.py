"""FastAPI 应用：唯一职责是把 HTTP 变成用例调用，把错误变成状态码。"""

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pmstudio.api import routes
from pmstudio.api.errors import body_for, status_for
from pmstudio.application.errors import ApplicationError
from pmstudio.application.ports import EventStream, RoundOrchestrator
from pmstudio.application.use_cases import (
    CreateProject,
    GetDocument,
    GetRound,
    GetWorkspace,
    ListProjects,
)
from pmstudio.domain.shared.errors import DomainError


@dataclass(frozen=True, slots=True)
class ApiServices:
    runner: RoundOrchestrator
    create_project: CreateProject
    workspace: GetWorkspace
    list_projects: ListProjects
    get_round: GetRound
    get_document: GetDocument
    stream: EventStream
    model_name: str


def create_app(services: ApiServices) -> FastAPI:
    app = FastAPI(title="PM Studio", version="0.2.0")
    app.state.services = services
    app.include_router(routes.router, prefix="/api")

    @app.exception_handler(DomainError)
    @app.exception_handler(ApplicationError)
    async def _handle(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, (DomainError, ApplicationError))
        return JSONResponse(status_code=status_for(error), content=body_for(error))

    return app
