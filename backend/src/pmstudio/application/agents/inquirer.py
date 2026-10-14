"""调研智能体：以潜在用户视角做推演，产出**可证伪的假设**与盲区。

它不是席位：不发表专业意见、不判断该写什么，只回答"用户可能会怎么想"；
而且**这不是真实调研**——没有接触过任何真实用户，产出是"待验证的假设清单"。
证伪方式与所需真实证据由领域对象强制（`item.Hypothesis`），不靠提示词自觉。
"""

from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.definitions import INQUIRER
from pmstudio.application.context import NO_MEMORY_TEXT
from pmstudio.application.evidence_refs import resolve_evidence_refs, resolve_refs_lenient
from pmstudio.domain.exploration.item import BlindSpot, Grounding, Hypothesis
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope


@dataclass(frozen=True, slots=True)
class InquiryOutcome:
    """一趟调研交回的东西：领域对象 + 信封（溢出/摘除只有信封一处存储）。"""

    hypotheses: tuple[Hypothesis, ...]
    blind_spots: tuple[BlindSpot, ...]
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


class Inquirer(Agent[InquiryOutcome, schemas.InquiryPacket]):
    definition = INQUIRER

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        super().__init__(model=model, prompts=prompts)

    async def run(
        self,
        *,
        scope: str,
        document_state: str,
        project_memory: str = NO_MEMORY_TEXT,
        exploration_id: str | None = None,
        known_eids: frozenset[EvidenceId] = frozenset(),
        validation_error: str = "",
        packet_slot: str = "",
        lenient: bool = False,
    ) -> InquiryOutcome:
        outcome = await self._call(
            slots={
                "scope": scope,
                "document_state": document_state,
                "project_memory": project_memory,
                "validation_error": validation_error,
            },
            metadata={} if exploration_id is None else {"exploration_id": exploration_id},
            packet_id=self._make_packet_id(
                str(exploration_id or "new"), "inquirer", slot=packet_slot
            ),
            run_id=str(exploration_id or "new"),
            produced_by="inquirer",
        )
        hypotheses, blind_spots = inquiry_from_packet(
            outcome.packet, known_eids=known_eids, lenient=lenient
        )
        return InquiryOutcome(
            hypotheses=hypotheses, blind_spots=blind_spots, envelope=outcome
        )


def inquiry_from_packet(
    packet: schemas.InquiryPacket,
    *,
    known_eids: frozenset[EvidenceId],
    lenient: bool = False,
) -> tuple[tuple[Hypothesis, ...], tuple[BlindSpot, ...]]:
    """线形状 → 领域对象。空结果与坏形状都抛 `PacketParseError`（走重装那一环）。

    `lenient`（最后一次尝试）：假设 `based_on` 里不存在的编号摘掉，不判死整趟；
    摘空后 `grounding=document` 降级成 `invented`（文档接地**必须**有据，不放行无据的 document）。
    """

    if not packet.hypotheses and not packet.blind_spots:
        raise PacketParseError(
            "调研既没给假设、也没给盲区（空结果不算成功）", purpose=INQUIRER.purpose
        )
    hypotheses: list[Hypothesis] = []
    blind_spots: list[BlindSpot] = []
    seen: set[str] = set()
    for index, draft in enumerate(packet.hypotheses, start=1):
        local_id = draft.local_id.strip() or f"H{index}"
        _mark_unique(seen, local_id)
        try:
            grounding = Grounding(draft.grounding)
        except ValueError as error:
            raise PacketParseError(
                f"未知的 grounding：{draft.grounding}",
                purpose=INQUIRER.purpose,
                local_id=local_id,
            ) from error
        if lenient:
            resolved = resolve_refs_lenient(
                draft.based_on,
                known=known_eids,
                what="假设引用了不存在的编号",
                local_id=local_id,
            )
            refs = resolved.kept
            if grounding is Grounding.DOCUMENT and not refs:
                # 摘空了：文档接地必须带编号，降级成"凭空推演"（领域不放行无据的 document）
                grounding = Grounding.INVENTED
        else:
            refs = resolve_evidence_refs(
                draft.based_on,
                known=known_eids,
                what="假设引用了不存在的编号",
                local_id=local_id,
            )
        try:
            hypotheses.append(
                Hypothesis(
                    local_id=local_id,
                    statement=draft.statement,
                    grounding=grounding,
                    based_on=refs,
                    falsification=draft.falsification,
                    evidence_needed=draft.evidence_needed,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"假设过不了领域校验：{error.message}",
                purpose=INQUIRER.purpose,
                local_id=local_id,
            ) from error
    for index, spot in enumerate(packet.blind_spots, start=1):
        local_id = spot.local_id.strip() or f"B{index}"
        _mark_unique(seen, local_id)
        try:
            blind_spots.append(
                BlindSpot(
                    local_id=local_id,
                    statement=spot.statement,
                    next_step=spot.next_step,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"盲区过不了领域校验：{error.message}",
                purpose=INQUIRER.purpose,
                local_id=local_id,
            ) from error
    return tuple(hypotheses), tuple(blind_spots)


def _mark_unique(seen: set[str], local_id: str) -> None:
    if local_id in seen:
        raise PacketParseError(
            f"候选 id 重复：{local_id}", purpose=INQUIRER.purpose, local_id=local_id
        )
    seen.add(local_id)
