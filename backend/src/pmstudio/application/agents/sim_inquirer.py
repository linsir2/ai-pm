"""模拟调研智能体（M10）：以潜在用户视角做推演，产出**待验证的假设**与盲区。

与 M8 `Inquirer` 的关系：只复用"单调用 + 重装 + 机械筛"的机械，
**不复用它的包/容器/purpose**——真调研（external 取证）与模拟推演（simulated）
在留样里必须分得开（M10 审核 C1）。
`Slice` 没有领域对象：它是包内分组（锚点单位），无生命周期——
pydantic 管形状，装配层验挂载与账本，存 payload 时就是 dict。
"""

from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.schemas import RULES
from pmstudio.application.context import NO_MEMORY_TEXT
from pmstudio.application.evidence_refs import resolve_evidence_refs
from pmstudio.domain.exploration.item import BlindSpot, Grounding, Hypothesis
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope, make_packet_id
from pmstudio.harness.model.structured import StructuredCall, call_structured

PURPOSE = "sim_inquiry"


@dataclass(frozen=True, slots=True)
class SimInquiryOutcome:
    """一趟模拟交回的东西：切片（dict 存 payload 即此形）+ 领域对象 + 信封。

    `packet` 也带上：装配层按 draft 反查 slice 挂载（draft 才有 slice_id），
    用 `filled_local_id` 与这里同一规则对齐 local_id。
    """

    slices: list[dict]
    hypotheses: tuple[Hypothesis, ...]
    unknowns: list[dict]
    packet: schemas.SimInquiryPacket
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


class SimInquiryAgent:
    PURPOSE = PURPOSE
    PROMPT = "sim_inquiry"
    CALL = StructuredCall(
        purpose=PURPOSE,
        kind="sim_inquiry",
        prompt=PROMPT,
        packet=schemas.SimInquiryPacket,
        rules=RULES,
    )
    PROMPT_SLOTS = (
        "document_state",
        "project_memory",
        "schema_json",
        "validation_error",
    )

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def run(
        self,
        *,
        document_state: str,
        project_memory: str = NO_MEMORY_TEXT,
        round_id: str | None = None,
        known_eids: frozenset[EvidenceId] = frozenset(),
        validation_error: str = "",
        packet_slot: str = "",
    ) -> SimInquiryOutcome:
        outcome = await call_structured(
            self.CALL,
            gateway=self._model,
            prompts=self._prompts,
            slots={
                "document_state": document_state,
                "project_memory": project_memory,
                "validation_error": validation_error,
            },
            metadata={} if round_id is None else {"round_id": round_id},
            packet_id=make_packet_id(
                str(round_id or "new"), self.CALL.kind, "sim_inquirer", slot=packet_slot
            ),
            run_id=str(round_id or "new"),
            produced_by="sim_inquirer",
        )
        slices, hypotheses, unknowns = sim_from_packet(
            outcome.packet, known_eids=known_eids
        )
        return SimInquiryOutcome(
            slices=slices,
            hypotheses=hypotheses,
            unknowns=unknowns,
            packet=outcome.packet,
            envelope=outcome,
        )


def sim_from_packet(
    packet: schemas.SimInquiryPacket, *, known_eids: frozenset[EvidenceId]
) -> tuple[list[dict], tuple[Hypothesis, ...], list[dict]]:
    """包形状 → 切片 dict + 领域对象。空结果与坏形状都抛 `PacketParseError`（走重装那一环）。

    切片与盲区以 dict 形态返回：它们没有领域对象（无生命周期，存 payload 即此形），
    但每个字段都在这里验过——切片验挂载与账本，盲区验三字段非空（`why_it_matters`
    没有领域家，用 3 行显式校验代替单测里"无声通过"的坑）。
    """

    if not packet.hypotheses and not packet.unknowns:
        raise PacketParseError(
            "模拟调研既没给假设、也没给盲区（空结果不算成功）", purpose=PURPOSE
        )
    seen: set[str] = set()
    slices: list[dict] = []
    for index, draft in enumerate(packet.slices, start=1):
        local_id = filled_local_id(draft.local_id, "S", index)
        _mark_unique(seen, local_id)
        anchors = resolve_evidence_refs(
            draft.anchors,
            known=known_eids,
            what="切片引用了不存在的编号",
            local_id=local_id,
            purpose=PURPOSE,
        )
        slices.append(
            {
                "local_id": local_id,
                "situation": draft.situation,
                "job": draft.job,
                "trigger": draft.trigger,
                "workaround": draft.workaround,
                "anchors": list(anchors),
            }
        )
    slice_ids = {item["local_id"] for item in slices}
    hypotheses: list[Hypothesis] = []
    for index, draft in enumerate(packet.hypotheses, start=1):
        local_id = filled_local_id(draft.local_id, "H", index)
        _mark_unique(seen, local_id)
        if draft.slice_id not in slice_ids:
            raise PacketParseError(
                f"假设挂到了不存在的切片上：{draft.slice_id}",
                purpose=PURPOSE,
                local_id=local_id,
            )
        try:
            grounding = Grounding(draft.grounding)
        except ValueError as error:
            raise PacketParseError(
                f"未知的 grounding：{draft.grounding}",
                purpose=PURPOSE,
                local_id=local_id,
            ) from error
        refs = resolve_evidence_refs(
            draft.based_on,
            known=known_eids,
            what="假设引用了不存在的编号",
            local_id=local_id,
            purpose=PURPOSE,
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
                purpose=PURPOSE,
                local_id=local_id,
            ) from error
    unknowns: list[dict] = []
    for index, draft in enumerate(packet.unknowns, start=1):
        local_id = filled_local_id(draft.local_id, "U", index)
        _mark_unique(seen, local_id)
        for field in (draft.statement, draft.why_it_matters, draft.how_to_find_out):
            if not field.strip():
                raise PacketParseError(
                    "盲区三个字段都不许空（陈述/为什么值得问/怎么去验证）",
                    purpose=PURPOSE,
                    local_id=local_id,
                )
        try:
            BlindSpot(
                local_id=local_id,
                statement=draft.statement,
                next_step=draft.how_to_find_out,
            )
        except DomainError as error:
            raise PacketParseError(
                f"盲区过不了领域校验：{error.message}",
                purpose=PURPOSE,
                local_id=local_id,
            ) from error
        unknowns.append(
            {
                "local_id": local_id,
                "statement": draft.statement,
                "why_it_matters": draft.why_it_matters,
                "how_to_find_out": draft.how_to_find_out,
            }
        )
    return slices, tuple(hypotheses), unknowns


def filled_local_id(raw: str, prefix: str, index: int) -> str:
    """空 local_id 按顺序补（S1/H1/U1 三类各自编号）。装配层按同一规则反查，
    所以规则只许写在这里——改编号规则只改这一处。"""

    return raw.strip() or f"{prefix}{index}"


def _mark_unique(seen: set[str], local_id: str) -> None:
    if local_id in seen:
        raise PacketParseError(f"候选 id 重复：{local_id}", purpose=PURPOSE, local_id=local_id)
    seen.add(local_id)
