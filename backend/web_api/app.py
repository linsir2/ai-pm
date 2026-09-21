"""API 层：FastAPI 应用 + 路由。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from pmstudio.bootstrap.runtime import build_runtime
from pmstudio.common.errors import ContractViolation
from pmstudio.contracts.enums import (
    CardStatus,
    ProposalState,
    RegionName,
    RoundEntry,
)
from pmstudio.contracts.models.card import CardAnswer
from pmstudio.contracts.models.scope import Scope
from web_api.dto import (
    CreateProjectRequest,
    StartRoundRequest,
    SubmitCardsRequest,
    card_group_to_dto,
    document_to_dto,
    round_to_dto,
    version_to_dto,
)
from web_api.env import load_env_file
from web_api.errors import map_exception
from web_api.sse import event_stream


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 启动时读一次 `backend/.env`：这是本地开发的密钥通道（条目里只写变量名，
    # 值只活在环境变量 / `.env` 里）。M22 是惰性读密钥的，所以放前放后都行；
    # 放这里是为了"进进程就生效"，不用等到第一次调模型。
    load_env_file()
    app.state.runtime = await build_runtime(app.state.db_path, harness=app.state.harness)
    yield
    app.state.runtime.close()


def create_app(db_path: str, *, harness: object | None = None) -> FastAPI:
    """建 FastAPI 应用。

    `harness`: 透传给 `build_runtime`。测试与本地 demo 注入 `FakeHarness`，
    避免未配置 API Key 时全链路跑不起来（P0 可运行闭环）。
    """
    app = FastAPI(title="PM Studio", lifespan=lifespan)
    app.state.db_path = db_path
    app.state.harness = harness
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    @app.post("/projects")
    async def create_project(req: CreateProjectRequest):
        runtime = app.state.runtime
        try:
            result = await runtime.project_service.create_project(
                name=req.name,
                template_id=req.template_id,
            )
            return {"project_id": result.project_id, "doc_id": result.doc_id}
        except Exception as exc:
            raise map_exception(exc) from exc

    @app.post("/rounds")
    async def start_round(req: StartRoundRequest):
        runtime = app.state.runtime
        try:
            scope = Scope(
                selected_fields=tuple(req.selected_fields) if req.selected_fields else ()
            )
            round_id = await runtime.round_driver.start_round(
                project_id=req.project_id,
                entry=RoundEntry(req.entry),
                user_input=req.user_input,
                scope=scope,
            )
            return {"round_id": round_id}
        except Exception as exc:
            raise map_exception(exc) from exc

    @app.get("/rounds/{round_id}")
    async def get_round(round_id: str):
        runtime = app.state.runtime
        try:
            round_region = await runtime.board.read(RegionName.ROUND)
            if round_region is None:
                raise ContractViolation(f"找不到轮次 {round_id}")
            card_group = await runtime.board.read(RegionName.CARD_GROUP)
            return round_to_dto(round_region, card_group).model_dump()
        except Exception as exc:
            raise map_exception(exc) from exc

    @app.post("/card-groups/{group_id}/submit")
    async def submit_cards(group_id: str, req: SubmitCardsRequest):
        runtime = app.state.runtime
        try:
            answers = [
                CardAnswer(
                    card_id=a.card_id,
                    verdict=a.verdict,
                    status=CardStatus(a.status),
                    answer=a.answer,
                    proposal_states={
                        k: ProposalState(v) for k, v in (a.proposal_states or {}).items()
                    },
                )
                for a in req.answers
            ]
            group = await runtime.round_driver.submit_cards(group_id, answers)
            return card_group_to_dto(group).model_dump()
        except Exception as exc:
            raise map_exception(exc) from exc

    @app.get("/documents/{doc_id}")
    async def get_document(doc_id: str):
        runtime = app.state.runtime
        try:
            doc = runtime.ledger.read_document(doc_id)
            if doc is None:
                raise ContractViolation(f"找不到文档 {doc_id}")
            blocks = runtime.ledger.read_blocks(doc_id)
            return document_to_dto(doc, blocks).model_dump()
        except Exception as exc:
            raise map_exception(exc) from exc

    @app.get("/documents/{doc_id}/versions")
    async def get_document_versions(doc_id: str):
        runtime = app.state.runtime
        try:
            versions = runtime.ledger.list_versions(doc_id)
            return {"versions": [version_to_dto(v).model_dump() for v in versions]}
        except Exception as exc:
            raise map_exception(exc) from exc

    @app.get("/events/stream")
    async def sse_stream(request: Request, round_id: str | None = None):
        return await event_stream(request, round_id)
