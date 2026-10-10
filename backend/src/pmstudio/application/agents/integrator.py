"""Integrator：把席位的专业意见组装成"能进文档的内容候选"与"能问人的题目"。

它不产生新的专业判断——只决定一件事：这条意见是问，还是写；以及写成什么形状。
"""

from dataclasses import dataclass

from pmstudio.application.agents import schemas
from pmstudio.application.agents.definitions import INTEGRATOR
from pmstudio.application.context import (
    FEATURE_FIELDS_TEXT,
    NO_MATERIAL_TEXT,
    NO_MEMORY_TEXT,
    NO_PENDING_QUESTION_TEXT,
    SECTION_SLOTS_TEXT,
)
from pmstudio.domain.shared.ids import RoundId
from pmstudio.harness.agents.agent import Agent
from pmstudio.harness.errors import PacketParseError
from pmstudio.harness.model.contracts import ModelGateway, PromptLibrary
from pmstudio.harness.model.envelope import Envelope, make_packet_id
from pmstudio.harness.model.structured import StructuredCall, call_structured


@dataclass(frozen=True, slots=True)
class IntegrationOutcome:
    """整合者这一趟交回的东西：报文 + 报文里**没进卡片**的那些条。

    截断不是错误（`schemas.parse_packet_trimmed` 的语义）：用例层决定要不要先重装一次，
    溢出最终变成留痕与草案卡上的缺口行（"不静默丢"）。

    `discarded` 是同一类东西的另一半（P23）：容器里**根本不是对象**的元素被摘掉了。
    席位与主张早就能摘（P22 F17），整合这一包原先不能——因为**摘了没地方记账**；
    现在用例层有出口了（`Round.dropped` + `Draft.gaps`），所以一起开。
    """

    #: 这一趟的全部事实都在信封上（报文 / 依据 / 没进卡片的那些条 / 原文）。见 `seat.py`。
    #:
    #: **这里不再有 `packet` 字段**——它与 `envelope.packet` 是同一个对象，
    #: 而两条访问路径已经漂移过一次：归因补交代那步用 `replace(outcome, packet=merged)`
    #: 只换了前者，信封里那份还是补交代之前的报文。按新约定读 `outcome.envelope.packet`。
    envelope: Envelope

    @property
    def overflow(self) -> tuple[schemas.Overflow, ...]:
        """转发到信封——只有信封一处存储。"""

        return self.envelope.overflow

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


@dataclass(frozen=True, slots=True)
class DispositionOutcome:
    """小调用交回的东西：只有"没采用"的申报（+ 被摘掉的那几条）。"""

    envelope: Envelope

    @property
    def discarded(self) -> tuple[schemas.Discarded, ...]:
        return self.envelope.discarded


