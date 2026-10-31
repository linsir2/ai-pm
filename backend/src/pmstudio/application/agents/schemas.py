"""这个产品的报文长什么样：信息包 + 它们的 JSON Schema + 两个去处工具。

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

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from pmstudio.domain.exploration.item import (
    MAX_INQUIRY_BLIND_SPOTS,
    MAX_INQUIRY_HYPOTHESES,
    MAX_SIM_INQUIRY_HYPOTHESES,
    MAX_SIM_INQUIRY_SLICES,
    MAX_SIM_INQUIRY_UNKNOWNS,
)
from pmstudio.domain.exploration.research import (
    MAX_RESEARCH_FINDINGS,
    MAX_RESEARCH_OPEN_QUESTIONS,
)
from pmstudio.domain.shared.errors import ShapeError
from pmstudio.domain.shared.ids import FeatureSlug, SectionId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template_adapter import TemplateAdapter
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
    material_eids: list[str] = Field(
        default_factory=list,
        description=(
            "这条主张主要依据的材料块编号（如 E43，本轮外部材料那节里出现过的）。"
            "没依据就留空——乱写不存在或没给它看过的编号，整包走重装"
        ),
    )


class SeatPickDraft(BaseModel):
    #: Analyzer 挑的席位：只能从目录里挑，并且必须说清为什么这一轮需要它。

    seat_id: str = Field(description="只能取上面席位目录里出现过的 id")
    why: str = Field(description="一句话：读完这段输入，为什么这个专业面要表态")


class WebQueryDraft(BaseModel):
    #: Analyzer 提名的检索词（工具层 C-2b）：**只提名，不联网**。
    #:
    #:    为什么把上限写在描述里、而不是 `max_length=3`：写死了就是"给了 4 条 → 整包重装一次"，
    #:    而仓库对"模型多写了一条"的既定口径是**丢那一条 + 留痕**（见席位提名与互斥对子）。
    #:

    query: str = Field(description="要拿去搜的一句话（像人问的那样，别堆关键词）")
    why: str = Field(
        description=(
            "为什么这一轮需要**外部客观事实**——文档里没有、用户也没说过的那种"
            "（行业标准、法规、第三方接口的既有做法）"
        )
    )


class WebRequestDraft(BaseModel):
    #: 三方共用的结构化联网申请（P3a）：**只提名，不联网**。
    #:
    #:    上限不写进 schema：模型多写一条的既定口径是**丢那一条 + 留痕**，
    #:    不是整包重装。名额由用例层在 `attach_web_requests` 之前按方取前 3。

    query: str = Field(
        default="", description="要拿去搜的一句话（像人问的那样，别堆关键词）"
    )
    why: str = Field(
        default="",
        description=(
            "为什么需要**外部客观事实**——文档里没有、材料里没有、用户也答不了的那种"
            "（行业标准、法规条款、第三方接口的既有做法）"
        ),
    )


class ContradictionDraft(BaseModel):
    #: 一对**立场互斥**的主张。写的是**第几条**（从 1 开始），不是 `c1`。
    #:
    #:    为什么用序号：`c1/c2` 是我们按 claims 数组下标发的，提示词里从没出现过——
    #:    让模型写 id 等于让它猜。序号 → 主张 id 的映射由解析那一层做，只有那一处。
    #:

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
    web_requests: list[WebRequestDraft] = Field(
        default_factory=list,
        description=(
            "本席位想查的外部客观事实（最多 3 条；**只提名，不联网**，"
            "是否批准由用户决定）。没有就给空数组"
        ),
    )


class HypothesisDraft(BaseModel):
    #: 一条待验证假设（M8 调研）。**可证伪是硬要求**：证伪方式与所需真实证据都不许空。

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
    #: 一条盲区：模拟答不了、只能靠真实接触回答的问题。

    local_id: str = Field(default="", description="本包内唯一，如 B1；留空由系统按顺序补")
    statement: str = Field(description="我们现在不知道什么")
    next_step: str = Field(description="下一步去哪问 / 去看什么（一句话就能执行）")


class InquiryPacket(BaseModel):
    #: 调研回包：假设 + 盲区。两样都空**不算成功**（由解析层判，不靠提示词自觉）。

    basis: str = Field(description="你读了文档里的哪些内容才推出这些")
    hypotheses: list[HypothesisDraft] = Field(
        default_factory=list, max_length=MAX_INQUIRY_HYPOTHESES
    )
    blind_spots: list[BlindSpotDraft] = Field(
        default_factory=list, max_length=MAX_INQUIRY_BLIND_SPOTS
    )


class ResearchFindingDraft(BaseModel):
    #: 一条结论。`refs` 用**来源序号**（`S1..S5`，就是提示词里来源卡前面的那个号），
    #: 不是 E 编号：E 在入库那一刻才发，模型这一趟还没见过它。
    #: 装配层把 S 号映射成 E 之后才进领域——领域里不存 S（那是报文内的瞬态）。

    text: str = Field(description="一句结论：根据下面点名的来源，我判断……")
    refs: list[str] = Field(min_length=1, description="来源序号（S1、S2…），至少一个")


class ResearchPacket(BaseModel):
    #: 研究综合回包（P3b-1）：结论 + 开放问题。**结论可以空**（只搜到来源、没下判断是合法结果）；
    #: 零来源不是报文能表达的事，由用例判失败。
    #:
    #: 没有 `basis`：结论自带的 `refs` 就是依据，"我读了哪几张卡"这句话没有消费者
    #: （界面显示结论 + 引用，不显示一句总结），多一个必填字段只是多一种失败模式。

    findings: list[ResearchFindingDraft] = Field(
        default_factory=list, max_length=MAX_RESEARCH_FINDINGS
    )
    open_questions: list[str] = Field(
        default_factory=list, max_length=MAX_RESEARCH_OPEN_QUESTIONS
    )


class SimSliceDraft(BaseModel):
    #: 一个处境切片（M10 模拟调研）：任务/处境分组，不是人口学画像。
    #:
    #:    schema 里没有可填身份属性的位置；`anchors` 是它唯一的机械作用
    #:    （装配层逐个验账本存在性，落空则丢条 + 留痕）。
    #:

    local_id: str = Field(default="", description="本包内唯一，如 S1；留空由系统按顺序补")
    situation: str = Field(description="谁·在什么场景·要完成什么（不许写身份属性）")
    job: str = Field(description="他真正要推进的一件事")
    trigger: str = Field(description="触发点")
    workaround: str = Field(description="现在怎么凑合（替代方案/手工流程/竞品）")
    anchors: list[str] = Field(
        min_length=1, description="出发点：文档/材料里已有的 E 编号（≥1 个）"
    )


class SimHypothesisDraft(BaseModel):
    #: 一条模拟假设：字段集锁死这 7 个。
    #:
    #:    kind / subject / touches 被砍掉过（M10 审核：没有任何机械消费者，
    #:    分组看 grounding 就够，去向提示是展示文案不是字段）——加回来先过评审。
    #:

    local_id: str = Field(default="", description="本包内唯一，如 H1；留空由系统按顺序补")
    slice_id: str = Field(description="挂在哪个切片（装配层验存在性，挂不上则丢条 + 留痕）")
    statement: str = Field(description="一句**可证伪**的陈述，不是结论")
    grounding: Literal["document", "material", "invented"] = Field(
        description="document=从文档推的；material=从材料推的；invented=凭空推演"
    )
    based_on: list[str] = Field(default_factory=list)
    falsification: str = Field(description="什么观察会推翻它")
    evidence_needed: str = Field(description="要什么真实证据才能确认")


class SimUnknownDraft(BaseModel):
    #: 一条盲区：模拟答不了、只能靠真实接触回答的问题（只活在 artifact，不落文档）。

    local_id: str = Field(default="", description="本包内唯一，如 U1；留空由系统按顺序补")
    statement: str = Field(description="我们现在不知道什么")
    why_it_matters: str = Field(description="为什么值得问")
    how_to_find_out: str = Field(description="只能靠真实接触的动作")


class SimInquiryPacket(BaseModel):
    #: 模拟调研回包：切片 + 假设 + 盲区。
    #:
    #:    分层：parse 只管形状（空假设/空盲区放行）；"没东西算不算成功"、
    #:    slice 挂载、账本锚点都是装配层的事。
    #:

    basis: str = Field(description="你读了文档里的哪些内容才推出这些")
    slices: list[SimSliceDraft] = Field(min_length=1, max_length=MAX_SIM_INQUIRY_SLICES)
    hypotheses: list[SimHypothesisDraft] = Field(
        default_factory=list, max_length=MAX_SIM_INQUIRY_HYPOTHESES
    )
    unknowns: list[SimUnknownDraft] = Field(
        default_factory=list, max_length=MAX_SIM_INQUIRY_UNKNOWNS
    )


# ── 模拟评测（M11）：一个 persona 一趟文本走查 ──────────────────────────────

#: 每功能走查步骤上限：防输出膨胀（`02` 输出侧教训，同 M10 的 3/6/3 口径）。
MAX_SIM_REVIEW_WALKTHROUGH_STEPS = 12

#: 每功能摩擦点上限。
MAX_SIM_REVIEW_FRICTIONS = 6

#: 每功能验收核对上限：正常功能 2–6 条 acceptance；超过这个数的功能 v1 评测不出来
#: （覆盖率筛会判"没核完"→ 重装 → 整趟失败，是如实的"机器没走完"）。
MAX_SIM_REVIEW_CHECKS = 12


class SimWalkStepDraft(BaseModel):
    #: 一步走查：我在这一步做什么、文档支不支持、卡在缺什么。

    step_index: int = Field(description="对齐文档 main_flow 的步骤序号（从 1 起）")
    step_text: str = Field(description="文档里的原步骤文字（抄写，不改写）")
    from_doc: str = Field(
        default="", description="这一步从文档哪儿读来的（entry_id，或章节#字段）"
    )
    action: str = Field(description="你（模拟使用者）这一步要做什么")
    outcome: Literal["ok", "stuck", "guessed"] = Field(
        description="ok=文档支持我走完；stuck=走不下去；guessed=只能猜"
    )
    need: str = Field(default="", description="outcome 不是 ok 时必须写：缺什么信息")


class SimFrictionDraft(BaseModel):
    #: 一个摩擦点：**要么引原文、要么明确标"文档没写"**（二选一，机械判）。

    local_id: str = Field(default="", description="本功能内唯一，如 F1；留空由系统补")
    kind: Literal[
        "missing_entry",
        "missing_feedback",
        "ambiguous_rule",
        "dead_end",
        "extra_work",
        "trust_gap",
        "edge_break",
        "cannot_start",
    ] = Field(description="摩擦分类；机械规则只消费 dead_end 与 cannot_start")
    entry_id: str = Field(default="", description="指向文档条目（优先用这个）")
    field: str = Field(default="", description="没有条目时指向功能字段名（如 rules）")
    quote: str = Field(default="", description="文档原文（与 not_documented 二选一）")
    not_documented: bool = Field(
        default=False, description="文档确实没写（与 quote 二选一）"
    )
    blocks_flow: bool = Field(default=False, description="这一步是不是被彻底堵住")
    note: str = Field(description="摩擦是什么样的（不许写身份属性、不许编数量）")


class SimAcceptanceDraft(BaseModel):
    #: 一条验收标准的核对：浅到"文档有没有说清它"，不预测真实行为。

    local_id: str = Field(default="", description="本功能内唯一，如 A1；留空由系统补")
    entry_id: str = Field(description="对应文档里的那条 acceptance 条目")
    status: Literal["verified", "unverified", "contradicted"] = Field(
        description="verified=照文档能核对；unverified=文档没说清；contradicted=与文档其它内容打架"
    )
    note: str = Field(default="", description="为什么是这个状态（可空）")


class SimFeatureReviewDraft(BaseModel):
    #: 一个功能的走查报告：走查 + 摩擦 + 验收核对 + 三档结论。

    feature_slug: str = Field(description="必须是本次被点名的功能之一")
    started: bool = Field(description="false=照文档根本开不了头（要么文档没有，要么进不去）")
    walkthrough: list[SimWalkStepDraft] = Field(
        default_factory=list, max_length=MAX_SIM_REVIEW_WALKTHROUGH_STEPS
    )
    frictions: list[SimFrictionDraft] = Field(
        default_factory=list, max_length=MAX_SIM_REVIEW_FRICTIONS
    )
    acceptance_checks: list[SimAcceptanceDraft] = Field(
        default_factory=list, max_length=MAX_SIM_REVIEW_CHECKS
    )
    verdict: Literal["smooth", "friction", "blocked"] = Field(
        description="三档，不打分：smooth / friction / blocked"
    )
    verdict_because: list[str] = Field(
        default_factory=list, description="结论文号（引本功能的 friction local_id）"
    )


class SimReviewPacket(BaseModel):
    #: 模拟评测回包：一个 persona 对全部被点名功能的走查（M11）。
    #:
    #:    不叫 SimulationPacket 之外的名字是为了和 `SimInquiryPacket` 成对：
    #:    同一个 `simulated` 编号体系、同一条"默认不勾"的降级链，但产物形状不同
    #:    （评测=走查报告+减提案，调研=处境切片+假设）。
    #:

    basis: str = Field(description="你读了文档里的哪些内容才走出这些")
    features: list[SimFeatureReviewDraft] = Field(
        min_length=1, description="被点名的每个功能一份报告"
    )


class DiscussionReply(BaseModel):
    #: 讨论里的一轮回答：全文 + 引用的 E 编号；空串不算成功。

    text: str = Field(
        min_length=1, description="这一轮的回答全文；空串不算成功"
    )
    based_on: list[str] = Field(
        default_factory=list, description="引用的 E 编号；凭空推演留空"
    )


class DiscussionCardDraft(BaseModel):
    #: 一条待收下的灵感（M9 讨论）。模型不管 id：`card_id` 由服务端 stamp C1..。

    statement: str = Field(description="一条可收下的灵感，一句话")
    grounding: Literal["document", "invented"] = Field(
        description="document 必须带 based_on；invented 必须留空"
    )
    based_on: list[str] = Field(default_factory=list)


class CardsPacket(BaseModel):
    #: 组装回包：1-3 张卡。空 list 不算成功；超 3 不算成功（重装指引会说）。

    cards: list[DiscussionCardDraft] = Field(min_length=1, max_length=3)


class DiscussionSynthesis(BaseModel):
    #: 一轮的综合：共识 / 分歧 / 未决三组清单。只归纳发言里出现过的内容，
    #: 不开新观点、不编 E 编号（引用归卡片管）。三组都可以空。

    agreed: list[str] = Field(default_factory=list, description="大家都认的，每条一句话")
    disagreed: list[str] = Field(default_factory=list, description="明确唱反调的，每条一句话")
    open: list[str] = Field(default_factory=list, description="没说清、要追问的，每条一句话")


class TargetDraft(BaseModel):
    zone: Zone = "body"
    section_id: str | None = Field(
        default=None,
        description=(
            "章节 id（如 s5；写功能字段时必须是功能锚点章——章节表里标了 feature 的那章）"
        ),
    )
    feature_slug: str | None = Field(
        default=None,
        description="功能标识，如 order-export；功能字段必须给，章节槽不要给",
    )
    field: str | None = Field(
        default=None,
        description=(
            "功能字段名（如 acceptance / exceptions / one_liner）与 feature_slug 一起给；"
            "章节槽名按章节表填写（如 field=cost、section_id=s8），"
            "不给 feature_slug；普通章节内容不带 field"
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
    op: Literal["add", "replace", "remove"]
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
        description="键必填：add 明确给 null；replace/remove 给既有条目清单里的非空 id"
    )

    @field_validator("entry_id")
    @classmethod
    def check_entry_id(cls, value: str | None, info: ValidationInfo) -> str | None:
        op = info.data.get("op")
        if op == "add" and value is not None:
            raise ValueError("add 的 entry_id 必须明确为 null")
        if op in ("replace", "remove") and (value is None or not value.strip()):
            raise ValueError("replace/remove 必须给非空 entry_id")
        return value


class DroppedOpinionDraft(BaseModel):
    #: 模型自己申报的丢弃：一条都不许默默丢掉（`04-未决.md` C5 后半）。

    handle: str = Field(min_length=1, description="席位条目地址，如 seat.business_rules:O1")
    reason: str = Field(min_length=1, description="一句话：为什么这条意见没有进卡片")


class DispositionsPacket(BaseModel):
    #: 归因补交代那条**小调用**的唯一回答形状（`02` §11.0.38）。
    #:
    #:    只有 `dropped_opinions` 一个字段，而且只回答"我点名的那些里，哪些是你没采用的"。
    #:    为什么不让它回"我用了"：那样等于**让模型自己把"没人交代"这条指标清零**，
    #:    而 `04-未决.md` C5 早就否掉了"赌模型老实交代"这条设计。**它没列的就仍算没交代**，
    #:    沉默还是沉默——这条指标才不会被自报抹平。
    #:

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
    web_requests: list[WebRequestDraft] = Field(
        default_factory=list,
        description=(
            "本轮需要核对的**外部客观事实**（最多 3 条；只提名，不联网，"
            "不产生 E 编号，不能当依据引用）。没有就给空数组"
        ),
    )


class MemoryChangeDraft(BaseModel):
    #: 记忆变更指令（M6）。**提议，不是事实**——只有用户在草案卡上勾了才生效。
    #:
    #:    形状故意宽松（与 `DroppedOpinionDraft` 同一个理由）：模型写歪的东西正是要留痕的东西。
    #:    真正的判据在领域层（`domain/memory/memory.py::screen_changes`）：缺出处 / 账本里没有 /
    #:    点名不存在 / 超两行 / 装不下——一处实现，这里不重复判。
    #:

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
    #: 记忆整理者的唯一回答形状。`changes` 上限 5 条：一轮能攒下的"值得记住的事"没那么多。

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


def target_of(draft: TargetDraft, *, adapter: TemplateAdapter) -> Target:
    """线形状 → Target。形状或成员不对时抛领域错误；调用方负责转成 PacketParseError。

    `adapter` 是这一份文档的模板视角（`02` §11.0.69）：功能挂哪一章、有哪些章节槽，
    都由它说了算——同一套代码在 13 章与 6 章模板上都能把去处翻译对。
    """

    if draft.zone == "preference":
        if any((draft.section_id, draft.feature_slug, draft.field)):
            raise ShapeError("偏好区的 target 不带章节、功能与字段")
        return Target.preference()
    section = SectionId(draft.section_id) if draft.section_id else adapter.feature_home
    if draft.field is not None:
        if draft.feature_slug is not None:
            target = Target.body(section, feature_slug=FeatureSlug(draft.feature_slug),
                                 field=draft.field)
        else:
            # 没有 feature_slug 时，field 只能是**章节槽**（standard 里是那六块，P5a）。
            # 真模型实测：这里以前一律要求 feature_slug，于是六个槽对 AI 路径根本不可达。
            if adapter.slot(section, draft.field) is None:
                raise ShapeError(
                    f"{section} 没有名为 {draft.field} 的章节槽；功能字段必须和 feature_slug 一起给"
                )
            target = Target.body(section, field=draft.field)
    elif draft.feature_slug is not None:
        target = Target.body(section, feature_slug=FeatureSlug(draft.feature_slug))
    else:
        target = Target.body(section)
    adapter.check_target(target.section_id, target.feature_slug, target.field)
    return target


def normalize_claim_target(
    draft: TargetDraft, *, label: str, adapter: TemplateAdapter
) -> Target:
    """主张的"归位建议"规范化：套用领域规则，而不是把模型的笔误变成整轮失败。

    规则（真模型冒烟实测出来的，见汇报）：
    - **标签优先**：`label=preference` ⇒ 偏好区（LB1 说"偏好不进正文"，标签才是权威分类，
      `zone` 只是它的复述——模型两处写不一致时以标签为准）；
    - 有 `feature_slug` ⇒ 章节必然是**功能锚点章**（功能子树只长在那儿，D3）；
      模型写成别的章也纠正过来；
    - 有 `field` 但没 `feature_slug` ⇒ 丢掉 field（字段必须挂在功能下，留着没意义）；

    注意这里**不丢内容**：主张文本、原话、标签都照旧，用户在开场卡上还能改去处。
    章节 id 本身不认识（比如 s99）仍然报错——那是幻觉，不能猜。
    """

    if label == "preference" or draft.zone == "preference":
        return Target.preference()
    if draft.feature_slug:
        target = (
            Target.feature_field(
                FeatureSlug(draft.feature_slug), draft.field, home=adapter.feature_home
            )
            if draft.field
            else Target.body(adapter.feature_home, feature_slug=FeatureSlug(draft.feature_slug))
        )
    else:
        target = Target.body(
            SectionId(draft.section_id) if draft.section_id else adapter.feature_home
        )
    adapter.check_target(target.section_id, target.feature_slug, target.field)
    return target
