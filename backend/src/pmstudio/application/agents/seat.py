"""席位：以本领域的专业眼光出意见——只有疑问与判断两种形态。

它不决定这些意见最后写进哪个字段、也不决定要不要问用户；那是 Integrator 的活。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.context import NO_MATERIAL_TEXT, NO_MEMORY_TEXT
from pmstudio.application.errors import PacketParseError, unknown_ref_message
from pmstudio.application.evidence_refs import candidates_for, normalize_ref
from pmstudio.application.ports import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
    PromptLibrary,
    SeatDefinition,
)
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import EvidenceId, RoundId

PURPOSE = "seat_review"


@dataclass(frozen=True, slots=True)
class SeatOutcome:
    """一个席位这一趟交回的东西：领域对象 + 报文里**没进卡片**的那些条。

    截断不是错误（`schemas.parse_packet_trimmed` 的语义）：整轮照跑，溢出由用例层写成留痕。
    `discarded` 同理（2026-10-01 审计 F17）：元素根本不是对象的那些条被摘掉，
    位置报回用例层写成留痕——一个占位符不该把整轮打掉。
    """

    review: SeatReview
    overflow: tuple[schemas.Overflow, ...] = ()
    discarded: tuple[schemas.Discarded, ...] = ()


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
        # 跨轮记忆（M6）：这一份是这个项目**确认过**的口径 / 教训 / 否掉的东西
        "project_memory",
        "external_material",
        "schema_json",
        "validation_error",
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
        project_memory: str = NO_MEMORY_TEXT,
        external_material: str = NO_MATERIAL_TEXT,
        known_eids: frozenset[EvidenceId],
        answer_eids: Mapping[str, str] | None = None,
        round_id: RoundId | None = None,
        validation_error: str = "",
    ) -> SeatOutcome:
        schema = schemas.SeatPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            seat_title=seat.title,
            seat_focus=seat.focus,
            document_state=document_state,
            claims=claims,
            opinions_so_far=opinions_so_far,
            answered=answered,
            project_memory=project_memory,
            external_material=external_material,
            schema_json=json.dumps(schema, ensure_ascii=False),
            validation_error=validation_error,
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="SeatPacket",
                response_schema=schema,
                metadata={
                    "seat_id": seat.seat_id,
                    **({} if round_id is None else {"round_id": str(round_id)}),
                },
            )
        )
        packet, overflow, discarded = schemas.parse_packet_lenient(
            reply.text, schemas.SeatPacket, purpose=self.PURPOSE
        )
        return SeatOutcome(
            review=review_from_packet(
                packet,
                seat_id=seat.seat_id,
                known_eids=known_eids,
                answer_eids=answer_eids or {},
            ),
            overflow=overflow,
            discarded=discarded,
        )


def review_from_packet(
    packet: schemas.SeatPacket,
    *,
    seat_id: str,
    known_eids: frozenset[EvidenceId],
    answer_eids: Mapping[str, str] | None = None,
) -> SeatReview:
    """把席位报文变成领域对象。**滑一跤不判死整轮，没理由的"无意见"才判死**（L2）。

    真模型冒烟实测出来的三条规则：
    - 既没意见也没说读了什么 ⇒ `PacketParseError`（这正是 L2 要防的静默缺口）；
    - 有意见但没写 basis ⇒ 标注"模型未说明读了什么"后继续（内容还在，界面看得见）；
    - 某条意见既没依据编号也没写 why ⇒ 按"它提议的"处理（最低权威，默认不勾），
      并在 basis_note 里写明这是模型没给依据——**不伪装成有依据的判断**（AG1 的实质）。
    """

    if not packet.opinions and not packet.basis.strip():
        raise PacketParseError(
            "席位既没给意见、也没说读了什么（空意见必须给理由）",
            purpose=PURPOSE,
            seat_id=seat_id,
        )
    basis = packet.basis.strip() or "（模型未说明读了什么，意见照收，请在草案卡上核对）"
    opinions: list[Opinion] = []
    seen: set[str] = set()
    for index, draft in enumerate(packet.opinions, start=1):
        local_id = draft.local_id.strip() or f"O{index}"
        if local_id in seen:
            raise PacketParseError(
                f"同一席位里 local_id 重复：{local_id}", purpose=PURPOSE, seat_id=seat_id
            )
        seen.add(local_id)
        # 机械翻译：模型有时把问卷题号（q3）当出处编号写；答案的出处编号是 E 编号。
        # 翻译过去才是它想表达的意思（不翻译就是整轮失败——真模型冒烟实测过一次）。
        # 再归一化写法（E08 → E8）：只认能落回账本的那种写法，落不回照样判死（GB1）。
        mapping = answer_eids or {}
        refs = tuple(
            EvidenceId(normalize_ref(mapping.get(ref, ref), known=known_eids))
            for ref in draft.evidence_refs
        )
        unknown = [str(ref) for ref in refs if ref not in known_eids]
        if unknown:
            raise PacketParseError(
                unknown_ref_message(unknown, what="意见引用了不存在的编号"),
                purpose=PURPOSE,
                seat_id=seat_id,
                local_id=local_id,
                candidates=[
                    candidates_for(item, known=known_eids) for item in unknown
                ],
            )
        note = (draft.basis_note or "").strip() or None
        if not refs and note is None:
            note = "（模型没给依据：按纯专业判断处理，草案里会归到「它提议的」）"
        try:
            opinions.append(
                Opinion(
                    local_id=local_id,
                    form=OpinionForm(draft.form),
                    about=draft.about,
                    claim=draft.claim,
                    because=draft.because,
                    evidence_refs=refs,
                    basis_note=note,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"席位意见过不了领域校验：{error.message}",
                purpose=PURPOSE,
                seat_id=seat_id,
                local_id=local_id,
            ) from error
    try:
        return SeatReview(seat_id=seat_id, basis=basis, opinions=tuple(opinions))
    except DomainError as error:
        raise PacketParseError(
            f"席位交回的内容过不了领域校验：{error.message}",
            purpose=PURPOSE,
            seat_id=seat_id,
        ) from error
