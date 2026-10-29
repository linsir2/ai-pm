"""研究综合：把工具循环收回来的来源写成**只读**的结论与开放问题。

它不是席位、不写文档：产物是 `ResearchReport` 的一部分，进不了正文，
只能挂到轮次当来源卡用（`02` §11.0.93）。

**结论必须点名来源**（`refs: S1..S5`），理由：这一趟没有用户输入、没有问卷答案，
结论的唯一依据就是它自己读过的网页；不许引用别的，也不许不引用。
S→E 的映射在用例层（E 要等入库那一刻才发），所以这里交回的是**报文**，不是领域对象。
"""

from pmstudio.application.agents import schemas
from pmstudio.application.agents.definitions import RESEARCHER
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary


class ResearchSynthesizer(Agent[schemas.ResearchPacket, schemas.ResearchPacket]):
    definition = RESEARCHER

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        super().__init__(model=model, prompts=prompts)

    async def run(
        self,
        *,
        topic: str,
        sources: str,
        tool_notes: str = "",
        exploration_id: str | None = None,
        validation_error: str = "",
        packet_slot: str = "",
    ) -> schemas.ResearchPacket:
        outcome = await self._call(
            slots={
                "topic": topic,
                "sources": sources,
                "tool_notes": tool_notes,
                "validation_error": validation_error,
            },
            metadata={} if exploration_id is None else {"exploration_id": exploration_id},
            packet_id=self._make_packet_id(
                str(exploration_id or "new"), "researcher", slot=packet_slot
            ),
            run_id=str(exploration_id or "new"),
            produced_by="researcher",
        )
        return outcome.packet
