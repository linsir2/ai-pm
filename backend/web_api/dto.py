"""API 层 DTO：前端契约的序列化形状。

只暴露前端需要的字段。内部实现细节（base_versions、dropped 等）不暴露。
"""

from pydantic import BaseModel, Field

from pmstudio.contracts.models.card import Card, CardOption, Proposal
from pmstudio.contracts.models.card_group import CardGroup
from pmstudio.contracts.models.citation import Citation
from pmstudio.contracts.models.document import Block, Document, DocumentVersion
from pmstudio.contracts.models.project import Project
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.contracts.skeleton.events import Event


class CreateProjectRequest(BaseModel):
    name: str
    template_id: str = "reg_tpl_initial"


class StartRoundRequest(BaseModel):
    project_id: str
    user_input: str
    entry: str = "main"
    selected_fields: list[str] | None = None


class CardAnswerDTO(BaseModel):
    card_id: str
    verdict: str
    status: str
    answer: str | None = None
    proposal_states: dict[str, str] | None = None


class SubmitCardsRequest(BaseModel):
    answers: list[CardAnswerDTO]


class ProjectDTO(BaseModel):
    project_id: str
    name: str
    template_id: str


class RoundDTO(BaseModel):
    round_id: str
    phase: str
    user_input: str
    entry: str
    selected_fields: list[str]
    card_group: "CardGroupDTO | None" = None
    end_reason: str | None = None


class CardGroupDTO(BaseModel):
    group_id: str
    round_id: str
    state: str
    cards: list["CardDTO"]


class CardDTO(BaseModel):
    card_id: str
    kind: str
    prompt: str
    options: list["OptionDTO"] | None = None
    proposals: list["ProposalDTO"] | None = None
    answer: str | None = None
    status: str


class OptionDTO(BaseModel):
    option_id: str
    text: str
    citations: list["CitationDTO"] = Field(default_factory=list)


class ProposalDTO(BaseModel):
    proposal_id: str
    target_label: str
    op: str
    content: str
    state: str
    citations: list["CitationDTO"] = Field(default_factory=list)


class CitationDTO(BaseModel):
    target_type: str
    target_id: str


class DocumentDTO(BaseModel):
    doc_id: str
    project_id: str
    blocks: list["BlockDTO"]


class BlockDTO(BaseModel):
    block_id: str
    schema_label: str
    content: str
    version: int
    source_card_id: str | None = None


class EventDTO(BaseModel):
    event_id: str | None = None
    type: str
    at: str
    round_id: str | None = None
    payload: dict


class VersionDTO(BaseModel):
    version_id: str
    seq: int
    trigger: str
    group_id: str | None = None
    target_version_id: str | None = None


def project_to_dto(p: Project) -> ProjectDTO:
    return ProjectDTO(project_id=p.project_id, name=p.name, template_id=p.template_id)


def round_to_dto(r: RoundRegion, card_group: CardGroup | None) -> RoundDTO:
    return RoundDTO(
        round_id=r.round_id,
        phase=r.phase.value,
        user_input=r.user_input,
        entry=r.entry.value,
        selected_fields=list(r.scope.selected_fields),
        card_group=card_group_to_dto(card_group) if card_group else None,
        end_reason=r.end_reason.value if r.end_reason else None,
    )


def card_group_to_dto(g: CardGroup) -> CardGroupDTO:
    return CardGroupDTO(
        group_id=g.group_id,
        round_id=g.round_id,
        state=g.state.value,
        cards=[card_to_dto(c) for c in g.cards],
    )


def card_to_dto(c: Card) -> CardDTO:
    return CardDTO(
        card_id=c.card_id,
        kind=c.kind.value,
        prompt=c.prompt,
        options=[option_to_dto(o) for o in c.options] if c.options else None,
        proposals=[proposal_to_dto(p) for p in c.proposals] if c.proposals else None,
        answer=c.answer,
        status=c.status.value,
    )


def option_to_dto(o: CardOption) -> OptionDTO:
    return OptionDTO(
        option_id=o.option_id,
        text=o.text,
        citations=[citation_to_dto(c) for c in o.citations],
    )


def proposal_to_dto(p: Proposal) -> ProposalDTO:
    return ProposalDTO(
        proposal_id=p.proposal_id,
        target_label=p.target_label,
        op=p.op.value,
        content=p.content,
        state=p.state.value,
        citations=[citation_to_dto(c) for c in p.citations],
    )


def citation_to_dto(c: Citation) -> CitationDTO:
    return CitationDTO(target_type=c.target_type.value, target_id=c.target_id)


def document_to_dto(doc: Document, blocks: list[Block]) -> DocumentDTO:
    return DocumentDTO(
        doc_id=doc.doc_id,
        project_id=doc.project_id,
        blocks=[block_to_dto(b) for b in blocks],
    )


def block_to_dto(b: Block) -> BlockDTO:
    return BlockDTO(
        block_id=b.block_id,
        schema_label=b.schema_label,
        content=b.content,
        version=b.version,
        source_card_id=b.source_card_id,
    )


def event_to_dto(e: Event) -> EventDTO:
    return EventDTO(
        type=e.type.value,
        at=e.at.isoformat(),
        round_id=getattr(e.payload, "round_id", None),
        payload=e.payload.model_dump(),
    )


def version_to_dto(v: DocumentVersion) -> VersionDTO:
    return VersionDTO(
        version_id=v.version_id,
        seq=v.seq,
        trigger=v.trigger.value,
        group_id=v.group_id,
        target_version_id=v.target_version_id,
    )
