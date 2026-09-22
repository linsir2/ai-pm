"""Integrator：把席位的专业意见组装成"能进文档的内容候选"与"能问人的题目"。

它不产生新的专业判断——只决定一件事：这条意见是问，还是写；以及写成什么形状。
"""

import json

from pmstudio.application.agents import schemas
from pmstudio.application.ports import ModelGateway, ModelMessage, ModelRequest, PromptLibrary

PURPOSE = "integration"

LIMITS_TEXT = "\n".join(
    [
        "- 一批最多 5 道题；多出来的放进 list_items（本轮不挡交付）",
        "- 每条提案必须带 evidence_refs，只能引用清单里已有的编号（如 E9）",
        "- 不许新建功能子树：席位想建新功能时，出一道具，kind=decision，"
        "选项 id 用 new_feature_yes / new_feature_no",
        "- target 必须落在 13 章内；功能字段只能落在 s5 的既有功能下（feature_slug + field）",
        "- 与文档已有内容或历史答案重复的不要再提",
        "- 与文档既有结论冲突时出一道具，kind=conflict，why 里写清两侧立场",
        "- 每条内容候选一句一件事；不确定的别写进提案，去出题",
    ]
)


class Integrator:
    PURPOSE = PURPOSE
    PROMPT = "integrator"
    PROMPT_SLOTS = (
        "document_state",
        "claims",
        "opinions",
        "answered",
        "skipped",
        "entry_ids",
        "limits",
        "schema_json",
        "validation_error",
    )

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        self._model = model
        self._prompts = prompts

    async def integrate(
        self,
        *,
        document_state: str,
        claims: str,
        opinions: str,
        answered: str,
        skipped: str,
        entry_ids: str,
        validation_error: str = "",
    ) -> schemas.IntegrationPacket:
        schema = schemas.IntegrationPacket.model_json_schema()
        prompt = self._prompts.render(
            self.PROMPT,
            document_state=document_state,
            claims=claims,
            opinions=opinions,
            answered=answered,
            skipped=skipped,
            entry_ids=entry_ids,
            limits=LIMITS_TEXT,
            schema_json=json.dumps(schema, ensure_ascii=False),
            validation_error=validation_error,
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="IntegrationPacket",
                response_schema=schema,
            )
        )
        return schemas.parse_packet(reply.text, schemas.IntegrationPacket, purpose=self.PURPOSE)
