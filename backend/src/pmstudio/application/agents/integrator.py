"""Integrator：把席位的专业意见组装成"能进文档的内容候选"与"能问人的题目"。

它不产生新的专业判断——只决定一件事：这条意见是问，还是写；以及写成什么形状。
"""

import json

from pmstudio.application.agents import schemas
from pmstudio.application.context import FEATURE_FIELDS_TEXT
from pmstudio.application.ports import ModelGateway, ModelMessage, ModelRequest, PromptLibrary

PURPOSE = "integration"

LIMITS_TEXT = "\n".join(
    [
        "- 一批最多 5 道题；多出来的放进 list_items（本轮不挡交付）",
        "- evidence_refs 里**只许出现 E 开头的出处编号**（E1、E9…），只能是清单里已有的；"
        "席位条目（seat.xxx:O2）是地址不是编号，写进来算校验不过",
        "- 每条提案必须带 evidence_refs（至少一条 E 编号）；题目的 evidence_refs 可以留空"
        "——席位的「疑问」自己没有出处编号，要引用就引用它「依据」里那些 E 编号",
        "- 题目要写 from_opinions（席位条目地址，如 seat.business_rules:O1）：这道题来自哪几条"
        "席位意见。它只用来标「谁问的」，不当出处编号",
        "- 不许新建功能子树：席位想建新功能时，出一道具，kind=decision，"
        "选项 id 用 new_feature_yes / new_feature_no",
        "- target 必须落在 13 章内。两种写法别混：",
        "  ① 章节级：{zone: body, section_id: s8}（配 kind，如 nfr）",
        "  ② 功能字段级：{zone: body, section_id: s5, feature_slug: order-export, "
        "field: acceptance}——**给了 field 就必须给 feature_slug，且 section_id 必须是 s5**",
        "- field 只能从「功能字段表」里选；不在表里的名字一律不许用",
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
        "feature_fields",
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
            feature_fields=FEATURE_FIELDS_TEXT,
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
