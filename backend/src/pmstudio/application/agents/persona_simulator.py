"""模拟评测智能体（M11）：以**使用行为**走查文档功能，产出摩擦点与三档结论。

与 M10 `SimInquirerAgent` 的关系：同一个"单调用 + 信封"机械、同一个 `simulated` 编号体系，
但**视角与产物形状都不同**——它代入"潜在用户的处境"推演需求；这里代入"使用者的行为规则"
核对文档能不能支撑把功能用起来。真调研（external 取证）与两种模拟在留样里分得开。
"""

from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.definitions import SIM_REVIEW
from pmstudio.application.agents.sim_inquirer import filled_local_id
from pmstudio.application.context import NO_MEMORY_TEXT
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope


def render_persona(persona: object) -> str:
    """把 persona 定义渲成提示词里的一段。

    鸭子类型读 `.title/.situation/.goal/.behavior/.probes`——application 不 import
    `infrastructure`（分层守卫），形状由 `PersonaDefinition` 保证，装配层注入。
    """

    return "\n".join(
        (
            f"称呼：{persona.title}",
            f"使用情境：{persona.situation}",
            f"要完成的任务：{persona.goal}",
            f"行为规则：{persona.behavior}",
            f"走查必查项：{persona.probes}",
        )
    )


def render_targets(feature_slugs: tuple[str, ...]) -> str:
    """被点名的功能：只走查这些（`feature#slug` 与文档渲染里的地址一致）。"""

    return "、".join(f"feature#{slug}" for slug in feature_slugs)


@dataclass(frozen=True, slots=True)
class SimReviewOutcome:
    """一趟走查交回的东西：persona 身份的 review dict + 信封。

    `review` 形状：`{persona_id, title, features: [...]}`，落 `Round.sim_review` 前
    再由用例层筛（引用/覆盖率/窄表）——这里不判"文档里有没有"，转换层只锁结构。
    """

    review: dict
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


class PersonaSimulatorAgent(Agent[SimReviewOutcome, schemas.SimReviewPacket]):
    definition = SIM_REVIEW

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        super().__init__(model=model, prompts=prompts)

    async def run(
        self,
        *,
        persona: object,
        feature_slugs: tuple[str, ...],
        document_state: str,
        claims: str = "",
        external_material: str = "",
        project_memory: str = NO_MEMORY_TEXT,
        round_id: str | None = None,
        validation_error: str = "",
        packet_slot: str = "",
    ) -> SimReviewOutcome:
        outcome = await self._call(
            slots={
                "document_state": document_state,
                "claims": claims,
                "external_material": external_material,
                "project_memory": project_memory,
                "persona": render_persona(persona),
                "targets": render_targets(feature_slugs),
                "validation_error": validation_error,
            },
            metadata={}
            if round_id is None
            else {"round_id": round_id, "persona_id": persona.persona_id},
            packet_id=self._make_packet_id(
                str(round_id or "new"), "persona_simulator", slot=packet_slot
            ),
            run_id=str(round_id or "new"),
            produced_by="persona_simulator",
        )
        review = review_from_packet(outcome.packet)
        review["persona_id"] = persona.persona_id
        review["title"] = persona.title
        return SimReviewOutcome(review=review, envelope=outcome)


def review_from_packet(packet: schemas.SimReviewPacket) -> dict:
    """包形状 → plain dict（补 local_id F/A 按功能内顺序；重复 id 直接炸）。

    不判引用：`entry_id` 存不存在、验收有没有覆盖全、窄表命不命中，都要文档锚点，
    住在用例层（`sim_review.review_issues`）。这里只做"没有文档也成立"的转换。
    """

    features: list[dict] = []
    for feature in packet.features:
        friction_ids: set[str] = set()
        frictions: list[dict] = []
        for index, item in enumerate(feature.frictions, start=1):
            local_id = filled_local_id(item.local_id, "F", index)
            if local_id in friction_ids:
                raise PacketParseError(
                    f"摩擦 id 重复：{local_id}", purpose=SIM_REVIEW.purpose, local_id=local_id
                )
            friction_ids.add(local_id)
            frictions.append(
                {
                    "local_id": local_id,
                    "kind": item.kind,
                    "entry_id": item.entry_id.strip(),
                    "field": item.field.strip(),
                    "quote": item.quote,
                    "not_documented": item.not_documented,
                    "blocks_flow": item.blocks_flow,
                    "note": item.note,
                }
            )
        check_ids: set[str] = set()
        checks: list[dict] = []
        for index, item in enumerate(feature.acceptance_checks, start=1):
            local_id = filled_local_id(item.local_id, "A", index)
            if local_id in check_ids:
                raise PacketParseError(
                    f"验收核对 id 重复：{local_id}", purpose=SIM_REVIEW.purpose, local_id=local_id
                )
            check_ids.add(local_id)
            checks.append(
                {
                    "local_id": local_id,
                    "entry_id": item.entry_id.strip(),
                    "status": item.status,
                    "note": item.note,
                }
            )
        features.append(
            {
                "feature_slug": feature.feature_slug.strip(),
                "started": feature.started,
                "walkthrough": [
                    {
                        "step_index": step.step_index,
                        "step_text": step.step_text,
                        "from_doc": step.from_doc.strip(),
                        "action": step.action,
                        "outcome": step.outcome,
                        "need": step.need,
                    }
                    for step in feature.walkthrough
                ],
                "frictions": frictions,
                "acceptance_checks": checks,
                "verdict": feature.verdict,
                "verdict_because": [
                    item.strip()
                    for item in feature.verdict_because
                    if item.strip()
                ],
            }
        )
    return {"features": features}
