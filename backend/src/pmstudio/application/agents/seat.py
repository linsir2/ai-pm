"""席位：以本领域的专业眼光出意见——只有疑问与判断两种形态。

它不决定这些意见最后写进哪个字段、也不决定要不要问用户；那是 Integrator 的活。
"""

from collections.abc import Mapping
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.definitions import SEAT_REVIEWER
from pmstudio.application.context import NO_MATERIAL_TEXT, NO_MEMORY_TEXT
from pmstudio.application.evidence_refs import resolve_evidence_refs, resolve_refs_lenient
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope


@dataclass(frozen=True, slots=True)
class SalvagedRef:
    """降级时从某条意见上**摘掉**的引用（留痕要报：正文还在、依据摘了）。"""

    local_id: str
    dropped: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SeatOutcome:
    """一个席位这一趟交回的东西：领域对象 + 报文里**没进卡片**的那些条。

    截断不是错误（`schemas.parse_packet_trimmed` 的语义）：整轮照跑，溢出由用例层写成留痕。
    `discarded` 同理（2026-10-01 审计 F17）：元素根本不是对象的那些条被摘掉，
    位置报回用例层写成留痕——一个占位符不该把整轮打掉。

    `salvaged`（2026-10-14）：最后一次尝试里"引用了不存在的编号"被摘掉的那些条——
    与截断/摘除同一纪律：**一个幻觉编号不该把整轮打掉**，但必须留痕。
    """

    review: SeatReview
    #: §4.4 的信封。**这一趟的全部事实都在它身上**：报文、依据、没进卡片的那些条、原文。
    envelope: Envelope
    salvaged: tuple[SalvagedRef, ...] = ()

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        """转发到信封——**只有信封一处存储**。

        以前这里是独立字段、信封是另一份载体，两份会漂移。现在这里是读信封的窗口，
        调用点读起来还是 `outcome.overflow`，但存的地方只有一个。
        """

        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


class SeatReviewer(Agent[SeatOutcome, schemas.SeatPacket]):
    definition = SEAT_REVIEWER

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        super().__init__(model=model, prompts=prompts)

    async def review(
        self,
        *,
        seat: SeatDefinition,
        document_state: str,
        claims: str,
        opinions_so_far: str,
        answered: str,
        project_memory: str = NO_MEMORY_TEXT,
        project_context: str = "",
        observations_index: str = "",
        external_material: str = NO_MATERIAL_TEXT,
        known_eids: frozenset[EvidenceId],
        answer_eids: Mapping[str, str] | None = None,
        round_id: RoundId | None = None,
        validation_error: str = "",
        packet_slot: str = "",
        allowed_eids: str = "",
        lenient: bool = False,
    ) -> SeatOutcome:
        outcome = await self._call(
            slots={
                "seat_title": seat.title,
                "seat_focus": seat.focus,
                "document_state": document_state,
                "claims": claims,
                "opinions_so_far": opinions_so_far,
                "answered": answered,
                "project_memory": project_memory,
                "project_context": project_context,
                "observations_index": observations_index,
                "external_material": external_material,
                "allowed_eids": allowed_eids,
                "validation_error": validation_error,
            },
            metadata={
                "seat_id": seat.seat_id,
                **({} if round_id is None else {"round_id": str(round_id)}),
            },
            packet_id=self._make_packet_id(
                str(round_id or "new"), seat.seat_id, slot=packet_slot
            ),
            run_id=str(round_id or "new"),
            produced_by=seat.seat_id,
        )
        review, salvaged = review_from_packet(
            outcome.packet,
            seat_id=seat.seat_id,
            known_eids=known_eids,
            answer_eids=answer_eids or {},
            lenient=lenient,
        )
        return SeatOutcome(review=review, envelope=outcome, salvaged=salvaged)


