"""讨论智能体：以固定角色身份接话，或把一段讨论总结成灵感卡。

它不是席位：不发表"该写什么"的专业判断，只做两件事——
以某个固定角色（反方 / 用户之声 / 类比者）的身份接话（say），
以及把上面的讨论总结成 1-3 张灵感卡（assemble）。

say 单调用请求内跑完（不读 memory：讨论是灵感阶段，接地只靠文档态，T2 定）。
"""

from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.schemas import RULES
from pmstudio.application.evidence_refs import resolve_evidence_refs
from pmstudio.domain.discussion.card import Card, Grounding
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope, make_packet_id
from pmstudio.harness.model.structured import StructuredCall, call_structured

PURPOSE_SAY = "discussion"
PURPOSE_CARDS = "discussion_cards"


@dataclass(frozen=True, slots=True)
class SayOutcome:
    """一趟接话交回的东西：回答正文 + 规范引用 + 信封（溢出/摘除只有信封一处存储）。"""

    text: str
    refs: tuple[EvidenceId, ...]
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


@dataclass(frozen=True, slots=True)
class CardsOutcome:
    """一趟组装交回的东西：领域卡 + 信封。

    `card_id` 是包内序号（C1..），跨包全局编号由用例层 stamp。
    """

    cards: tuple[Card, ...]
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


class Discusser:
    SAY_PROMPT = "discussion_say"
    CARDS_PROMPT = "discussion_cards"
    SAY = StructuredCall(
        purpose=PURPOSE_SAY,
        kind="discussion",
        prompt=SAY_PROMPT,
        packet=schemas.DiscussionReply,
        rules=RULES,
    )
    CARDS = StructuredCall(
        purpose=PURPOSE_CARDS,
        kind="discussion_cards",
        prompt=CARDS_PROMPT,
        packet=schemas.CardsPacket,
        rules=RULES,
    )
    PROMPT_SLOTS = (
        "role_title",
        "role_focus",
        "topic",
        "question",
        "thread_history",
        "document_state",
        "schema_json",
        "validation_error",
    )

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def say(
        self,
        *,
        role_title: str,
        role_focus: str,
        topic: str,
        question: str,
        history: str,
        document_state: str,
        discussion_id: str,
        turn_slot: str,
        known_eids: frozenset[EvidenceId] = frozenset(),
        validation_error: str = "",
    ) -> SayOutcome:
        outcome = await call_structured(
            self.SAY,
            gateway=self._model,
            prompts=self._prompts,
            slots={
                "role_title": role_title,
                "role_focus": role_focus,
                "topic": topic,
                "question": question,
                "thread_history": history,
                "document_state": document_state,
                "validation_error": validation_error,
            },
            metadata={"discussion_id": discussion_id},
            packet_id=make_packet_id(
                discussion_id, self.SAY.kind, "discusser", slot=turn_slot
            ),
            run_id=discussion_id,
            produced_by="discusser",
        )
        text, refs = say_from_packet(
            outcome.packet, known_eids=known_eids, turn_slot=turn_slot
        )
        return SayOutcome(text=text, refs=refs, envelope=outcome)

    async def assemble(
        self,
        *,
        role_title: str,
        role_focus: str,
        topic: str,
        thread_text: str,
        document_state: str,
        discussion_id: str,
        assemble_slot: str,
        known_eids: frozenset[EvidenceId] = frozenset(),
        validation_error: str = "",
    ) -> CardsOutcome:
        outcome = await call_structured(
            self.CARDS,
            gateway=self._model,
            prompts=self._prompts,
            slots={
                "role_title": role_title,
                "role_focus": role_focus,
                "topic": topic,
                "thread_history": thread_text,
                "document_state": document_state,
                "validation_error": validation_error,
            },
            metadata={"discussion_id": discussion_id},
            packet_id=make_packet_id(
                discussion_id, self.CARDS.kind, "discusser", slot=assemble_slot
            ),
            run_id=discussion_id,
            produced_by="discusser",
        )
        if outcome.overflow:
            # 超 3 不算成功：lenient 截断会把第 4 张静默吃掉，那正是铁律不许的。
            first = outcome.overflow[0]
            raise PacketParseError(
                f"一次最多总结 3 张卡，这次给了 {first.kept + first.dropped} 条："
                "请合并或删到 3 条以内",
                purpose=PURPOSE_CARDS,
                assemble_slot=assemble_slot,
            )
        cards = cards_from_packet(outcome.packet, known_eids=known_eids)
        return CardsOutcome(cards=cards, envelope=outcome)


def say_from_packet(
    packet: schemas.DiscussionReply,
    *,
    known_eids: frozenset[EvidenceId],
    turn_slot: str,
) -> tuple[str, tuple[EvidenceId, ...]]:
    """线形状 →（正文，规范引用）。空正文不算成功（走重装那一环）。"""

    text = packet.text.strip()
    if not text:
        raise PacketParseError(
            "这一轮的回答是空的（空串不算成功）",
            purpose=PURPOSE_SAY,
            turn_slot=turn_slot,
        )
    refs = resolve_evidence_refs(
        packet.based_on,
        known=known_eids,
        what="回答引用了不存在的编号",
        local_id=turn_slot,
        purpose=PURPOSE_SAY,
    )
    return text, refs


def cards_from_packet(
    packet: schemas.CardsPacket, *, known_eids: frozenset[EvidenceId]
) -> tuple[Card, ...]:
    """线形状 → 领域对象。空包与超 3 都抛 `PacketParseError`（走重装那一环）。"""

    if not packet.cards:
        raise PacketParseError(
            "组装一张卡都没给（空 list 不算成功）", purpose=PURPOSE_CARDS
        )
    if len(packet.cards) > 3:
        raise PacketParseError(
            f"一次最多总结 3 张卡，这次给了 {len(packet.cards)} 条："
            "请合并或删到 3 条以内",
            purpose=PURPOSE_CARDS,
        )
    cards: list[Card] = []
    for index, draft in enumerate(packet.cards, start=1):
        local_id = f"C{index}"
        try:
            grounding = Grounding(draft.grounding)
        except ValueError as error:
            raise PacketParseError(
                f"未知的 grounding：{draft.grounding}",
                purpose=PURPOSE_CARDS,
                local_id=local_id,
            ) from error
        refs = resolve_evidence_refs(
            draft.based_on,
            known=known_eids,
            what="灵感卡引用了不存在的编号",
            local_id=local_id,
            purpose=PURPOSE_CARDS,
        )
        try:
            cards.append(
                Card(
                    card_id=local_id,
                    statement=draft.statement,
                    grounding=grounding,
                    based_on=refs,
                )
            )
        except DomainError as error:
            raise PacketParseError(
                f"灵感卡过不了领域校验：{error.message}",
                purpose=PURPOSE_CARDS,
                local_id=local_id,
            ) from error
    return tuple(cards)
