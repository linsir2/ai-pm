"""这个产品的报文长什么样：5 个信息包 + 它们的 JSON Schema + 两个去处工具。

**这里只回答"报文与去处"，不回答"怎么解析"**（2026-10-04 拆出）：
归一化引擎（补壳 / 收 null / 摘非对象 / 超限截断）与三档 `parse_packet*` 已搬进
`pmstudio.harness.model.normalize`。搬的时候把两样对产品的知识抽成入参
`NormalizeRules(signatures, write_semantics_fields)`——引擎只认它，不认产品。

**下面的三个 `parse_packet*` 是薄封装，不是兼容层**：签名与搬移前逐字一致，
因为它们是"三档口径"这一文档级 API（`02` §11.0.9）的落地处——严格档、截断档在测试里
逐条钉着口径，宽松档在四个 agent 手里。直接删掉这一层，等于让每个调用方自己传签名表。

`Overflow` / `Discarded` 在这里被 import 是**真实使用**（下面两个 wrapper 的返回类型），
顺带让既有断言 `schemas.Overflow(...)` 继续成立——它们是同一个类对象，按值相等。
"""

from typing import Literal

from pydantic import BaseModel, Field

from pmstudio.domain.exploration.item import (
    MAX_INQUIRY_BLIND_SPOTS,
    MAX_INQUIRY_HYPOTHESES,
)
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.sections import FEATURE_HOME, section_slot
from pmstudio.domain.shared.target import Target
from pmstudio.harness.model.normalize import (
    Discarded,
    NormalizeRules,
    Overflow,
    parse_lenient,
)

Label = Literal["fact", "need", "goal", "constraint", "preference", "guess", "decision"]
Zone = Literal["body", "preference"]


class AssumptionDraft(BaseModel):
    failure_consequence: str
    verification: str


class ClaimDraft(BaseModel):
    text: str
    raw_span: str = Field(
        description=(
            "逐字摘自用户输入、且连续出现的一段原话（用来跳回原文）："
            "不许改写、不许用省略号把两段拼成一条，说明性文字（如「未提及」「隐含于上下文」）"
            "不是原话，不许填在这里"
        )
    )
    label: Label
    zone: Zone = "body"
    section_id: str | None = None
    feature_slug: str | None = None
    field: str | None = None
    assumption: AssumptionDraft | None = None
    decided_by: str | None = None


class SeatPickDraft(BaseModel):
    """Analyzer 挑的席位：只能从目录里挑，并且必须说清为什么这一轮需要它。"""

    seat_id: str = Field(description="只能取上面席位目录里出现过的 id")
    why: str = Field(description="一句话：读完这段输入，为什么这个专业面要表态")


class WebQueryDraft(BaseModel):
    """Analyzer 提名的检索词（工具层 C-2b）：**只提名，不联网**。

    为什么把上限写在描述里、而不是 `max_length=3`：写死了就是"给了 4 条 → 整包重装一次"，
    而仓库对"模型多写了一条"的既定口径是**丢那一条 + 留痕**（见席位提名与互斥对子）。
    """

    query: str = Field(description="要拿去搜的一句话（像人问的那样，别堆关键词）")
    why: str = Field(
        description=(
            "为什么这一轮需要**外部客观事实**——文档里没有、用户也没说过的那种"
            "（行业标准、法规、第三方接口的既有做法）"
        )
    )


class ContradictionDraft(BaseModel):
    """一对**立场互斥**的主张。写的是**第几条**（从 1 开始），不是 `c1`。

    为什么用序号：`c1/c2` 是我们按 claims 数组下标发的，提示词里从没出现过——
    让模型写 id 等于让它猜。序号 → 主张 id 的映射由解析那一层做，只有那一处。
    """

    claim_a: int = Field(description="第几条主张（从 1 开始，按 claims 数组的顺序）")
    claim_b: int = Field(description="另一条主张的序号（从 1 开始）")
    why: str = Field(
        description=(
            "一句话说清这两条为什么**立场互斥**（例如「记录不许改」 vs 「点错了要能改」）。"
            "只是「还没定」「还没想清楚」不算互斥，别写在这里"
        )
    )


class ClaimsPacket(BaseModel):
    basis: str = Field(description="你读了哪些章节与编号才切出这些主张")
    #: 12 → 20（P21 F16-a）：真跑里一份"把前面几章补齐"的长输入被切出 17 条主张，
    #: 12 条上限直接把整轮判死（起轮 502，且**没有 `/retry` 可点**）。
    #: 20 条装得下长输入；再超就是模型失准，走 `parse_packet_trimmed` 截断 + 留痕，一样不判死。
    claims: list[ClaimDraft] = Field(min_length=1, max_length=20)
    seats: list[SeatPickDraft] = Field(
        min_length=1,
        max_length=7,
        description="这一轮该请哪些席位（从目录里挑，一般 2–6 个）",
    )
    contradictions: list[ContradictionDraft] = Field(
        default_factory=list,
        max_length=5,
        description="本轮输入内部**立场互斥**的主张对子；没有就给空数组",
    )
    web_queries: list[WebQueryDraft] = Field(
        default_factory=list,
        description=(
            "最多 3 条。**只在你真觉得需要外部客观事实时给**；"
            "用户已经说过的 / 文档或材料里已有的 / 产品取舍与偏好 —— 一律给空数组。"
            "没有就给空数组。（这里只是提名，用户会在复述卡上勾选要不要真的联网）"
        ),
    )