def review_from_packet(
    packet: schemas.SeatPacket,
    *,
    seat_id: str,
    known_eids: frozenset[EvidenceId],
    answer_eids: Mapping[str, str] | None = None,
    lenient: bool = False,
) -> tuple[SeatReview, tuple[SalvagedRef, ...]]:
    """把席位报文变成领域对象。**滑一跤不判死整轮，没理由的"无意见"才判死**（L2）。

    真模型冒烟实测出来的三条规则：
    - 既没意见也没说读了什么 ⇒ `PacketParseError`（这正是 L2 要防的静默缺口）；
    - 有意见但没写 basis ⇒ 标注"模型未说明读了什么"后继续（内容还在，界面看得见）；
    - 某条意见既没依据编号也没写 why ⇒ 按"它提议的"处理（最低权威，默认不勾），
      并在 basis_note 里写明这是模型没给依据——**不伪装成有依据的判断**（AG1 的实质）。

    `lenient`（2026-10-14）：**最后一次尝试**才为真。引用了不存在的编号时不再判死整轮，
    而是把那些编号从该条意见上摘掉、写进 basis_note，并把摘了哪些报回用例层记留痕
    （`02` §11.0.2 口径，与模拟调研 `ref_drops` 同法）。上一层仍会先给一次重装机会。
    """

    if not packet.opinions and not packet.basis.strip():
        raise PacketParseError(
            "席位既没给意见、也没说读了什么（空意见必须给理由）",
            purpose=SEAT_REVIEWER.purpose,
            seat_id=seat_id,
        )
    basis = packet.basis.strip() or "（模型未说明读了什么，意见照收，请在草案卡上核对）"
    opinions: list[Opinion] = []
    salvaged: list[SalvagedRef] = []
    seen: set[str] = set()
    for index, draft in enumerate(packet.opinions, start=1):
        local_id = draft.local_id.strip() or f"O{index}"
        if local_id in seen:
            raise PacketParseError(
                f"同一席位里 local_id 重复：{local_id}",
                purpose=SEAT_REVIEWER.purpose, seat_id=seat_id,
            )
        seen.add(local_id)
        # 机械翻译：模型有时把问卷题号（q3）当出处编号写；答案的出处编号是 E 编号。
        # 翻译过去才是它想表达的意思（不翻译就是整轮失败——真模型冒烟实测过一次）。
        # 再归一化写法（E08 → E8）：只认能落回账本的那种写法，落不回照旧（重装/摘掉）。
        mapping = answer_eids or {}
        raw_refs = [mapping.get(ref, ref) for ref in draft.evidence_refs]
        dropped: tuple[str, ...] = ()
        if lenient:
            resolved = resolve_refs_lenient(
                raw_refs, known=known_eids, what="意见引用了不存在的编号", local_id=local_id
            )
            refs = resolved.kept
            dropped = resolved.dropped
        else:
            refs = resolve_evidence_refs(
                raw_refs,
                known=known_eids,
                what="意见引用了不存在的编号",
                local_id=local_id,
                purpose=SEAT_REVIEWER.purpose,
            )
        note = (draft.basis_note or "").strip() or None
        if dropped:
            # 摘掉编号**必须说清**（不静默）：正文照收，这条落到「它提议的」（无依据）。
            salvage_note = (
                f"（模型引用了不存在的编号 {'、'.join(dropped)}，已摘掉——这条按无依据处理）"
            )
            note = f"{note} {salvage_note}".strip()
            salvaged.append(SalvagedRef(local_id=local_id, dropped=dropped))
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
                purpose=SEAT_REVIEWER.purpose,
                seat_id=seat_id,
                local_id=local_id,
            ) from error
    try:
        review = SeatReview(seat_id=seat_id, basis=basis, opinions=tuple(opinions))
    except DomainError as error:
        raise PacketParseError(
            f"席位交回的内容过不了领域校验：{error.message}",
            purpose=SEAT_REVIEWER.purpose,
            seat_id=seat_id,
        ) from error
    return review, tuple(salvaged)
