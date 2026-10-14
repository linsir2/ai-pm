"""端点层：解析请求 → 调用例 → 序列化返回。业务判断一个都不在这儿。"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from pmstudio.api.schemas import (
    AnswersRequest,
    ConfirmRequest,
    CreateProjectRequest,
    EditEntryRequest,
    FetchRequest,
    JoinPanelRequest,
    KeepRequest,
    KeepSimInquiryRequest,
    OpeningRequest,
    RunSimReviewRequest,
    SayRequest,
    StartDiscussionRequest,
    StartExplorationRequest,
    StartRoundRequest,
    TemplateCreateRequest,
)
from pmstudio.api.sse import as_json, event_stream
from pmstudio.application.commands import RoundCommand
from pmstudio.application.errors import MaterialTooLarge
from pmstudio.application.use_cases import (
    MAX_MATERIAL_BYTES,
    AnswerInput,
    ClaimEdit,
)
from pmstudio.domain.discussion.thread import QuestionSnapshot
from pmstudio.domain.exploration.exploration import ExplorationScope, ScopeKind
from pmstudio.domain.shared.ids import DiscussionId, ExplorationId, ProjectId, RoundId
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
    """现行结构：13 章（含说明与槽）、功能字段表、三处上限、**＋讨论角色**。

    界面不再自己抄一份字段中文名、上限常量与角色表——那几份的真相源在后端
    （讨论角色见 `infrastructure/discussion/roles.toml`，灵感区不抄一份）。
    """

    return as_json(await services_of(request).get_schema())


@router.post("/projects", status_code=201)
async def create_project(request: Request, body: CreateProjectRequest):  # type: ignore[no-untyped-def]
    summary = await services_of(request).create_project(
        title=body.title, template_id=body.template_id
    )
    return as_json(summary)


@router.get("/templates")
async def list_templates(request: Request):  # type: ignore[no-untyped-def]
    """模板库全文（创建页的选择器读它）。"""

    return as_json(await services_of(request).list_templates())


@router.post("/templates", status_code=201)
async def create_template(request: Request, body: TemplateCreateRequest):  # type: ignore[no-untyped-def]
    template = await services_of(request).create_template(
        title=body.title,
        sections=[item.model_dump() for item in body.sections],
    )
    return as_json(
        await services_of(request).get_template(template_id=template)
    )


@router.get("/templates/{template_id}")
async def get_template(request: Request, template_id: str):  # type: ignore[no-untyped-def]
    return as_json(await services_of(request).get_template(template_id=template_id))


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
            seed_ids=tuple(body.seed_ids),
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


@router.post("/projects/{project_id}/attachments/fetch", status_code=201)
async def fetch_attachment(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, body: FetchRequest
):
    """按网址抓一份网页材料（用户点名的那一页）：**抓取在事务外、落库在事务内**。

    同一个网址再抓一次 = 刷新（没被引用过就地换；发过编号的另出一份）——见 `P27`。
    """

    return as_json(
        await services_of(request).fetch_material(
            project_id=ProjectId(project_id), url=body.url
        )
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
        RoundCommand(
            kind="confirm",
            round_id=round_id,
            accepted=tuple(body.accepted),
            accepted_memory=tuple(body.accepted_memory),
        )
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


@router.post("/rounds/{round_id}/sim-inquiry")
async def run_sim_inquiry(request: Request, round_id: str):  # type: ignore[no-untyped-def]
    """草案卡上的"去模拟调研"：轮内动作（不碰 step），跑完界面读回最新投影。"""

    view = await services_of(request).runner.run(
        RoundCommand(kind="sim_inquiry", round_id=round_id)
    )
    return as_json(view)


@router.post("/rounds/{round_id}/sim-review")
async def run_sim_review(  # type: ignore[no-untyped-def]
    request: Request, round_id: str, body: RunSimReviewRequest
):
    """草案卡上的"模拟评测"（M11）：轮内动作（不碰 step），跑完界面读回最新投影。"""

    view = await services_of(request).runner.run(
        RoundCommand(
            kind="sim_review",
            round_id=round_id,
            persona_ids=tuple(body.persona_ids),
            feature_slugs=tuple(body.feature_slugs),
        )
    )
    return as_json(view)


@router.post("/rounds/{round_id}/sim-inquiry/keep")
async def keep_sim_inquiry(  # type: ignore[no-untyped-def]
    request: Request, round_id: str, body: KeepSimInquiryRequest
):
    """收下模拟的勾选为材料（空选择直接拒；同选择重复收下幂等）。"""

    return as_json(
        await services_of(request).keep_sim_inquiry(
            round_id=RoundId(round_id), chosen=tuple(body.chosen)
        )
    )


@router.post("/rounds/{round_id}/abandon")
async def abandon_round(request: Request, round_id: str):  # type: ignore[no-untyped-def]
    """放弃这一轮：先记状态、再取消正在跑的模型调用（`02` §7）。

    它**不走 `runner.run()`**：被那条路拦住的正是"重试"，而放弃恰恰是重试之外的另一个出口。
    """

    view = await services_of(request).abandon_round(round_id=RoundId(round_id))
    return as_json(view)


@router.post("/projects/{project_id}/explorations")
async def start_exploration(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, body: StartExplorationRequest
):
    """发起一趟调研（M8）：请求内跑完，失败也返回 200 + status=failed（可重试）。

    接地检查（空文档）与忙碌（同项目已有在跑）都是人话错误：422 / 409。
    """

    scope = ExplorationScope(
        kind=ScopeKind(body.scope.kind), ref=body.scope.ref, text=body.scope.text
    )
    view = await services_of(request).exploration_runner.start(
        project_id=ProjectId(project_id), scope=scope
    )
    return as_json(view)


@router.get("/projects/{project_id}/explorations")
async def list_explorations(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    """这个项目走过的探索（最新在前）：过目台与历史都读它（≤20 条）。"""

    return as_json(
        await services_of(request).list_explorations(project_id=ProjectId(project_id))
    )


@router.post("/explorations/{exploration_id}/retry")
async def retry_exploration(request: Request, exploration_id: str):  # type: ignore[no-untyped-def]
    """重跑一趟失败的调研：只补这趟没跑完的那次调用（不重跑已经跑完的）。"""

    return as_json(
        await services_of(request).exploration_runner.retry(exploration_id=exploration_id)
    )


@router.post("/explorations/{exploration_id}/abandon")
async def abandon_exploration(request: Request, exploration_id: str):  # type: ignore[no-untyped-def]
    """放弃这一趟：**先写状态、再取消**（与放弃一轮同一条顺序与理由）。"""

    return as_json(
        await services_of(request).abandon_exploration(
            exploration_id=ExplorationId(exploration_id)
        )
    )


@router.post("/explorations/{exploration_id}/candidates/{candidate_id}/keep")
async def keep_candidate(  # type: ignore[no-untyped-def]
    request: Request, exploration_id: str, candidate_id: str, body: KeepRequest
):
    """收下一条候选：`seed` = 预填下一轮输入；`material` = 落一份材料（幂等）。"""

    return as_json(
        await services_of(request).keep_candidate(
            exploration_id=ExplorationId(exploration_id),
            candidate_id=candidate_id,
            as_=body.as_,
        )
    )


@router.post("/explorations/{exploration_id}/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(request: Request, exploration_id: str, candidate_id: str):  # type: ignore[no-untyped-def]
    """删掉一条候选：终态——不过目台、不回流。"""

    return as_json(
        await services_of(request).dismiss_candidate(
            exploration_id=ExplorationId(exploration_id), candidate_id=candidate_id
        )
    )


@router.post("/projects/{project_id}/discussions")
async def start_discussion(  # type: ignore[no-untyped-def]
    request: Request, project_id: str, body: StartDiscussionRequest
):
    """开一条讨论线程（M9/M18）：同（项目，归一化主题，角色集合）的 open 线程幂等返。"""

    return as_json(
        await services_of(request).start_discussion(
            project_id=ProjectId(project_id),
            topic=body.topic,
            role_ids=list(body.role_ids),
            question=(
                None
                if body.question is None
                else QuestionSnapshot(title=body.question.title, why=body.question.why)
            ),
            finding_exploration_id=body.finding_exploration_id,
        )
    )


@router.get("/projects/{project_id}/discussions")
async def list_discussions(request: Request, project_id: str):  # type: ignore[no-untyped-def]
    """这个项目的讨论线程（最新在前，≤20 条）。"""

    return as_json(
        await services_of(request).list_discussions(project_id=ProjectId(project_id))
    )


@router.post("/discussions/{discussion_id}/say")
async def say_to_thread(  # type: ignore[no-untyped-def]
    request: Request, discussion_id: str, body: SayRequest
):
    """追问一轮：模型跑完、校验通过才原子记一对（user＋assistant）。"""

    return as_json(
        await services_of(request).say_to_thread(
            discussion_id=DiscussionId(discussion_id), text=body.text
        )
    )


@router.post("/discussions/{discussion_id}/cards")
async def assemble_cards(request: Request, discussion_id: str):  # type: ignore[no-untyped-def]
    """把讨论总结成 1-3 张灵感卡（无 body：总结对象就是整段讨论）。"""

    return as_json(
        await services_of(request).assemble_cards(
            discussion_id=DiscussionId(discussion_id)
        )
    )


@router.post("/discussions/{discussion_id}/cards/{card_id}/keep")
async def keep_discussion_card(  # type: ignore[no-untyped-def]
    request: Request, discussion_id: str, card_id: str, body: KeepRequest
):
    """收下一张卡：`seed` = 预填下一轮输入；`material` = 落一份材料（幂等）。"""

    return as_json(
        await services_of(request).keep_card(
            discussion_id=DiscussionId(discussion_id), card_id=card_id, as_=body.as_
        )
    )


@router.post("/discussions/{discussion_id}/cards/{card_id}/dismiss")
async def dismiss_discussion_card(  # type: ignore[no-untyped-def]
    request: Request, discussion_id: str, card_id: str
):
    """忽略一张卡：终态——不进灵感箱、不回流（同卡重复忽略幂等）。"""

    return as_json(
        await services_of(request).dismiss_card(
            discussion_id=DiscussionId(discussion_id), card_id=card_id
        )
    )


@router.post("/discussions/{discussion_id}/close")
async def close_discussion(request: Request, discussion_id: str):  # type: ignore[no-untyped-def]
    """关掉一条线程：终态幂等（无 body）。"""

    return as_json(
        await services_of(request).close_discussion(
            discussion_id=DiscussionId(discussion_id)
        )
    )


@router.post("/discussions/{discussion_id}/run-round")
async def run_panel_round(request: Request, discussion_id: str):  # type: ignore[no-untyped-def]
    """跑一轮 panel 自主讨论（M18）：本轮在场者并行发言 → 综合 → 落库。

    一次只跑一轮；N 轮由前端循环驱动。失败保留已落库的 turn、无综合，重试补剩余发言者。
    同一线程同时只允许一轮在跑（`discussion_busy` 409）。
    """

    return as_json(
        await services_of(request).discussion_runner.run_round(
            discussion_id=DiscussionId(discussion_id)
        )
    )


@router.post("/discussions/{discussion_id}/join")
async def join_panel(  # type: ignore[no-untyped-def]
    request: Request, discussion_id: str, body: JoinPanelRequest
):
    """中途拉人进 panel（M18）：下一轮生效，不追补、不重跑。"""

    return as_json(
        await services_of(request).join_panel(
            discussion_id=DiscussionId(discussion_id), role_id=body.role_id
        )
    )


@router.post("/discussions/{discussion_id}/abort-run")
async def abort_panel_round(request: Request, discussion_id: str):  # type: ignore[no-untyped-def]
    """停掉正在跑的那一轮（M18）：只让在途调用停下来，不写状态。

    已落库的 turn 照留，线程还是 open；没在跑时幂等成功。
    """

    aborted = await services_of(request).discussion_runner.abort(
        discussion_id=DiscussionId(discussion_id)
    )
    return as_json({"aborted": aborted})


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    stream = services_of(request).stream
    return StreamingResponse(
        event_stream(stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.delete("/projects/{project_id}/memory/{memory_id}", status_code=204)
async def forget_memory(request: Request, project_id: str, memory_id: str) -> Response:
    """忘掉一条记忆（M6）。**唯一一条不走确认卡**的记忆写入：用户要能立刻收回一条。

    删不掉（这条不在记忆里）→ 404（`UnknownMemoryEntry` 由错误处理器翻译）。
    """

    await services_of(request).forget_memory(
        project_id=ProjectId(project_id), memory_id=memory_id
    )
    return Response(status_code=204)