class OpinionDraft(BaseModel):
    local_id: str = Field(description="本席位内唯一，如 O1")
    form: Literal["question", "judgment"]
    about: str
    claim: str
    because: str
    evidence_refs: list[str] = Field(default_factory=list, description="已存在的编号，如 E4")
    basis_note: str | None = Field(default=None, description="没有可引用编号时说明为什么")


class SeatPacket(BaseModel):
    basis: str
    #: 上限跟着提示词走：`seat_review.md` 允许"最多 5 条判断"，加上疑问一共 12 条。
    #: 真跑实测：5 判断 + 5 疑问 = 10 条，旧的 8 条上限直接把整轮打死（报告 F4）。
    opinions: list[OpinionDraft] = Field(default_factory=list, max_length=12)


class HypothesisDraft(BaseModel):
    """一条待验证假设（M8 调研）。**可证伪是硬要求**：证伪方式与所需真实证据都不许空。"""

    local_id: str = Field(default="", description="本包内唯一，如 H1；留空由系统按顺序补")
    statement: str = Field(description="一句**可证伪**的陈述，不是结论、不是愿望")
    grounding: Literal["document", "material", "invented"] = Field(
        description="document=从上面文档的内容推的；material=从材料推的；invented=凭空推演"
    )
    based_on: list[str] = Field(
        default_factory=list,
        description="grounding=document 时必填（只能是文档里出现过的 E 编号）；invented 时必须留空",
    )
    falsification: str = Field(description="什么观察会推翻它")
    evidence_needed: str = Field(
        description="要什么真实证据才能确认（访谈谁 / 看什么行为 / 做什么实验）"
    )


class BlindSpotDraft(BaseModel):
    """一条盲区：模拟答不了、只能靠真实接触回答的问题。"""

    local_id: str = Field(default="", description="本包内唯一，如 B1；留空由系统按顺序补")
    statement: str = Field(description="我们现在不知道什么")
    next_step: str = Field(description="下一步去哪问 / 去看什么（一句话就能执行）")


class InquiryPacket(BaseModel):
    """调研回包：假设 + 盲区。两样都空**不算成功**（由解析层判，不靠提示词自觉）。"""

    basis: str = Field(description="你读了文档里的哪些内容才推出这些")
    hypotheses: list[HypothesisDraft] = Field(
        default_factory=list, max_length=MAX_INQUIRY_HYPOTHESES
    )
    blind_spots: list[BlindSpotDraft] = Field(
        default_factory=list, max_length=MAX_INQUIRY_BLIND_SPOTS
    )


class DiscussionReply(BaseModel):
    """讨论里的一轮回答：全文 + 引用的 E 编号；空串不算成功。"""

    text: str = Field(
        min_length=1, description="这一轮的回答全文；空串不算成功"
    )
    based_on: list[str] = Field(
        default_factory=list, description="引用的 E 编号；凭空推演留空"
    )


class DiscussionCardDraft(BaseModel):
    """一条待收下的灵感（M9 讨论）。模型不管 id：`card_id` 由服务端 stamp C1..。"""

    statement: str = Field(description="一条可收下的灵感，一句话")
    grounding: Literal["document", "invented"] = Field(
        description="document 必须带 based_on；invented 必须留空"
    )
    based_on: list[str] = Field(default_factory=list)


class CardsPacket(BaseModel):
    """组装回包：1-3 张卡。空 list 不算成功；超 3 不算成功（重装指引会说）。"""

    cards: list[DiscussionCardDraft] = Field(min_length=1, max_length=3)


class TargetDraft(BaseModel):
    zone: Zone = "body"
    section_id: str | None = Field(
        default=None, description="章节 id，如 s5；写功能字段时必须是 s5"
    )
    feature_slug: str | None = Field(
        default=None,
        description="功能标识，如 order-export；**给了 field 就必须给 feature_slug**",
    )
    field: str | None = Field(
        default=None,
        description=(
            "功能字段名，只能取功能字段表里的名字（如 acceptance / exceptions / one_liner）；"
            "必须与 feature_slug 一起给；章节级内容不要给 field"
        ),
    )


class OptionDraft(BaseModel):
    option_id: str
    text: str


