"""14 个端点：解析请求 → 调用例 → 序列化返回。业务判断一个都不在这儿。"""

from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from pmstudio.api.schemas import (
    AnswersRequest,
    ConfirmRequest,
    CreateProjectRequest,
    EditEntryRequest,
    OpeningRequest,
    StartRoundRequest,
)
from pmstudio.api.sse import as_json, event_stream
from pmstudio.application.commands import RoundCommand
from pmstudio.application.use_cases import AnswerInput, ClaimEdit
from pmstudio.domain.shared.ids import ProjectId, RoundId
from pmstudio.domain.shared.label import Zone
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp

router = APIRouter()


def services_of(request: Request):  # type: ignore[no-untyped-def]
    """从 app.state 取组装好的用例（真正的组装在 bootstrap/container.py）。"""

    return request.app.state.services


@router.get("/health")
async def health(request: Request) -> dict[str, str]:
    return {"status": "ok", "model": services_of(request).model_name}


@router.post("/projects", status_code=201)
async def create_project(request: Request, body: CreateProjectRequest):  # type: ignore[no-untyped-def]
    summary = await services_of(request).create_project(title=body.title)
    return as_json(summary)


@router.get("/projects")
async def list_projects(request: Request):  # type: ignore[no-untyped-def]
    return as_json(await services_of(request).list_projects())


@router.get("/projects/{project_id}/workspace")
async def get_workspace(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    view = await services_of(request).workspace(project_id=ProjectId(project_id))
    return as_json(view)


@router.get("/projects/{project_id}/document")
async def get_document(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    view = await services_of(request).get_document(project_id=ProjectId(project_id))
    return as_json(view)


@router.get("/projects/{project_id}/readiness")
async def get_readiness(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    """按 13 章的合格标准算"还差什么"。只读，不挡写入（01 §7.4）。"""

    items = await services_of(request).get_readiness(project_id=ProjectId(project_id))
    blockers = sum(1 for item in items if item.severity == "blocker")
    warns = sum(1 for item in items if item.severity == "warn")
    return as_json(
        {
            "items": items,
            "blockers": blockers,
            "warns": warns,
            "ready": blockers == 0,
        }
    )


@router.get("/projects/{project_id}/export/markdown")
async def export_markdown(request: Request, project_id: str) -> Response:
    """导出一份可以递出去的 Markdown。

    文件名可能是中文（项目名），所以 `Content-Disposition` 双写：
    ASCII 兜底名给老浏览器，RFC 5987 的 `filename*` 给现代浏览器。
    """

    file = await services_of(request).export_markdown(project_id=ProjectId(project_id))
    disposition = (
        f'attachment; filename="deliverable-v{file.version_no}.md"; '
        f"filename*=UTF-8''{quote(file.filename)}"
    )
    return Response(
        content=file.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.post("/projects/{project_id}/edits")
async def edit_entry(request: Request, project_id: str, body: EditEntryRequest):  # type: ignore[no-untyped-def]
    """用户手改文档。一次改动一版，改过的块之后 AI 不能再动。"""

    result = await services_of(request).edit_entry(
        project_id=ProjectId(project_id),
        op=WriteOp(body.op),
        entry_id=body.entry_id,
        block_id=body.block_id,
        text=body.text,
    )
    return as_json(result)


@router.post("/projects/{project_id}/rounds")
async def start_round(request: Request, project_id: str, body: StartRoundRequest):  # type: ignore[no-untyped-def]
    view = await services_of(request).runner.run(
        RoundCommand(kind="start", project_id=project_id, user_input=body.input)
    )
    return as_json(view)


@router.get("/rounds/{round_id}")
async def get_round(request: Request, round_id: str):  # type: ignore[no-untyped-def]
    view = await services_of(request).get_round(round_id=RoundId(round_id))
    return as_json(view)


@router.post("/rounds/{round_id}/opening")
async def submit_opening(request: Request, round_id: str, body: OpeningRequest):  # type: ignore[no-untyped-def]
    edits = tuple(
        ClaimEdit(
            claim_id=item.claim_id,
            label=item.label,
            target=Target(
                zone=Zone(item.target.zone),
                section_id=item.target.section_id,
                feature_slug=item.target.feature_slug,
                field=item.target.field,
            ),
        )
        for item in body.claim_edits
    )
    view = await services_of(request).runner.run(
        RoundCommand(
            kind="submit_opening",
            round_id=round_id,
            seats=tuple(body.seats),
            claim_edits=edits,
        )
    )
    return as_json(view)


@router.post("/rounds/{round_id}/answers")
async def submit_answers(request: Request, round_id: str, body: AnswersRequest):  # type: ignore[no-untyped-def]
    view = await services_of(request).runner.run(
        RoundCommand(
            kind="submit_answers",
            round_id=round_id,
            form_id=body.form_id,
            answers=tuple(
                AnswerInput(
                    question_id=item.question_id,
                    text=item.text,
                    selected=tuple(item.selected),
                )
                for item in body.answers
            ),
            skipped=tuple(body.skipped),
        )
    )
    return as_json(view)


@router.post("/rounds/{round_id}/confirm")
async def confirm(request: Request, round_id: str, body: ConfirmRequest):  # type: ignore[no-untyped-def]
    view = await services_of(request).runner.run(
        RoundCommand(kind="confirm", round_id=round_id, accepted=tuple(body.accepted))
    )
    return as_json({"version_no": view.confirmed_version_no, "round": view})


@router.post("/rounds/{round_id}/retry")
async def retry(request: Request, round_id: str):  # type: ignore[no-untyped-def]
    view = await services_of(request).runner.run(
        RoundCommand(kind="retry", round_id=round_id)
    )
    return as_json(view)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    stream = services_of(request).stream
    return StreamingResponse(
        event_stream(stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
