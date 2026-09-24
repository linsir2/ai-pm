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


class OpeningRequest(BaseModel):
    seats: list[str] = Field(min_length=1)
    claim_edits: list[ClaimEditRequest] = Field(default_factory=list)


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


class EditEntryRequest(BaseModel):
    """手改文档：改一条 / 加一条 / 删一条（`op` 决定要带哪些字段）。"""

    op: Literal["add", "replace", "remove"]
    entry_id: str | None = None
    block_id: str | None = None
    text: str = ""