LIMITS_TEXT = "\n".join(
    [
        "- 一批最多 5 道题（问出去就 5 个名额，阻塞题优先）。"
        "超出的**系统会替你留着，下一批先问**——不要自己塞进 `list_items`（那是另一种东西）。"
        "轮结束还没排上的会落成 s11 未决条目；其中阻塞题会标〔未决·阻塞〕，"
        "**那是会挡住就绪判定的**，所以阻塞题别往后排",
        "- **s11 未决条目的标题只写问题本身**：不许写「本轮已出题（见题『…』）」"
        "「未出题（题位已满）」「（open12）」这类**处置说明**——那是系统自己的台账，"
        "写上去读者看不懂，而且系统靠标题认「这其实是同一件事」，写上它反而认不出来；"
        "行首的条目地址只在出 `remove` 时填进 `entry_id`，出处编号只有 `E…` 这一种",
        "- 内容候选（proposals）**一次整合最多 60 条**（这一轮可能整合好几次，"
        "每次都要守这 60）；同一件事只说一次，优先保留 s5 功能字段与验收标准。"
        "超了系统会先让你精简一次、再**截断**丢掉多余的——那几条用户拿不到（只留在留痕里），"
        "所以装不下就先出题，或者放进 list_items 下一趟再说",
        "- 一条提案的 **text 就是文档里要出现的那句话**：依据说明、为什么这么定、"
        "上下文复述，写进 `rationale`（草案卡上单独显示「它为什么这么判断」），"
        "别写进正文——正文越长，这一趟越慢，也越容易撞上输出截断",
        "- dropped_opinions 一轮最多 84 条（7 席 × 12 条）："
        "没采用的席位意见一条都不许默默丢掉",
        "- **本轮主张点名过的功能已授权**：内容必须落到 "
        "{zone: body, section_id: s5, feature_slug: <那个功能>, field: <功能字段>}；"
        "拿不准也别降级成章节级（章节级只用来写目标/范围/受众/端到端流程这类整章内容）",
        "- 一条功能内容都没有时，先把主张点到的每个功能写成 name / one_liner / goal / "
        "audience / exceptions / acceptance 里的至少一条，别让 s5 空着",
        "- **每个功能的正常路径要有一条 `main_flow`**"
        "（`{zone: \"body\", section_id: \"s5\", feature_slug: <功能>, field: \"main_flow\"}`，"
        "一条提案写完整条：1. …；2. …）：依据（主张 / 答案 / 材料）里**看不出步骤的功能，"
        "就出题问用户**——别硬编一串自己想的步骤",
        "- **s6 业务流程与交互**写的是**跨功能/端到端**的流程与异常路径："
        '{zone: "body", section_id: "s6", kind: "steps"}——**kind 必须写 steps**'
        "（写成 text 就只是普通正文：这一章会一直判成「还没有主流程」，界面与导出也不按步骤排）。"
        "一条提案写完整条流程（分步写在一段里，像功能字段的 main_flow 那样，"
        "例如「1. …；2. …；3. …」）。"
        "依据（主张 / 答案 / 材料）里**看不出来的一步都不许编**："
        "写不全就出题问用户，或者干脆空着。功能**内部**的正常路径写 s5 的 main_flow，"
        "两处别把同一段流程写两遍",
        "- **主张落在 s6 的，这一轮必须出一条 s6 的 steps 提案**：把已经写进各功能 main_flow 的步骤"
        "**按先后顺序串起来**就是它——**串起来不算编造**，但不许自己加新步骤"
        "（只许用主张 / 答案 / 材料里出现过的）；万一一步都串不起来，就出题问用户",
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
        "- **同一件事不要问两遍**（同一个 target 配同一个问法算重复题；"
        "问法只比首尾标点，中间的问号算不同的问法）：重复的那条**会被系统悄悄丢掉**，"
        "你不会收到任何提示——白写。"
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


class Integrator(Agent[IntegrationOutcome, schemas.IntegrationPacket]):
    definition = INTEGRATOR

    #: 归因补交代用的**小调用**（`02` §11.0.38）：同一个席位身份、另一种提示词，
    #: 只回答"我点名的那些里，哪些是你没采用的"。它**不重写**题目与提案——
    #: 真跑证明"重写整包"会补一批又漏一批（P23：点名 3/4/3 条补了 2/4/3，总量没降）。
    DISPOSITIONS_CALL = StructuredCall(
        purpose="opinion_dispositions",
        kind="opinion_dispositions",
        prompt="opinion_dispositions",
        packet=schemas.DispositionsPacket,
        rules=schemas.RULES,
    )
    DISPOSITIONS_SLOTS = ("opinions", "produced", "schema_json")

    def __init__(self, *, model: ModelGateway, prompts: PromptLibrary) -> None:
        super().__init__(model=model, prompts=prompts)

    async def integrate(
        self,
        *,
        document_state: str,
        claims: str,
        opinions: str,
        answered: str,
        skipped: str,
        entry_ids: str,
        #: 问过但没答也没跳过的题（`context.render_asked_questions`）。**默认空串**，
        #: 不是必填：它是一段补充上下文，缺了不影响任何机制——真跑里"没答案"才是常态，
        #: 所以调用方不传也不该炸（与 `external_material` 同一档：带默认值的上下文）。
        asked_questions: str = NO_PENDING_QUESTION_TEXT,
        external_material: str = NO_MATERIAL_TEXT,
        validation_error: str = "",
        round_id: RoundId | None = None,
        authorized_features: str = "",
        open_items: str = "",
        previous_list_items: str = "",
        contradictions: str = "",
        seat_conflicts: str = "",
        project_memory: str = NO_MEMORY_TEXT,
        project_context: str = "",
        observations_index: str = "",
        digests: str = "",
        inspirations_index: str = "",
        packet_slot: str = "",
    ) -> IntegrationOutcome:
        outcome = await self._call(
            slots={
                "document_state": document_state,
                "claims": claims,
                "opinions": opinions,
                "answered": answered,
                "asked_questions": asked_questions,
                "skipped": skipped,
                "project_memory": project_memory,
                "project_context": project_context,
                "observations_index": observations_index,
                "digests": digests,
                "inspirations_index": inspirations_index,
                "entry_ids": entry_ids,
                "external_material": external_material,
                "limits": LIMITS_TEXT,
                "feature_fields": FEATURE_FIELDS_TEXT,
                "section_slots": SECTION_SLOTS_TEXT,
                "validation_error": validation_error,
                "authorized_features": authorized_features,
                "open_items": open_items,
                "previous_list_items": previous_list_items,
                "contradictions": contradictions,
                "seat_conflicts": seat_conflicts,
            },
            metadata={} if round_id is None else {"round_id": str(round_id)},
            packet_id=self._make_packet_id(
                str(round_id or "new"), "integrator", slot=packet_slot
            ),
            run_id=str(round_id or "new"),
            produced_by="integrator",
        )
        packet = outcome.packet
        discarded = outcome.discarded
        if discarded and not (
            packet.questions
            or packet.list_items
            or packet.proposals
            or packet.dropped_opinions
        ):
            raise PacketParseError(
                "整包都是占位符：这一趟等于什么都没给（空返回不等于没意见）",
                purpose=self.definition.purpose,
                discarded=(discarded[0].container, discarded[0].index),
            )
        return IntegrationOutcome(envelope=outcome)

    async def dispositions(
        self,
        *,
        opinions: str,
        produced: str,
        round_id: RoundId | None = None,
        packet_slot: str = "",
    ) -> DispositionOutcome:
        """归因补交代：只问"我点名的那些里，哪些是你没采用的"。

        与 `integrate()` 共用同一个席位身份与模型网关，但**提示词更小**（只给点名的意见 +
        核算读过的引用），而且**不重写**题目与提案——真跑（P23）证明重写整包会"补一批、
        又漏一批"，总量降不下来。
        """

        outcome = await call_structured(
            self.DISPOSITIONS_CALL,
            gateway=self._model,
            prompts=self._prompts,
            slots={"opinions": opinions, "produced": produced},
            metadata={} if round_id is None else {"round_id": str(round_id)},
            packet_id=make_packet_id(
                str(round_id or "new"),
                self.DISPOSITIONS_CALL.kind,
                "integrator",
                slot=packet_slot,
            ),
            run_id=str(round_id or "new"),
            produced_by="integrator",
        )
        return DispositionOutcome(envelope=outcome)

