"""智能体之间唯一的通信形状。它同时做三件事：生成喂给模型的 `response_schema`、
在提示词里内联展示、解析模型回复——所以它是 JSON Schema 的唯一真相源。"""

import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from pmstudio.application.errors import PacketParseError
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.sections import FEATURE_HOME
from pmstudio.domain.shared.target import Target

Label = Literal["fact", "need", "goal", "constraint", "preference", "guess", "decision"]
Zone = Literal["body", "preference"]


class AssumptionDraft(BaseModel):
    failure_consequence: str
    verification: str


class ClaimDraft(BaseModel):
    text: str
    raw_span: str = Field(description="逐字摘自用户输入，用来跳回原文")
    label: Label
    zone: Zone = "body"
    section_id: str | None = None
    feature_slug: str | None = None
    field: str | None = None
    assumption: AssumptionDraft | None = None
    decided_by: str | None = None


class ClaimsPacket(BaseModel):
    basis: str = Field(description="你读了哪些章节与编号才切出这些主张")
    claims: list[ClaimDraft] = Field(min_length=1, max_length=12)


class OpinionDraft(BaseModel):
    local_id: str = Field(description="本席位内唯一，如 O1")
    form: Literal["question", "judgment"]
    about: str
    claim: str
    because: str
    evidence_refs: list[str] = Field(default_factory=list, description="已存在的编号，如 E4")
    basis_note: str | None = Field(default=None, description="没有可引用编号时说明为什么")


class SeatPacket(BaseModel):
    basis: str
    opinions: list[OpinionDraft] = Field(default_factory=list, max_length=8)


class TargetDraft(BaseModel):
    zone: Zone = "body"
    section_id: str | None = None
    feature_slug: str | None = None
    field: str | None = None


class OptionDraft(BaseModel):
    option_id: str
    text: str


class QuestionDraft(BaseModel):
    kind: Literal["gap", "conflict", "decision"] = "gap"
    title: str
    why: str
    consequence_if_skipped: str
    target: TargetDraft
    type: Literal["single", "multi", "text"] = "single"
    options: list[OptionDraft] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    asked_by: list[str] = Field(default_factory=list, description="提出这条问题的席位 id")


class ListItemDraft(BaseModel):
    text: str
    target: TargetDraft
    reason: str


class ProposalDraft(BaseModel):
    op: Literal["add", "replace", "remove"] = "add"
    target: TargetDraft
    text: str
    origin_type: Literal["user", "derived", "proposed", "system_default"] = "derived"
    evidence_refs: list[str] = Field(min_length=1)
    rationale: str | None = None
    kind: str | None = Field(
        default=None, description="章节级内容才给：text/acceptance/nfr/bullet_list/…"
    )
    entry_id: str | None = Field(
        default=None, description="replace/remove 必填，用既有条目清单里的 id"
    )


class IntegrationPacket(BaseModel):
    questions: list[QuestionDraft] = Field(default_factory=list, max_length=12)
    list_items: list[ListItemDraft] = Field(default_factory=list, max_length=20)
    proposals: list[ProposalDraft] = Field(default_factory=list, max_length=30)


def parse_packet[T: BaseModel](text: str, model: type[T], *, purpose: str) -> T:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise PacketParseError(
            f"模型返回的不是合法 JSON：{error.msg}", purpose=purpose, offset=error.pos
        ) from error
    try:
        return model.model_validate(payload)
    except ValidationError as error:
        raise PacketParseError(
            "模型返回的报文过不了 schema", purpose=purpose, errors=error.errors()
        ) from error


def target_of(draft: TargetDraft) -> Target:
    """线形状 → Target。形状不对时抛领域错误；调用方负责转成 PacketParseError。"""

    if draft.zone == "preference":
        if any((draft.section_id, draft.feature_slug, draft.field)):
            raise ShapeError("偏好区的 target 不带章节、功能与字段")
        return Target.preference()
    section = SectionId(draft.section_id) if draft.section_id else FEATURE_HOME
    if draft.field is not None:
        if draft.feature_slug is None:
            raise ShapeError("字段名必须和 feature_slug 一起给")
        return Target.feature_field(FeatureSlug(draft.feature_slug), draft.field)
    if draft.feature_slug is not None:
        return Target.body(section, feature_slug=FeatureSlug(draft.feature_slug))
    return Target.body(section)
