"""14 个端点：解析请求 → 调用例 → 序列化返回。业务判断一个都不在这儿。"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
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
from pmstudio.application.errors import MaterialTooLarge
from pmstudio.application.use_cases import (
    MAX_MATERIAL_BYTES,
    AnswerInput,
    ClaimEdit,
)
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


@router.get("/schema")
async def schema(request: Request):  # type: ignore[no-untyped-def]
    """现行结构：13 章（含说明与槽）、功能字段表、三处上限。

    界面不再自己抄一份字段中文名与上限常量——那两份的真相源在后端。
    """

    return as_json(await services_of(request).get_schema())


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
        target=(
            None
            if body.target is None
            else Target(
                zone=Zone(body.target.zone),
                section_id=body.target.section_id,
                feature_slug=body.target.feature_slug,
                field=body.target.field,
            )
        ),
        text=body.text,
    )
    return as_json(result)


@router.get("/projects/{project_id}/versions")
async def list_versions(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    """版本列表：底栏的版本历史抽屉读它。只读，不产生版本。"""

    return as_json(await services_of(request).get_versions(project_id=ProjectId(project_id)))


@router.get("/projects/{project_id}/versions/{version_no}/diff")
async def version_diff(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, version_no: int, to: int
):
    """两个版本的差异：`to` 是拿谁来比（不传由 FastAPI 直接 422）。"""

    view = await services_of(request).get_version_diff(
        project_id=ProjectId(project_id),
        from_version_no=version_no,
        to_version_no=to,
    )
    return as_json(view)


@router.post("/projects/{project_id}/versions/{version_no}/rollback")
async def rollback_version(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, version_no: int
):
    """回到这一版：产生**新**的一版（`trigger=rollback`），旧版本一个都不删。"""

    return as_json(
        await services_of(request).rollback_version(
            project_id=ProjectId(project_id), version_no=version_no
        )
    )


@router.get("/projects/{project_id}/evidence/{eid}")
async def evidence_lookup(request: Request, project_id: str, eid: str):  # type: ignore[no-untyped-def]
    """按编号查处方：原话、来路、链条、被谁引用（界面跨轮跳转靠它）。"""

    return as_json(
        await services_of(request).get_evidence(project_id=ProjectId(project_id), eid=eid)
    )


@router.post("/projects/{project_id}/rounds")
async def start_round(request: Request, project_id: str, body: StartRoundRequest):  # type: ignore[no-untyped-def]
    view = await services_of(request).runner.run(
        RoundCommand(
            kind="start",
            project_id=project_id,
            user_input=body.input,
            material_ids=tuple(body.material_ids),
        )
    )
    return as_json(view)


@router.get("/projects/{project_id}/rounds")
async def list_rounds(request: Request, project_id: str, limit: int = 20):  # type: ignore[no-untyped-def]
    """这个项目走过的轮次（最新在前）：闭环区顶上那串时间线读它。"""

    return as_json(
        await services_of(request).list_rounds(project_id=ProjectId(project_id), limit=limit)
    )


@router.post("/projects/{project_id}/attachments", status_code=201)
async def upload_attachment(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, file: Annotated[UploadFile, File()]
):
    """上传一份材料（multipart）。**解析在事务外**，落库在事务内；材料永远不写文档。"""

    services = services_of(request)
    # 体积在**读进内存之前**判一次（`UploadFile.size` 由 Starlette 填好）
    if file.size is not None and file.size > MAX_MATERIAL_BYTES:
        raise MaterialTooLarge(
            f"这份材料太大了（{file.size // 1024 // 1024}MB > "
            f"{MAX_MATERIAL_BYTES // 1024 // 1024}MB）",
            filename=file.filename or "",
        )
    data = await file.read(MAX_MATERIAL_BYTES + 1)
    summary = await services.upload_material(
        project_id=ProjectId(project_id),
        filename=file.filename or "未命名",
        media_type=file.content_type or "application/octet-stream",
        data=data,
    )
    return as_json(summary)


@router.get("/projects/{project_id}/attachments")
async def list_attachments(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    """这个项目有哪些材料（**只回摘要**，块正文不进列表）。"""

    return as_json(
        await services_of(request).list_materials(project_id=ProjectId(project_id))
    )


@router.delete("/projects/{project_id}/attachments/{material_id}")
async def delete_attachment(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, material_id: str
):
    """删一份材料。**只有没被引用过的才准删**（编号发出即冻结，`P19`）——
    被引用过就 409，消息里会列出是哪几个编号。返回刷新后的材料列表。"""

    return as_json(
        await services_of(request).delete_material(
            project_id=ProjectId(project_id), material_id=material_id
        )
    )


@router.post("/projects/{project_id}/attachments/{material_id}/reparse")
async def reparse_attachment(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, material_id: str
):
    """用**当前解析器**把这份材料的原始字节重读一遍（不用重传文件）。

    没被引用过 → 就地重读；被引用过 → 另出一份（旧的冻结留着）——见 `P19`。
    返回刷新后的材料列表。
    """

    return as_json(
        await services_of(request).reparse_material(
            project_id=ProjectId(project_id), material_id=material_id
        )
    )


@router.get("/projects/{project_id}/search")
async def search(request: Request, project_id: str, q: str = ""):  # type: ignore[no-untyped-def]
    """在这个项目里搜（F6）：条目 / 功能 / 编号 / 席位发言。空查询回空，不回全量。"""

    return as_json(await services_of(request).search(project_id=ProjectId(project_id), query=q))


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
            web_queries=tuple(body.web_queries),
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


@router.post("/rounds/{round_id}/dig")
async def dig(request: Request, round_id: str):  # type: ignore[no-untyped-def]
    """草案卡上的"继续深挖"：回到席位轮再问一批（P14 C6）。

    与 `/retry` 同款薄封装——真正的动作在 `DigRound` 与图的路由里。
    """

    view = await services_of(request).runner.run(RoundCommand(kind="dig", round_id=round_id))
    return as_json(view)


@router.post("/rounds/{round_id}/abandon")
async def abandon_round(request: Request, round_id: str):  # type: ignore[no-untyped-def]
    """放弃这一轮：先记状态、再取消正在跑的模型调用（`02` §7）。

    它**不走 `runner.run()`**：被那条路拦住的正是"重试"，而放弃恰恰是重试之外的另一个出口。
    """

    view = await services_of(request).abandon_round(round_id=RoundId(round_id))
    return as_json(view)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    stream = services_of(request).stream
    return StreamingResponse(
        event_stream(stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
