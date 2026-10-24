"""对外请求体。响应体从 DTO 投影直接序列化（`api.sse.as_json`）。"""

from typing import Literal

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    #: 模板 id（`02` §11.0.68）：缺省 standard，老客户端只传 title 照样能建。
    template_id: str = "standard"


class TemplateSlotRequest(BaseModel):
    name: str
    title: str
    kind: str


class TemplateSectionRequest(BaseModel):
    id: str
    title: str
    purpose: str
    role: str = "plain"
    slots: list[TemplateSlotRequest] = Field(default_factory=list)
    #: 判据声明：整数（"至少 N 条"）或 `{count, kind}`（只数某种块，如验收标准）
    min_entries: int | dict[str, object] | None = None
    #: 上下文覆盖度下限（提示词用"这章写满没有"）；缺省按 role 推导
    coverage: int | None = None


class TemplateCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    sections: list[TemplateSectionRequest] = Field(min_length=1)


class TargetRequest(BaseModel):
    zone: Literal["body", "preference"] = "body"
    section_id: str | None = None
    feature_slug: str | None = None
    field: str | None = None


class ClaimEditRequest(BaseModel):
    claim_id: str
    label: str
    target: TargetRequest


class StartRoundRequest(BaseModel):
    input: str = Field(min_length=1)
    #: 这一轮带进来的材料 id（工具层 C-2）；不传 = 不带材料
    material_ids: list[str] = Field(default_factory=list)
    #: 这一轮带进来的过目产物种子（M12 统一格式：`来源:id#条目id`，如
    #: `exploration:x-1#H1` / `discussion:d-1#C1`；不传 = 不带种子）——
    #: 缺了它前端发的种子会被静默丢掉（M8.1 的教训），所以这里必须显式声明。
    seed_ids: list[str] = Field(default_factory=list)


class ExplorationScopeRequest(BaseModel):
    kind: Literal["entry", "document"]
    ref: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=500)


class StartExplorationRequest(BaseModel):
    kind: Literal["inquiry"]
    scope: ExplorationScopeRequest


class KeepRequest(BaseModel):
    """收下一条过目产物（调研候选 / 讨论卡同形）。JSON 键是 `as`——Python 关键字只能用别名接。"""

    as_: Literal["seed", "material"] = Field(alias="as")


class SkipSeatRequest(BaseModel):
    """用户手动跳过一个还没交回的席位。只带 seat_id——轮次在路径里。"""

    seat_id: str


class KeepSimInquiryRequest(BaseModel):
    """收下模拟的勾选：假设/盲区 id 列表（切片是分组，不能单收，由用例层拦）。"""

    chosen: list[str] = Field(default_factory=list)


class RunSimReviewRequest(BaseModel):
    """跑一趟模拟评测：选中的模拟使用者与要查的功能。

    上限（1–3 人 / 1–2 个功能）在**用例层**校验（`ShapeError` → 422）——
    常量只有 `application/sim_review.py` 一份，api 层不抄数字（抄一份就是两处真相源）。
    """

    persona_ids: list[str] = Field(default_factory=list)
    feature_slugs: list[str] = Field(default_factory=list)


class QuestionRefRequest(BaseModel):
    """开聊时带的问卷题（只当开场白：题面快照进 topic，角色由前端定）。"""

    title: str = Field(min_length=1, max_length=500)
    why: str | None = Field(default=None, max_length=500)


class StartDiscussionRequest(BaseModel):
    """开一条讨论线程：主题＋角色（1–4 个 panel，M18）必填，问卷题可选，开题底稿可选（M13）。"""

    topic: str = Field(min_length=1, max_length=200)
    role_ids: list[str] = Field(min_length=1, max_length=4)
    question: QuestionRefRequest | None = None
    #: 「先跑一轮调研」：那一趟探索的全部候选快照成开题底稿（只引用；不传 = 无底稿）
    finding_exploration_id: str | None = None


class JoinPanelRequest(BaseModel):
    """中途拉人进 panel：下一轮生效（M18）。"""

    role_id: str = Field(min_length=1)


class SayRequest(BaseModel):
    """追问一轮：空文本不算一轮（422），上限 1000 字。"""

    text: str = Field(min_length=1, max_length=1000)


class FetchRequest(BaseModel):
    """按网址抓一份网页材料（`P27`）：只收网址本身（护栏在 `HttpxWebFetcher` 里）。"""

    url: str = Field(min_length=1, max_length=2000)


class OpeningRequest(BaseModel):
    seats: list[str] = Field(min_length=1)
    claim_edits: list[ClaimEditRequest] = Field(default_factory=list)
    #: 复述卡上勾中的联网检索词（工具层 C-2b）。空 = 不联网——**默认就是空**。
    web_queries: list[str] = Field(default_factory=list)


class AttachMaterialRequest(BaseModel):
    """搜索框「带进本轮」：材料 id + 要带的块（一次最多 3 块，用例层再挡）。"""

    material_id: str = Field(min_length=1, max_length=64)
    #: 与 `application.use_cases.MAX_MANUAL_CHUNKS_PER_CALL=3` 对齐；解析层先挡，
    #: 别等超大 JSON 进了用例才数。
    chunk_ids: list[str] = Field(min_length=1, max_length=3)


class AnswerRequest(BaseModel):
    question_id: str
    text: str = ""
    selected: list[str] = Field(default_factory=list)


class AnswersRequest(BaseModel):
    form_id: str
    answers: list[AnswerRequest] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)


class ConfirmRequest(BaseModel):
    accepted: list[str] = Field(default_factory=list)
    #: 草案卡上勾中的记忆提议 id（`mem#1`…）。不传 = 这一轮不写记忆（老客户端行为不变）
    accepted_memory: list[str] = Field(default_factory=list)


class EditEntryRequest(BaseModel):
    """手改文档：改一条 / 加一条 / 删一条（`op` 决定要带哪些字段）。"""

    op: Literal["add", "replace", "remove"]
    entry_id: str | None = None
    block_id: str | None = None
    #: 加一条时也能给"落到哪"的完整形状（`{feature_slug, field}` / `{section_id, field}`）——
    #: 字段块还不存在也能加（domain 自己建块，`04-未决.md` F8-①）。
    target: TargetRequest | None = None
    text: str = ""
