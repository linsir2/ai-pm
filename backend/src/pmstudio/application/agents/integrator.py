"""Integrator：把席位的专业意见组装成"能进文档的内容候选"与"能问人的题目"。

它不产生新的专业判断——只决定一件事：这条意见是问，还是写；以及写成什么形状。
"""

import json
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.context import FEATURE_FIELDS_TEXT, SECTION_SLOTS_TEXT
from pmstudio.application.ports import ModelGateway, ModelMessage, ModelRequest, PromptLibrary
from pmstudio.domain.shared.ids import RoundId

PURPOSE = "integration"


@dataclass(frozen=True, slots=True)
class IntegrationOutcome:
    """整合者这一趟交回的东西：报文 + **容器被截断了几条**。

    截断不是错误（`schemas.parse_packet_trimmed` 的语义）：用例层决定要不要先重装一次，
    溢出最终变成留痕与草案卡上的缺口行（"不静默丢"）。
    """

    packet: schemas.IntegrationPacket
    overflow: tuple[schemas.Overflow, ...] = ()


LIMITS_TEXT = "\n".join(
    [
        "- 一批最多 5 道题；多出来的放进 list_items（本轮不挡交付）",
        "- 内容候选（proposals）一轮最多 60 条；同一件事只说一次，"
        "优先保留 s5 功能字段与验收标准",
        "- dropped_opinions 一轮最多 84 条（7 席 × 12 条）："
        "没采用的席位意见一条都不许默默丢掉",
        "- **本轮主张点名过的功能已授权**：内容必须落到 "
        "{zone: body, section_id: s5, feature_slug: <那个功能>, field: <功能字段>}；"
        "拿不准也别降级成章节级（章节级只用来写目标/范围/受众这类整章内容）",
        "- 一条功能内容都没有时，先把主张点到的每个功能写成 name / one_liner / goal / "
        "audience / exceptions / acceptance 里的至少一条，别让 s5 空着",
        "- 上下文里列出的**未决项**本轮必须处理：能定的写内容，并出一条 "
        "{op: remove, entry_id: <那条未决项的 id>} 把它撤下；定不了的出成题"
        "（别原样再留一遍）",
        "- evidence_refs 里**只许出现 E 开头的出处编号**（E1、E9…），只能是清单里已有的；"
        "席位条目（seat.xxx:O2）是地址不是编号，写进来算校验不过",
        "- 每条提案必须带 evidence_refs（至少一条 E 编号）；题目的 evidence_refs 可以留空"
        "——席位的「疑问」自己没有出处编号，要引用就引用它「依据」里那些 E 编号",
        "- 题目要写 from_opinions（席位条目地址，如 seat.business_rules:O1）：这道题来自哪几条"
        "席位意见。它只用来标「谁问的」，不当出处编号",
        "- 不许新建功能子树：席位想建新功能时，出一道具，kind=decision，"
        "选项 id 用 new_feature_yes / new_feature_no",
        "- target 必须落在 13 章内。三种写法别混：",
        "  ① 章节级：{zone: body, section_id: s2}（配 kind，如 bullet_list）——"
        "**章节级内容绝对不要给 field**：像 {section_id: s1, field: one_liner} 或 "
        "{section_id: s2, field: goal} 都是错的（one_liner / goal 是「功能字段」，"
        "只能在 s5 上配 feature_slug 用；s1/s2 这些章没有字段）",
        "  ② 章节槽：{zone: body, section_id: s8, field: compatibility}——只有 s8 有六个槽"
        "（performance / availability / security / compatibility / observability / cost），"
        "**这种写法不要给 feature_slug**；非功能需求优先落到对应的槽里",
        "  ③ 功能字段级：{zone: body, section_id: s5, feature_slug: order-export, "
        "field: acceptance}——**给了 feature_slug 就必须是 s5，field 只能取功能字段表里的名字**",
        "- field 的合法值分两个命名空间：有 feature_slug 时取功能字段表，没有时取该章的槽表；"
        "两张表都在下面",
        "- 去处写错**只丢那一条**（记进留痕，别人不知道它本来想说什么）；"
        "所以拿不准去处时，宁可落到**章节级**（只给 section_id + kind），也别硬塞一个 field",
        "- 与文档已有内容或历史答案重复的不要再提",
        "- **同一件事不要问两遍**（同一个 target 配同一个问法算重复题，整轮不算通过）；"
        "同一个去处上的**两件不同的事**各出一题：去处相同没关系，但题面要写清问的是哪一件"
        "（两件事各有自己的选项与跳过后果，别硬合成一道）",
        "- **两侧立场互斥就出一道具，kind=conflict，why 里写清是哪两侧**，三种都算："
        "① 与**文档里的既有结论**冲突（文档说只能主管审批，本轮说要放开）；"
        "② **本轮输入内部**自相矛盾（要导出 100 万行 + 必须 3 秒内完成）；"
        "③ 两个席位的判断互斥（一个说同步够用，一个说必须异步）。"
        "说得出两侧就说得出选项：一个选项保住这一侧、另一个保住另一侧。"
        "**只是「还没定」不算矛盾**（那是 gap，如「量级是多少」）——别把缺信息的题也标成 conflict，"
        "界面上矛盾题与缺信息题是分开显示的",
    "- 每条内容候选一句一件事；不确定的别写进提案，去出题",
    "- 你没采用的席位意见**必须**写进 dropped_opinions（handle + 一句话原因）；",
    "  一条都不许默默丢掉——事后再问「这条为什么没进文档」时，只有这段留痕答得上来",
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
        "section_slots",
        "schema_json",
        "validation_error",
        "authorized_features",
        "open_items",
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
        round_id: RoundId | None = None,
        authorized_features: str = "",
        open_items: str = "",
    ) -> IntegrationOutcome:
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
            section_slots=SECTION_SLOTS_TEXT,
            schema_json=json.dumps(schema, ensure_ascii=False),
            validation_error=validation_error,
            authorized_features=authorized_features,
            open_items=open_items,
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="IntegrationPacket",
                response_schema=schema,
                metadata={} if round_id is None else {"round_id": str(round_id)},
            )
        )
        packet, overflow = schemas.parse_packet_trimmed(
            reply.text, schemas.IntegrationPacket, purpose=self.PURPOSE
        )
        return IntegrationOutcome(packet=packet, overflow=overflow)
