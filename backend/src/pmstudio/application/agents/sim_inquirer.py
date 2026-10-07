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
    `ref_drops`：引用类问题（锚点/依据落不回账本、挂到不存在的切片）的 `(handle, reason)`
    清单——**不抛**（2026-10-07 真跑：空文档时模型把 `s8` 当锚点，设计口径是
    重装一次→仍犯丢条+留痕，不是整包判死；`02` §11.0.47）。
    """

    slices: list[dict]
    hypotheses: tuple[Hypothesis, ...]
    unknowns: list[dict]
    packet: schemas.SimInquiryPacket
    envelope: Envelope
    ref_drops: tuple[tuple[str, str], ...] = ()

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
        # 接地三源（D1 §3.3）：文档 ＋ 本轮用户的话（有 E 编号）＋ 本轮材料。
        # 真跑抓过：只渲染文档时，空文档的项目里模型一个编号都看不见，
        # 把章节号 s8 当锚点填进 anchors——这三段缺一不可（02 §11.0.47）。
        "claims",
        "external_material",
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
        claims: str = "",
        external_material: str = "",
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
                "claims": claims,
                "external_material": external_material,
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
        slices, hypotheses, unknowns, ref_drops = sim_from_packet(
            outcome.packet, known_eids=known_eids
        )
        return SimInquiryOutcome(
            slices=slices,
            hypotheses=hypotheses,
            unknowns=unknowns,
            packet=outcome.packet,
            envelope=outcome,
            ref_drops=ref_drops,
        )


def sim_from_packet(
    packet: schemas.SimInquiryPacket, *, known_eids: frozenset[EvidenceId]
) -> tuple[list[dict], tuple[Hypothesis, ...], list[dict], tuple[tuple[str, str], ...]]:
    """包形状 → 切片 dict + 领域对象 + **引用问题清单**（不抛）。

    引用类问题（锚点/依据落不回账本、假设挂到不存在的切片）不判死整包：
    返回 `ref_drops`，调用方走"重装一次→仍犯丢条+留痕"（`02` §11.0.2 口径，
    2026-10-07 真跑修正——空文档的项目里模型把 `s8` 当锚点，旧实现整包 502）。
    结构性问题（空结果、id 重复、grounding 值非法、凭空推演带依据）仍整包抛。

    切片与盲区以 dict 形态返回：无生命周期，存 payload 即此形；盲区三字段非空在这里验。
    """

    if not packet.hypotheses and not packet.unknowns:
        raise PacketParseError(
            "模拟调研既没给假设、也没给盲区（空结果不算成功）", purpose=PURPOSE
        )
    seen: set[str] = set()
    ref_drops: list[tuple[str, str]] = []
    slices: list[dict] = []
    bad_slices: set[str] = set()
    for index, draft in enumerate(packet.slices, start=1):
        local_id = filled_local_id(draft.local_id, "S", index)
        _mark_unique(seen, local_id)
        anchors, unknown = _resolve_refs_lenient(
            draft.anchors, known=known_eids, local_id=local_id,
            what="切片引用了不存在的编号",
        )
        if unknown is not None:
            bad_slices.add(local_id)
            ref_drops.append((f"模拟切片 {local_id}", unknown))
            continue
        slices.append(
            {
                "local_id": local_id,
                "situation": draft.situation,
                "job": draft.job,
                "trigger": draft.trigger,
                "workaround": draft.workaround,
                "anchors": [str(eid) for eid in anchors or ()],
            }
        )
    slice_ids = {item["local_id"] for item in slices}
    hypotheses: list[Hypothesis] = []
    for index, draft in enumerate(packet.hypotheses, start=1):
        local_id = filled_local_id(draft.local_id, "H", index)
        _mark_unique(seen, local_id)
        if draft.slice_id in bad_slices:
            ref_drops.append(
                (
                    f"模拟假设 {local_id}",
                    f"它挂的切片 {draft.slice_id} 被丢了（那块引用了不存在的编号）",
                )
            )
            continue
        if draft.slice_id not in slice_ids:
            ref_drops.append(
                (f"模拟假设 {local_id}", f"挂到了不存在的切片上：{draft.slice_id}")
            )
            continue
        try:
            grounding = Grounding(draft.grounding)
        except ValueError as error:
            raise PacketParseError(
                f"未知的 grounding：{draft.grounding}",
                purpose=PURPOSE,
                local_id=local_id,
            ) from error
        refs, unknown = _resolve_refs_lenient(
            draft.based_on, known=known_eids, local_id=local_id,
            what="假设引用了不存在的编号",
        )
        if unknown is not None:
            ref_drops.append((f"模拟假设 {local_id}", unknown))
            continue
        try:
            hypotheses.append(
                Hypothesis(
                    local_id=local_id,
                    statement=draft.statement,
                    grounding=grounding,
                    based_on=refs or (),
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
    return slices, tuple(hypotheses), unknowns, tuple(ref_drops)


def _resolve_refs_lenient(
    raw_refs: tuple[str, ...] | list[str],
    *,
    known: frozenset[EvidenceId],
    local_id: str,
    what: str,
) -> tuple[tuple[EvidenceId, ...] | None, str | None]:
    """解析引用：全好返回 `(编号元组, None)`；有未知返回 `(None, 人话原因)`（不抛）。

    原因复用 `resolve_evidence_refs` 的报错文案（它已经会区分席位地址/提案编号那两种
    真踩过的坑，见 `unknown_ref_message`）。
    """

    try:
        refs = resolve_evidence_refs(
            raw_refs, known=known, what=what, local_id=local_id, purpose=PURPOSE
        )
    except PacketParseError as error:
        return None, error.message
    return refs, None


def filled_local_id(raw: str, prefix: str, index: int) -> str:
    """空 local_id 按顺序补（S1/H1/U1 三类各自编号）。装配层按同一规则反查，
    所以规则只许写在这里——改编号规则只改这一处。"""

    return raw.strip() or f"{prefix}{index}"


def _mark_unique(seen: set[str], local_id: str) -> None:
    if local_id in seen:
        raise PacketParseError(f"候选 id 重复：{local_id}", purpose=PURPOSE, local_id=local_id)
    seen.add(local_id)