class QuestionDraft(BaseModel):
    # 必填（2026-09-29 P11 C3）：默认值会把"忘了写"和"判断成 gap"混成一种——
    # 真跑 `edge-contradiction` 的 15 道题里 8 道没写 kind，全被默认值吞成 gap。
    kind: Literal["gap", "conflict", "decision"]
    title: str
    why: str
    consequence_if_skipped: str
    target: TargetDraft
    type: Literal["single", "multi", "text"] = "single"
    options: list[OptionDraft] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    from_opinions: list[str] = Field(
        default_factory=list,
        description=(
            "这道题来自哪几条席位意见（席位条目地址，如 seat.business_rules:O1）。"
            "这只是归因，不是出处编号；出处编号写在 evidence_refs 里"
        ),
    )


class ListItemDraft(BaseModel):
    text: str
    target: TargetDraft
    reason: str


class ProposalDraft(BaseModel):
    op: Literal["add", "replace", "remove"] = "add"
    target: TargetDraft
    text: str = Field(
        description=(
            "这一条要写进文档的内容。**add / replace 必须有内容**；"
            "**remove 可以留空**（要撤下哪一条看 entry_id）"
        )
    )
    origin_type: Literal["user", "derived", "proposed", "system_default"] = "derived"
    evidence_refs: list[str] = Field(min_length=1)
    rationale: str | None = None
    kind: str | None = Field(
        default=None, description="章节级内容才给：text/acceptance/nfr/bullet_list/…"
    )
    entry_id: str | None = Field(
        default=None, description="replace/remove 必填，用既有条目清单里的 id"
    )


class DroppedOpinionDraft(BaseModel):
    """模型自己申报的丢弃：一条都不许默默丢掉（`04-未决.md` C5 后半）。"""

    handle: str = Field(min_length=1, description="席位条目地址，如 seat.business_rules:O1")
    reason: str = Field(min_length=1, description="一句话：为什么这条意见没有进卡片")


class DispositionsPacket(BaseModel):
    """归因补交代那条**小调用**的唯一回答形状（`02` §11.0.38）。

    只有 `dropped_opinions` 一个字段，而且只回答"我点名的那些里，哪些是你没采用的"。
    为什么不让它回"我用了"：那样等于**让模型自己把"没人交代"这条指标清零**，
    而 `04-未决.md` C5 早就否掉了"赌模型老实交代"这条设计。**它没列的就仍算没交代**，
    沉默还是沉默——这条指标才不会被自报抹平。
    """

    dropped_opinions: list[DroppedOpinionDraft] = Field(
        default_factory=list,
        max_length=84,
        description=(
            "上面点名的意见里，你**没采用**的那些：handle 照抄 + 一句话原因"
            "（重复 / 无依据 / 与本轮无关 / 已经落到别处…）。**一条都不许漏**"
        ),
    )


class IntegrationPacket(BaseModel):
    questions: list[QuestionDraft] = Field(default_factory=list, max_length=12)
    list_items: list[ListItemDraft] = Field(default_factory=list, max_length=20)
    #: 一棵完整功能树 12 个字段；30 条装不下 3 棵（36），真跑里 44 条提案被整包判死（报告 F2）。
    proposals: list[ProposalDraft] = Field(default_factory=list, max_length=60)
    dropped_opinions: list[DroppedOpinionDraft] = Field(
        default_factory=list,
        #: 7 个席位 × 单席位 12 条 = 84——"一条都不许默默丢掉"要在数学上成立（报告 F11）。
        max_length=84,
        description="你没采用的席位意见：给出地址与原因；**一条都不许默默丢掉**",
    )


class MemoryChangeDraft(BaseModel):
    """记忆变更指令（M6）。**提议，不是事实**——只有用户在草案卡上勾了才生效。

    形状故意宽松（与 `DroppedOpinionDraft` 同一个理由）：模型写歪的东西正是要留痕的东西。
    真正的判据在领域层（`domain/memory/memory.py::screen_changes`）：缺出处 / 账本里没有 /
    点名不存在 / 超两行 / 装不下——一处实现，这里不重复判。
    """

    op: Literal["add", "update", "merge", "delete"]
    kind: Literal["decision", "rejected", "lesson"]
    text: str = ""
    refs: list[str] = Field(
        default_factory=list,
        description="出处编号（E 开头，上面清单里出现过的）；add/update/merge 必填至少一个",
    )
    target_id: str | None = Field(
        default=None, description="update/merge/delete 必填：上面记忆清单里那条的 id"
    )
    reason: str = Field(default="", description="一句话：为什么这一条值得记")


class MemoryPacket(BaseModel):
    """记忆整理者的唯一回答形状。`changes` 上限 5 条：一轮能攒下的"值得记住的事"没那么多。"""

    changes: list[MemoryChangeDraft] = Field(
        default_factory=list,
        max_length=5,
        description="这一批的变更指令（最多 5 条）；没东西可记就返回空数组",
    )


