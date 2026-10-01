"""Integrator：把席位的专业意见组装成"能进文档的内容候选"与"能问人的题目"。

它不产生新的专业判断——只决定一件事：这条意见是问，还是写；以及写成什么形状。
"""

import json
from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.context import (
    FEATURE_FIELDS_TEXT,
    NO_MATERIAL_TEXT,
    SECTION_SLOTS_TEXT,
)
from pmstudio.application.errors import PacketParseError
from pmstudio.application.ports import ModelGateway, ModelMessage, ModelRequest, PromptLibrary
from pmstudio.domain.shared.ids import RoundId

PURPOSE = "integration"


@dataclass(frozen=True, slots=True)
class IntegrationOutcome:
    """整合者这一趟交回的东西：报文 + 报文里**没进卡片**的那些条。

    截断不是错误（`schemas.parse_packet_trimmed` 的语义）：用例层决定要不要先重装一次，
    溢出最终变成留痕与草案卡上的缺口行（"不静默丢"）。

    `discarded` 是同一类东西的另一半（P23）：容器里**根本不是对象**的元素被摘掉了。
    席位与主张早就能摘（P22 F17），整合这一包原先不能——因为**摘了没地方记账**；
    现在用例层有出口了（`Round.dropped` + `Draft.gaps`），所以一起开。
    """

    packet: schemas.IntegrationPacket
    overflow: tuple[schemas.Overflow, ...] = ()
    discarded: tuple[schemas.Discarded, ...] = ()


@dataclass(frozen=True, slots=True)
class DispositionOutcome:
    """小调用交回的东西：只有"没采用"的申报（+ 被摘掉的那几条）。"""

    packet: schemas.DispositionsPacket
    discarded: tuple[schemas.Discarded, ...] = ()


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
        "- `remove`（撤下某条）的 text **可以留空**——撤哪一条看 entry_id；"
        "`add` / `replace` 的 text 必须有内容（空的那一条会被丢掉并留痕，别浪费）",
        "- 题目要写 from_opinions（席位条目地址，如 seat.business_rules:O1）：这道题来自哪几条"
        "席位意见。它只用来标「谁问的」，不当出处编号",
        "- **采纳了某条席位意见，就要引它**（否则系统认为这条意见没人交代，会挂进留痕）："
        "把某条「判断」写进提案或题目时，evidence_refs 里必须同时写上**那条判断自己的出处编号**"
        "（与它依据的 E 编号并列，例如 [\"E4\", \"E9\"]）；把某条「疑问」变成题时，"
        "from_opinions 里必须写上它的席位条目地址（seat.xxx:O2）",
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
        "external_material",
        "limits",
        "feature_fields",
        "section_slots",
        "schema_json",
        "validation_error",
        "authorized_features",
        "open_items",
        "previous_list_items",
        "contradictions",
    )

    #: 归因补交代用的**小调用**（`02` §11.0.38）：同一个席位身份、另一种提示词，
    #: 只回答"我点名的那些里，哪些是你没采用的"。它**不重写**题目与提案——
    #: 真跑证明"重写整包"会补一批又漏一批（P23：点名 3/4/3 条补了 2/4/3，总量没降）。
    DISPOSITIONS_PURPOSE = "opinion_dispositions"
    DISPOSITIONS_PROMPT = "opinion_dispositions"
    DISPOSITIONS_SLOTS = ("opinions", "produced", "schema_json")

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
        external_material: str = NO_MATERIAL_TEXT,
        validation_error: str = "",
        round_id: RoundId | None = None,
        authorized_features: str = "",
        open_items: str = "",
        previous_list_items: str = "",
        contradictions: str = "",
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
            external_material=external_material,
            limits=LIMITS_TEXT,
            feature_fields=FEATURE_FIELDS_TEXT,
            section_slots=SECTION_SLOTS_TEXT,
            schema_json=json.dumps(schema, ensure_ascii=False),
            validation_error=validation_error,
            authorized_features=authorized_features,
            open_items=open_items,
            previous_list_items=previous_list_items,
            contradictions=contradictions,
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
        packet, overflow, discarded = schemas.parse_packet_lenient(
            reply.text, schemas.IntegrationPacket, purpose=self.PURPOSE
        )
        if discarded and not (
            packet.questions
            or packet.list_items
            or packet.proposals
            or packet.dropped_opinions
        ):
            # 整包都是占位符 = 什么都没给。**必须判成错**（走重装 → 降级那条出口），
            # 否则它会一路走到 `draft_of(...) is None` → ShapeError → HTTP 409，
            # 用户看到的是一句莫名其妙的"整合既没有给出题目也没有给出提案"。
            raise PacketParseError(
                "整包都是占位符：这一趟等于什么都没给（空返回不等于没意见）",
                purpose=self.PURPOSE,
                discarded=(discarded[0].container, discarded[0].index),
            )
        return IntegrationOutcome(packet=packet, overflow=overflow, discarded=discarded)

    async def dispositions(
        self,
        *,
        opinions: str,
        produced: str,
        round_id: RoundId | None = None,
    ) -> DispositionOutcome:
        """归因补交代：只问"我点名的那些里，哪些是你没采用的"。

        与 `integrate()` 共用同一个席位身份与模型网关，但**提示词更小**（只给点名的意见 +
        核算读过的引用），而且**不重写**题目与提案——真跑（P23）证明重写整包会"补一批、
        又漏一批"，总量降不下来。
        """

        schema = schemas.DispositionsPacket.model_json_schema()
        prompt = self._prompts.render(
            self.DISPOSITIONS_PROMPT,
            opinions=opinions,
            produced=produced,
            schema_json=json.dumps(schema, ensure_ascii=False),
        )
        reply = await self._model.complete(
            ModelRequest(
                purpose=self.DISPOSITIONS_PURPOSE,
                messages=(ModelMessage(role="user", content=prompt),),
                schema_name="DispositionsPacket",
                response_schema=schema,
                metadata={} if round_id is None else {"round_id": str(round_id)},
            )
        )
        packet, _, discarded = schemas.parse_packet_lenient(
            reply.text, schemas.DispositionsPacket, purpose=self.DISPOSITIONS_PURPOSE
        )
        return DispositionOutcome(packet=packet, discarded=discarded)
