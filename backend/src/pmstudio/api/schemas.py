"""对外请求体。响应体从 DTO 投影直接序列化（`api.sse.as_json`）。"""

from typing import Literal

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


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
    #: 这一轮带进来的调研种子（M8.1：`探索id#候选id`；不传 = 不带种子）——
    #: 缺了它前端发的种子会被静默丢掉（M8 的教训），所以这里必须显式声明。
    seed_ids: list[str] = Field(default_factory=list)


class ExplorationScopeRequest(BaseModel):
    kind: Literal["entry", "document"]
    ref: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=500)


class StartExplorationRequest(BaseModel):
    kind: Literal["inquiry"]
    scope: ExplorationScopeRequest


class KeepCandidateRequest(BaseModel):
    """收下一条候选。JSON 键是 `as`——Python 关键字只能用别名接。"""

    as_: Literal["seed", "material"] = Field(alias="as")


class KeepSimInquiryRequest(BaseModel):
    """收下模拟的勾选：假设/盲区 id 列表（切片是分组，不能单收，由用例层拦）。"""

    chosen: list[str] = Field(default_factory=list)


class QuestionRefRequest(BaseModel):
    """开聊时带的问卷题（只当开场白：题面快照进 topic，角色由前端定）。"""

    title: str = Field(min_length=1, max_length=500)
    why: str | None = Field(default=None, max_length=500)


class StartDiscussionRequest(BaseModel):
    """开一条讨论线程：主题＋角色必填，问卷题可选（只当开场白）。"""

    topic: str = Field(min_length=1, max_length=200)
    role_id: str = Field(min_length=1)
    question: QuestionRefRequest | None = None


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