#: 顶层数组的元素 → 容器。**顺序本身是判据的一部分**：命中即停，先查特征最强的键。
#: 题面特征（`title` / `consequence_if_skipped`）必须排在 `evidence_refs` 前面——
#: 题目也可能带依据编号，先看题面才能把它和提案分开。
#: 只有"容器都有默认值"的包才进这张表：补壳不必凭空造必填字段。
#: 主张包 / 席位包必填 `basis`，所以**不给**签名表——那会让补壳变成编内容。
_SIGNATURES: dict[type[BaseModel], tuple[tuple[str, tuple[frozenset[str], ...]], ...]] = {
    IntegrationPacket: (
        ("dropped_opinions", (frozenset({"handle"}),)),
        ("questions", (frozenset({"title"}), frozenset({"consequence_if_skipped"}))),
        ("list_items", (frozenset({"text", "target", "reason"}),)),
        ("proposals", (frozenset({"evidence_refs"}), frozenset({"op"}))),
    ),
    MemoryPacket: (("changes", (frozenset({"op", "kind"}),)),),
}

#: 这几个字段**不适用**"null → 用默认值"：
#: `op` / `entry_id` 决定写/改/删哪一条；`form` 决定这条意见进不进账本（有没有编号）。
_WRITE_SEMANTICS_FIELDS = frozenset({"op", "entry_id", "form"})

#: 递给引擎的规则——全仓唯一一份，装配点只有这里。
#: **不带下划线**：它被四个 agent 当契约用（下划线私有被跨模块 import 是一种骗人的约定），
#: 由 `tests/application/test_agent_calls.py` 钉住"所有报文共用这一份"。
RULES = NormalizeRules(
    signatures=_SIGNATURES, write_semantics_fields=_WRITE_SEMANTICS_FIELDS
)


def parse_packet_lenient[T: BaseModel](
    text: str, model: type[T], *, purpose: str
) -> tuple[T, tuple[Overflow, ...], tuple[Discarded, ...]]:
    """宽松档：截断档 + 摘掉容器里根本不是对象的元素（真跑 F17 的占位符字符串）。"""

    return parse_lenient(text, model, purpose=purpose, rules=RULES)


def target_of(draft: TargetDraft) -> Target:
    """线形状 → Target。形状不对时抛领域错误；调用方负责转成 PacketParseError。"""

    if draft.zone == "preference":
        if any((draft.section_id, draft.feature_slug, draft.field)):
            raise ShapeError("偏好区的 target 不带章节、功能与字段")
        return Target.preference()
    section = SectionId(draft.section_id) if draft.section_id else FEATURE_HOME
    if draft.field is not None:
        if draft.feature_slug is not None:
            return Target.feature_field(FeatureSlug(draft.feature_slug), draft.field)
        # 没有 feature_slug 时，field 只能是**章节槽**（s8 的六块，P5a）。
        # 真模型实测：这里以前一律要求 feature_slug，于是六个槽对 AI 路径根本不可达。
        if section_slot(section, draft.field) is None:
            raise ShapeError(
                f"{section} 没有名为 {draft.field} 的章节槽；功能字段必须和 feature_slug 一起给"
            )
        return Target.body(section, field=draft.field)
    if draft.feature_slug is not None:
        return Target.body(section, feature_slug=FeatureSlug(draft.feature_slug))
    return Target.body(section)


def normalize_claim_target(draft: TargetDraft, *, label: str) -> Target:
    """主张的"归位建议"规范化：套用领域规则，而不是把模型的笔误变成整轮失败。

    规则（真模型冒烟实测出来的，见汇报）：
    - **标签优先**：`label=preference` ⇒ 偏好区（LB1 说"偏好不进正文"，标签才是权威分类，
      `zone` 只是它的复述——模型两处写不一致时以标签为准）；
    - 有 `feature_slug` ⇒ 章节必然是 s5（功能子树只长在 s5，D3）；模型写成 s12 也纠正过来；
    - 有 `field` 但没 `feature_slug` ⇒ 丢掉 field（字段必须挂在功能下，留着没意义）；

    注意这里**不丢内容**：主张文本、原话、标签都照旧，用户在开场卡上还能改去处。
    章节 id 本身不认识（比如 s99）仍然报错——那是幻觉，不能猜。
    """

    if label == "preference" or draft.zone == "preference":
        return Target.preference()
    if draft.feature_slug:
        return Target.feature_field(FeatureSlug(draft.feature_slug), draft.field) if draft.field \
            else Target.body(FEATURE_HOME, feature_slug=FeatureSlug(draft.feature_slug))
    return Target.body(SectionId(draft.section_id) if draft.section_id else FEATURE_HOME)
