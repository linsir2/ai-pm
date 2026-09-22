"""Analyzer：把一段用户输入切分成带标签与去处的主张。"""

import json

from pmstudio.application.agents import schemas
from pmstudio.application.context import LABELS_TEXT, SECTIONS_TEXT
from pmstudio.application.errors import PacketParseError
from pmstudio.application.ports import ModelGateway, ModelMessage, ModelRequest, PromptLibrary
from pmstudio.domain.round.claim import Assumption, Claim
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.label import ClaimLabel

PURPOSE = "claims"


def normalize(text: str) -> str:
    """空白归一化：模型抄原话时常用不同的换行与空格。"""

    return " ".join(text.split())


class ClaimAnalyzer:
    PURPOSE = PURPOSE
    PROMPT = "analyzer"
    PROMPT_SLOTS = ("document_state", "user_input", "labels", "sections", "schema_json")

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def analyze(self, *, user_input: str, document_state: str) -> tuple[Claim, ...]:
        schema = schemas.ClaimsPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            document_state=document_state,
            user_input=user_input,
            labels=LABELS_TEXT,
            sections=SECTIONS_TEXT,
            schema_json=json.dumps(schema, ensure_ascii=False),
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="ClaimsPacket",
                response_schema=schema,
            )
        )
        packet = schemas.parse_packet(reply.text, schemas.ClaimsPacket, purpose=self.PURPOSE)
        return claims_from_packet(packet, user_input=user_input)


def claims_from_packet(packet: schemas.ClaimsPacket, *, user_input: str) -> tuple[Claim, ...]:
    haystack = normalize(user_input)
    claims: list[Claim] = []
    for index, draft in enumerate(packet.claims, start=1):
        claim_id = f"c{index}"
        if normalize(draft.raw_span) not in haystack:
            raise PacketParseError(
                "主张里的原话片段在用户输入里找不到（复述必须能跳回原文）",
                purpose=PURPOSE,
                claim_id=claim_id,
                raw_span=draft.raw_span,
            )
        try:
            claims.append(
                Claim(
                    claim_id=claim_id,
                    text=draft.text.strip(),
                    label=ClaimLabel(draft.label),
                    target=schemas.target_of(draft),
                    raw_span=draft.raw_span,
                    assumption=(
                        Assumption(
                            failure_consequence=draft.assumption.failure_consequence,
                            verification=draft.assumption.verification,
                        )
                        if draft.assumption is not None
                        else None
                    ),
                    decided_by=draft.decided_by,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"主张 {claim_id} 过不了领域校验：{error.message}",
                purpose=PURPOSE,
                claim_id=claim_id,
                domain_code=error.code,
            ) from error
    return tuple(claims)
