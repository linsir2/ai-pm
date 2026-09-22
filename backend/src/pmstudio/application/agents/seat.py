"""席位：以本领域的专业眼光出意见——只有疑问与判断两种形态。

它不决定这些意见最后写进哪个字段、也不决定要不要问用户；那是 Integrator 的活。
"""

import json

from pmstudio.application.agents import schemas
from pmstudio.application.errors import PacketParseError
from pmstudio.application.ports import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
    PromptLibrary,
    SeatDefinition,
)
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.shared.ids import EvidenceId

PURPOSE = "seat_review"


class SeatReviewer:
    PURPOSE = PURPOSE
    PROMPT = "seat_review"
    PROMPT_SLOTS = (
        "seat_title",
        "seat_focus",
        "document_state",
        "claims",
        "opinions_so_far",
        "answered",
        "schema_json",
    )

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def review(
        self,
        *,
        seat: SeatDefinition,
        document_state: str,
        claims: str,
        opinions_so_far: str,
        answered: str,
        known_eids: frozenset[EvidenceId],
    ) -> SeatReview:
        schema = schemas.SeatPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            seat_title=seat.title,
            seat_focus=seat.focus,
            document_state=document_state,
            claims=claims,
            opinions_so_far=opinions_so_far,
            answered=answered,
            schema_json=json.dumps(schema, ensure_ascii=False),
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="SeatPacket",
                response_schema=schema,
                metadata={"seat_id": seat.seat_id},
            )
        )
        packet = schemas.parse_packet(reply.text, schemas.SeatPacket, purpose=self.PURPOSE)
        return review_from_packet(packet, seat_id=seat.seat_id, known_eids=known_eids)


def review_from_packet(
    packet: schemas.SeatPacket, *, seat_id: str, known_eids: frozenset[EvidenceId]
) -> SeatReview:
    opinions: list[Opinion] = []
    seen: set[str] = set()
    for index, draft in enumerate(packet.opinions, start=1):
        local_id = draft.local_id.strip() or f"O{index}"
        if local_id in seen:
            raise PacketParseError(
                f"同一席位里 local_id 重复：{local_id}", purpose=PURPOSE, seat_id=seat_id
            )
        seen.add(local_id)
        refs = tuple(EvidenceId(ref) for ref in draft.evidence_refs)
        unknown = [str(ref) for ref in refs if ref not in known_eids]
        if unknown:
            raise PacketParseError(
                f"意见引用了不存在的编号：{'、'.join(unknown)}",
                purpose=PURPOSE,
                seat_id=seat_id,
                local_id=local_id,
            )
        opinions.append(
            Opinion(
                local_id=local_id,
                form=OpinionForm(draft.form),
                about=draft.about,
                claim=draft.claim,
                because=draft.because,
                evidence_refs=refs,
                basis_note=draft.basis_note,
            )
        )
    return SeatReview(seat_id=seat_id, basis=packet.basis, opinions=tuple(opinions))
