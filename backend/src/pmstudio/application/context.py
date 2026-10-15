"""把领域现状渲染成提示词上下文。纯函数，无 IO，无模型——这样它可被单测钉住。

席位与整合者拿到的是**全文渲染**（作者要求：不读全文档怎么确保信息读全）。
文档真变大以后的索引层在设计里留位（P6），P2 不做。

**这是智能体上下文的唯一出口**：将来的 `PROJECT.md`（必读层）只是这条渲染链上的一节，
不是新的真相源（计划 §3.13）。
"""

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from dataclasses import dataclass

from pmstudio.application.dto import MaterialChunk, MaterialPacket
from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.blocks import Entry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.authoring.schema import FEATURE_FIELDS
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.memory.context import ProjectContext
from pmstudio.domain.memory.digest import RoundDigest
from pmstudio.domain.memory.inspiration import InspirationItem
from pmstudio.domain.memory.memory import MemoryEntry, MemoryKind, kind_title
from pmstudio.domain.memory.observations import ObservationType, RoundObservation
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim, SeatConflict
from pmstudio.domain.round.draft import Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import Question
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.errors import BadTemplateDefinition
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.sections import slot_block_id
from pmstudio.domain.shared.template_adapter import TemplateAdapter
from pmstudio.domain.shared.writing import BlockKind

LABEL_TITLES: dict[ClaimLabel, str] = {
    ClaimLabel.FACT: "事实",
    ClaimLabel.NEED: "需求",
    ClaimLabel.GOAL: "目标",
    ClaimLabel.CONSTRAINT: "约束",
    ClaimLabel.PREFERENCE: "偏好",
    ClaimLabel.GUESS: "猜测",
    ClaimLabel.DECISION: "决定",
}

LABEL_HINTS: dict[ClaimLabel, str] = {
    ClaimLabel.FACT: "已经是这样的事（如「运营每天都要用」）",
    ClaimLabel.NEED: "要做出来的能力",
    ClaimLabel.GOAL: "要达成的结果",
    ClaimLabel.CONSTRAINT: "不可协商的限制（时间、合规、预算）",
    ClaimLabel.PREFERENCE: "个人口味，不进正文，进偏好区",
    ClaimLabel.GUESS: "没把握的判断，必须升格为假设（写失败后果与验证方式）",
    # 真跑实测（2026-10-10）：只说"已经拍板的事"模型识别不出来——它把
    # 「复核入口这期也定了，放在订单详情页」标成 preference，而 preference 不进正文、
    # 不进交付，一条已经定的事就这么静默丢了。所以补两样：**识别线索**（用户原话里
    # "定了"都有哪些说法）+ **与偏好的边界**（拍板的是"这件事怎么做"，进正文；
    # 偏好是口味风格，不进）。
    ClaimLabel.DECISION: (
        "用户已经拍板的事——原话里常见「决定了 / 就这么定 / 这期定了 / 按X来 / 不用Y」。"
        "与偏好的区别：拍板的是**这件事怎么做**（进正文），偏好是口味与风格（不进正文）。"
        "必须写 decided_by（谁拍的；本项目只有一个用户，写「用户」）"
    ),
}

LABELS_TEXT = "\n".join(
    f"- {label.value}（{LABEL_TITLES[label]}）：{LABEL_HINTS[label]}" for label in ClaimLabel
)
def sections_text(adapter: TemplateAdapter) -> str:
    """章节表：**只给短 id 与标题**，并明说"section_id 只能填前面那个短 id"。

    走**文档自己的模板**（`02` §11.0.69）：自定义模板里这一句的示例 id 也跟着变——
    写"如 s3"而模板里根本没有 s3，模型就会去填一个不存在的章节。
    真模型实测：写成 `section_id: "s3 用户与受众"`（把整行抄进来）→ 未知章节 → 整轮 502。
    """

    ids = adapter.section_ids
    # 举例挑**第三章**（原来写死 s3）：第一章像格式说明、末章没人写，
    # 靠前的普通章最像"随便哪一章"，也让 standard 逐字不变。
    sample = ids[2] if len(ids) > 2 else ids[0]
    body = "\n".join(f"- {sid} {adapter.title_of(sid)}" for sid in ids)
    return f"{body}\n（section_id 只能填上面那个短 id，如 {sample}；**不要带标题、不要带空格**）"


def section_slots_text(adapter: TemplateAdapter) -> str:
    """章节槽：section 级内容也可以指定"落到哪个槽"（standard 里是 s8 的六项表态）。

    与功能字段表分开列，免得模型把槽名当功能字段。模板没有槽就说没有——
    不列一张空表，也不给一张别的模板的表。
    """

    rows = [
        f"- {slot_block_id(section_id, slot.name)}（{slot.title}，章节槽；"
        f"写它时给 section_id={section_id} + field={slot.name}，不要给 feature_slug）"
        for section_id in adapter.section_ids
        for slot in adapter.slots_of(section_id)
    ]
    return "\n".join(rows) or "（没有章节槽）"


def placement_text(adapter: TemplateAdapter) -> str:
    """归位表：内容该落到哪一章、怎么落（`$placement` 槽）。

    取代 `analyzer.md` 里那张写死 13 章的表（`02` §11.0.69）：逐章一行（id + 标题 +
    这一章是干什么的），再按角色补四条"这一章有什么特别规矩"；**步骤章那两条只在模板
    真的有步骤章时出现**——教模型往不存在的章写，比不教更糟。
    """

    lines = [
        f"- {sid} {adapter.title_of(sid)}：{adapter.purpose_of(sid)}"

        for sid in adapter.section_ids
    ]
    home = str(adapter.feature_home)
    open_home = str(adapter.open_home)
    steps = adapter.steps_section()
    decision = adapter.decision_section()
    # 功能怎么走一遍 → 指到功能字段 main_flow（P38 真跑相关性：标了 89% 会写，没标 23%）。
    # 这条不是模板事实（功能字段表是全局的），但**落点**是：写在哪一章。
    lines.append(
        f"- 功能字段只落在 {home} 那一章；章节级内容（目标 / 范围 / 受众 / 整章流程）落在别的章，"
        f"别都堆进 {home}"
    )
    lines.append(
        f"- 还没定的事落 {open_home}（标题只写问题本身）；"
        f"{adapter.version_home} 由系统生成，不用手写"
    )
    lines.append(
        f"- 功能**怎么走一遍**（这个功能的正常路径、先后步骤：谁先做什么、做完是什么）"
        f" → {home}，写法：section_id + feature_slug + `field: main_flow`"
    )
    if steps is not None:
        lines.append(
            f"- {steps} 写的是**跨功能 / 端到端**的流程与异常路径，kind 必须写 steps；"
            f"功能**内部**的正常路径写功能字段 main_flow——同一段流程只写一处"
        )
    if decision is not None:
        lines.append(f"- {decision} 写关键决策与备选，kind 写 decision")
    for sid in adapter.section_ids:
        for slot in adapter.slots_of(sid):
            lines.append(
                f"- 写 {sid} 里的「{slot.title}」用章节槽：section_id={sid} + field={slot.name}"
                "（不要给 feature_slug）"
            )
    return "\n".join(lines)

#: 字段表里"**设计说必填、但代码没标必填**"的那几个：给一句**提示词压力**——它不是判据，
#: 只是让模型看得见"这一轮该写"。
#:
#: 为什么不直接把 `required` 改成 True：`readiness` 数的是**必填**字段，改它等于给每个缺这个字段的
#: 功能加一条**阻塞**（P30 里好不容易 0 阻塞的车道会立刻破）。P37 拍板**不升必填**。
#: 真跑证据（P37/P38）：分析者切出来的字段**正好是代码里那 6 个必填**，`main_flow` 在 29 个功能里
#: 只有 18 条；而主张里标了 `field: main_flow` 时整合者 **16/18 = 89%** 会写出主流程，没标只有 23%。
FIELD_PRESSING = {
    "main_flow": "每个功能都要写：这个功能怎么走一遍（1. …；2. …；3. …）",
}

# 功能字段表：从 `authoring/schema.py` 生成（结构的唯一真相源），不许手抄。
FEATURE_FIELDS_TEXT = "\n".join(
    f"- {spec.name}（{spec.title}，{spec.kind.value}"
    f"{'，可多条' if spec.multiple else ''}{'，必填' if spec.required else ''}）"
    + (f"——{FIELD_PRESSING[spec.name]}" if spec.name in FIELD_PRESSING else "")
    for spec in FEATURE_FIELDS
)


def render_seat_catalog(seats: Sequence[SeatDefinition]) -> str:
    """席位目录：给分析者挑席位用。一行一个，说清它管什么、什么时候该请它。

    目录是"有哪些席位"的清单，**不是**"这一轮请谁"的答案——那件事由读到输入的那次
    模型调用决定（`02-编排与数据.md` §4.6）。
    """

    lines: list[str] = []
    for seat in seats:
        parts = [f"- {seat.seat_id} · {seat.title}", f"管：{seat.focus}"]
        if seat.always_on:
            parts.append("常驻（每轮都值得考虑，但不是必须选）")
        if seat.trigger_keywords:
            parts.append("说到这些词通常要请它：" + " / ".join(seat.trigger_keywords))
        lines.append(" · ".join(parts))
    return "\n".join(lines)


def _entry_line(entry: Entry) -> str:
    """一条目的渲染：`条目 <id>：正文（E…）`。

    **条目 id 不许用方括号**：方括号在本项目里是出处编号 `[E12]` 的专用格式
    （`render_materials` / `render_claims` 都用它）。两种身份撞格式的后果真跑踩过——
    `sim_review` 的提示词原写着"文档里 `[条目编号]` 那种，抄原样"，模型照办，
    把方括号抄进 `entry_id`，机械筛一律判"指向不存在的条目"（全文与窄化下同样发生）。
    换成自描述前缀后没有歧义，也不用再靠"别带括号"这种不可靠的自然语言提醒。
    """

    refs = " ".join(str(eid) for eid in entry.refs)
    return f"条目 {entry.entry_id}：{entry.text}" + (f"（{refs}）" if refs else "")


def _kept_block_lines(
    block: BlockView, keep_ids: frozenset[str], *, indent: str = "  "
) -> list[str]:
    """`_block_lines` 的窄化版：只渲染留下的条目，块与子块的抬头照旧。

    抬头必须留——模型靠它知道这条属于哪个功能/字段；去掉抬头等于把上下文拆成
    互不相干的碎片。**子块也要递归**：真库 s5 的功能块下挂着 `field=name` 这类
    子块，条目住在子块里；拍平会丢掉 `- name：`，模型就分不清这是 name 还是
    acceptance（2026-10-13 审核抓到）。
    """

    kept_here = [e for e in block.entries if str(e.entry_id) in keep_ids]
    kept_children = [
        child
        for child in block.children
        if any(str(e.entry_id) in keep_ids for e in _entries_of(child))
    ]
    if not kept_here and not kept_children:
        return []
    lines: list[str] = []
    if block.kind is BlockKind.FEATURE:
        lines.append(f"{indent}- feature#{block.feature_slug}")
    elif block.field is not None:
        lines.append(f"{indent}- {block.field}：")
    for entry in kept_here:
        lines.append(f"{indent}  · {_entry_line(entry)}")
    for child in kept_children:
        lines.extend(_kept_block_lines(child, keep_ids, indent=indent + "  "))
    return lines


def _block_lines(block: BlockView, *, indent: str = "  ") -> list[str]:
    lines: list[str] = []
    if block.kind is BlockKind.FEATURE:
        lines.append(f"{indent}- feature#{block.feature_slug}")
    elif block.field is not None:
        lines.append(f"{indent}- {block.field}：")
    for entry in block.entries:
        lines.append(f"{indent}  · {_entry_line(entry)}")
    for child in block.children:
        lines.extend(_block_lines(child, indent=indent + "  "))
    return lines


#: 窄化渲染的开场一句：明说这是切片。否则模型会对自己没看到的部分下判断——
#: 本项目一贯纪律是"不许假装完整"（与 `02` §11.0.53 同一条：不留空节、照实说略去了什么）。
NARROWED_NOTE = "（以下是这份文档的走查切片，不是全文：只有点名的功能展开了）"
#: 条目窄化（讨论区按主题）的开场一句。**与上一条分开写**：说"功能展开了"会让
#: 模型拿功能找对应，而这里收窄的维度是条目——措辞错就等于给了它一个错的预期。
NARROWED_ENTRIES_NOTE = "（以下是这份文档的走查切片，不是全文：只有相关条目展开了）"
#: 摘要形态的开场一句：明说每条目只留了一句。否则模型会以为自己看到了全文。
DIGEST_NOTE = "（以下是文档摘要：十三章骨架齐全，每条目只留一句，不是逐字全文）"


#: 摘要形态下每条目最多留几个字。**60 是量出来的**：真实文档条目中位长 42–74 字、
#: P90 108–145，截到 60 能让中位条目完整保留，总量 −59~69%（见 `07 §7.12`）。
DIGEST_ENTRY_CHARS = 60


def render_document(
    view: DocumentView,
    *,
    feature_slugs: frozenset[str] | None = None,
    keep_entry_ids: frozenset[str] | None = None,
) -> str:
    """文档现状 → 文本。**默认全文；给了 `feature_slugs` 就只渲染这些功能的块。**

    为什么值得有这个口子（`07 §7.12` 第 2 项）：`sim_review`（M11）点名要走查
    `feature#slug`，却一直拿全文十三章——而它自己的机械筛（`sim_review.py`）划定合法
    引用**只在目标功能内**（"指到了别的功能的内容"是 issue，`acceptance[slug]` 必须
    全覆盖且只许这些）。所以窄化不省证据，只省干扰：判据①「删优先于排」。

    三条不许破的规矩：
    1. **章节不许消失**——没有点名功能的节写"（本节没有点名的功能）"，再说清略去了几条；
    2. **偏好区照旧**——那是用户显式偏好，与功能无关，窄化不许静默丢它；
    3. **不传参就是全文**——另外 6 个调用方（seat / integration / inquiry /
       discussion-say / discussion-cards / sim_inquiry）的输出必须逐字不变，有用例钉着。

    `keep_entry_ids` 与 `feature_slugs` 是**同一套窄化骨架的另一个过滤维度**：
    前者按「块属于哪些功能」筛，后者按「留下哪些条目」筛。讨论区用它按线程主题收窄
    （`02` §11.0.61）。**两个参数都不给就是全文**；`feature_slugs` 优先。
    """

    narrowed = feature_slugs is not None or keep_entry_ids is not None
    lines: list[str] = []
    if narrowed:
        lines.append(
            NARROWED_NOTE if feature_slugs is not None else NARROWED_ENTRIES_NOTE
        )
    lines.append(f"# {view.title}（v{view.version_no}）")
    for section in view.sections:
        lines.append(f"## {section.section_id} {section.title}")
        body: list[str] = []
        skipped = 0
        for block in section.blocks:
            if feature_slugs is not None and block.feature_slug not in feature_slugs:
                skipped += len(block.entries)
                continue
            if keep_entry_ids is not None:
                # skipped 按**子树**算：`block.entries` 只有直属条目，子块里的不算在内，
                # 直接减会算错（真库 s5 功能块的条目全在子块里）。
                total = len(list(_entries_of(block)))
                kept_lines = _kept_block_lines(block, keep_entry_ids)
                kept_count = sum(
                    1 for e in _entries_of(block) if str(e.entry_id) in keep_entry_ids
                )
                skipped += total - kept_count
                if not kept_lines:
                    continue
                body.extend(kept_lines)
                continue
            body.extend(_block_lines(block))
        if not body:
            # 空节的说明**按窄化维度分开写**（2026-10-10）：feature 窄化下说"没有点名的功能"
            # 是对的；条目窄化（讨论区按主题）下这一节只是**没展开**，说成"没有功能"会让
            # 模型以为它不存在。两句话都会进提示词，措辞错就会误导。
            lines.append(
                "（本节没有点名的功能）"
                if feature_slugs is not None
                else ("（本节本次未展开）" if narrowed else "（空）")
            )
        else:
            lines.extend(body)
        if skipped:
            # "与功能无关"只在 feature 窄化下成立；条目窄化下这些条目只是**没被选中**，
            # 说它们"无关"会让模型更敢断言"文档里没有"（真跑第 2 轮踩到）。
            reason = "与功能无关的条目" if feature_slugs is not None else ""
            lines.append(f"  ·（另见本节 {skipped} 条{reason}，本次未展开）")
    if view.preference_entries:
        lines.append("## 偏好区（不进正文 / 不进交付）")
        lines.extend(f"- {_entry_line(entry)}" for entry in view.preference_entries)
    return "\n".join(lines)


READINESS_HARD_RULE = "补缺口不等于填满——拿不准就出题，不许为凑判据编内容。"

#: 记忆注入上限：**20 行**（P10 的现场：20 行提示词已经超过 20000 字符，见 P28 §8·4）
MEMORY_LINE_LIMIT = 20

#: 没有记忆时的那一句实话。**不留空节**——空节会让模型以为"这里本来就没东西"。
NO_MEMORY_TEXT = "（这个项目还没有确认过的记忆）"

#: 记忆节的硬规矩：文档与记忆冲突时以文档为准（`02` §11.0.44）
MEMORY_HARD_RULE = (
    "记忆里已经定过的不许换个说法再问；记忆与正文冲突时**以正文为准**，"
    "并在 rationale 里说一句。**括号里的 `E…` 才是出处编号**——记忆条目 id、"
    "条目 id（`r-…#01`）、提案 id 都不许写进 `evidence_refs`。"
)


def render_memory(entries: Sequence[MemoryEntry]) -> str:
    """把**已确认**的记忆渲成提示词里的一节（席位与整合者共用这一处实现）。

    限量 20 行：超了要**写明**"只带了前 N 条"——静默截断会让模型以为剩下那些不存在，
    而"看不见的规则"比"看得见的规则"更危险。

    **不渲记忆条目的 id**（M6f）：真跑（L2 第 2 轮）里席位把行首那串 `mem…` 当成了出处编号，
    写进 `evidence_refs` → 整趟席位判失败。菜单里放什么，模型就点什么：id 不该出现在它面前。
    需要 id 的是**记忆整理者**（它要写 `target_id`），那份走 `render_memory_state`。
    """

    if not entries:
        return NO_MEMORY_TEXT
    shown = list(entries[:MEMORY_LINE_LIMIT])
    lines = [
        f"- 〔{kind_title(item.kind)}〕{item.text}"
        f"（出处：{'、'.join(str(ref) for ref in item.refs)}）"
        for item in shown
    ]
    if len(entries) > len(shown):
        lines.append(f"（共 {len(entries)} 条，这里只带了前 {len(shown)} 条）")
    lines.append(MEMORY_HARD_RULE)
    return "\n".join(lines)


def render_memory_state(entries: Sequence[MemoryEntry]) -> str:
    """给**记忆整理者**看的那一份：带条目 id（`update` / `merge` / `delete` 要写 `target_id`）。

    与 `render_memory` 分开的原因只有一个：**谁能看见 id，谁就可能把它当出处**（M6f 真跑现场）。
    这一份额外写明"这些 id 只能填进 target_id"。
    """

    if not entries:
        return NO_MEMORY_TEXT
    shown = list(entries[:MEMORY_LINE_LIMIT])
    lines = [
        f"- {item.memory_id} · 〔{kind_title(item.kind)}〕{item.text}"
        f"（出处：{'、'.join(str(ref) for ref in item.refs)}）"
        for item in shown
    ]
    if len(entries) > len(shown):
        lines.append(f"（共 {len(entries)} 条，这里只带了前 {len(shown)} 条）")
    lines.append(
        "上面每行开头的 id 是**记忆条目 id**：只用来填 `target_id`（改 / 合并 / 删的那一条），"
        "**不许**写进 `refs`；`refs` 只收 `E` 开头的出处编号。"
    )
    return "\n".join(lines)


def render_round_digest(
    round_: Round, *, ledger: EvidenceLedger | None = None
) -> str:
    """这一轮发生了什么——给记忆整理者看的**只读摘要**（它不是第二真相源，只是渲染）。

    只放"判断记忆值不值得记"用得上的一圈：用户原话、主张、问答、这一轮的提案文本、
    以及没人交代的那些。**不放整份文档**：那是整合者的事，放进来只会把这一趟拖慢。
    """

    lines = [f"用户原话：{round_.user_input}"]
    claims = render_claims(round_.claims)
    if claims:
        lines += ["", "本轮主张：", claims]
    answers = render_answers(round_, ledger=ledger)
    if answers:
        lines += ["", "问与答：", answers]
    if round_.draft is not None:
        lines += ["", "这一轮出的草案（正文句子）："]
        lines += [f"- {item.text}" for item in round_.draft.proposals if item.text.strip()]
        if round_.draft.gaps:
            lines += ["", "草案上标出的缺口："]
            lines += [f"- {item}" for item in round_.draft.gaps]
    if round_.dropped:
        lines += ["", "没被交代 / 被丢掉的："]
        lines += [f"- {item.handle}：{item.reason}" for item in round_.dropped]
    return "\n".join(lines)


def render_readiness(items: Sequence[ReadinessItem]) -> str:
    """把就绪度清单渲染成提示词里的一段。没缺口就返回空串（别给模型一段废话）。"""

    if not items:
        return ""
    lines = ["## 本章还差什么（系统按 01-规格 §4 的合格标准算的）", READINESS_HARD_RULE]
    lines += [
        f"- {item.section_id} {item.title}：{item.detail}"
        f"〔{'阻塞' if item.severity is Severity.BLOCKER else '提示'}〕"
        for item in items
    ]
    return "\n".join(lines)


def render_document_state(
    view: DocumentView,
    *,
    readiness: Sequence[ReadinessItem] = (),
    feature_slugs: frozenset[str] | None = None,
    keep_entry_ids: frozenset[str] | None = None,
) -> str:
    """智能体看到的"文档现状" = 文档渲染 + 缺口清单。

    缺口必须进提示词，否则就绪度只是个界面上的仪表盘，不改变 AI 行为
    （`01-规格.md` §7.2）——**窄化时也一样**：走查切片要知道自己缺什么。

    `feature_slugs` / `keep_entry_ids` 原样透传给 `render_document`
    （见那边的三条规矩）。都不传 = 全文，这是另外 6 个调用方的既有行为。
    要"条目摘要"那种形态用 `render_document_topics`（claims 层走的是那条路，不走这里）。
    """

    gaps = render_readiness(readiness)
    text = render_document(
        view, feature_slugs=feature_slugs, keep_entry_ids=keep_entry_ids
    )
    return text if not gaps else f"{text}\n\n{gaps}"


def render_contradictions(round_: Round) -> str:
    """给整合者看的"被判成立场互斥的主张对子"。

    用**主张原文**而不是 `c1/c2`：模型才知道这两句在打架（编号是给机器对账的）。
    对子指向的主张找不到就跳过——渲染层不替领域层判错。
    """

    by_id = {claim.claim_id: claim for claim in round_.claims}
    lines: list[str] = []
    for item in round_.contradictions:
        left = by_id.get(item.claim_id_a)
        right = by_id.get(item.claim_id_b)
        if left is None or right is None:
            continue
        lines.append(
            f"- 「{left.text}」 ↔ 「{right.text}」：{item.why}"
            "（两侧立场互斥：要出一道**让用户拍板**的题，`kind` 写 conflict）"
        )
    return "\n".join(lines) or "（这一轮没有判出互斥的主张）"


def render_seat_conflicts(
    conflicts: tuple[SeatConflict, ...],
    *,
    ledger: EvidenceLedger | None = None,
    round_id: RoundId,
) -> str:
    """给整合者看的"席位间冲突"。

    两条判断都带上**出处编号**：整合者照着引，机械盖章才盖得上（与 `contradiction_pairs`
    的盖章先例配套）。没有账本时不假装有编号（与 `render_reviews` 同口径）。
    """

    lines: list[str] = []
    for c in conflicts:
        left = _conflict_eid_note(c.seat_a, c.handle_a, ledger=ledger, round_id=round_id)
        right = _conflict_eid_note(c.seat_b, c.handle_b, ledger=ledger, round_id=round_id)
        lines.append(
            f"- [{c.seat_a}]「{c.claim_a}」{left}"
            f" ↔ [{c.seat_b}]「{c.claim_b}」{right}"
            f"（两个席位对「{c.about}」持相反判断："
            "要出一道**让用户拍板**的题，`kind` 写 conflict）"
        )
    return "\n".join(lines) or "（这一轮没有检测出席位间冲突）"


def _conflict_eid_note(
    seat_id: str,
    handle: str,
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
) -> str:
    """一条判断的出处编号注：没账本 ⇒ 空；有账本但查不到 ⇒ 明确写"没有"。"""

    if ledger is None:
        return ""
    evidence = ledger.find_by_local(seat_id=seat_id, local_id=handle, round_id=round_id)
    eid = None if evidence is None else evidence.eid
    return f"（出处编号 {eid}）" if eid is not None else "（没有出处编号）"


def render_claims(claims: Sequence[Claim]) -> str:
    lines: list[str] = []
    for claim in claims:
        eid = str(claim.eid) if claim.eid is not None else "（无编号）"
        lines.append(
            f"- [{eid}] {LABEL_TITLES[claim.label]}（{claim.label.value}）"
            f" · {claim.target.label()} · {claim.text}"
        )
        lines.append(f"  原话：「{claim.raw_span}」")
        if claim.assumption is not None:
            lines.append(
                f"  假设：失败后果={claim.assumption.failure_consequence}；"
                f"验证={claim.assumption.verification}"
            )
        if claim.decided_by:
            lines.append(f"  决策者：{claim.decided_by}")
    return "\n".join(lines) or "（没有主张）"


def seat_entry_handle(seat_id: str, local_id: str) -> str:
    """席位条目的身份：`seat.xxx:O1`。

    它**不是**出处编号——只是"哪个席位的第几条意见"的地址。两套写法分开命名，
    是因为真模型会把看着像编号的东西当编号引用（`04-未决.md` C9）：
    同一份提示词里既有 `E4` 又有 `[O1]`，模型就把 O1 写进了必须放 E 编号的字段。
    """

    return f"{seat_id}:{local_id}"


def _own_eid(
    review: SeatReview,
    local_id: str,
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
) -> EvidenceId | None:
    """这条意见自己的出处编号（只有"判断"有；"疑问"不进账本，所以没有）。

    **必须带轮次**：趟数每轮从 1 重来，只按 (席位, 条目) 反查会把**上一轮的编号**显示给
    整合者——它照着引，等于把溯源链引到另一条判断上（真跑抓到过，P22 回归）。
    """

    if ledger is None:
        return None
    evidence = ledger.find_by_local(
        seat_id=review.seat_id,
        local_id=f"p{review.pass_index}:{local_id}",
        round_id=round_id,
    )
    return None if evidence is None else evidence.eid


def render_reviews(
    reviews: Sequence[SeatReview],
    *,
    titles: Mapping[str, str],
    ledger: EvidenceLedger | None = None,
    round_id: RoundId,
) -> str:
    """席位意见的渲染。**两套身份必须各叫各的名字**：

    - 席位条目 `seat.xxx:O2`：这条意见在席位里叫啥（不是编号，不许写进 evidence_refs）；
    - 出处编号 `E7`：这条判断自己进账本后拿到的编号（只有判断有，疑问没有）。
    """

    blocks: list[str] = []
    for review in reviews:
        title = titles.get(review.seat_id, review.seat_id)
        lines = [f"### {review.seat_id}（{title}）", f"依据：{review.basis}"]
        if not review.opinions:
            lines.append("- （没有需要补充的意见）")
        for opinion in review.opinions:
            lines.extend(_opinion_lines(review, opinion, ledger=ledger, round_id=round_id))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "（没有席位意见）"


def _opinion_identity(
    review: SeatReview,
    opinion: Opinion,
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
) -> str:
    """一条意见的双身份标注（`_opinion_lines` 与 `render_reviews_compact` 共用同一份）。"""

    handle = seat_entry_handle(review.seat_id, opinion.local_id)
    own_eid = _own_eid(review, opinion.local_id, ledger=ledger, round_id=round_id)
    if own_eid is not None:
        return f"（席位条目 {handle} · 出处编号 {own_eid}）"
    if ledger is not None:
        return f"（席位条目 {handle} · 没有出处编号）"
    return f"（席位条目 {handle}）"


def _opinion_lines(
    review: SeatReview,
    opinion: Opinion,
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
) -> list[str]:
    """一条意见的渲染（`render_reviews` 与 `render_selected_opinions` 共用同一份）。"""

    form = "疑问" if opinion.form is OpinionForm.QUESTION else "判断"
    identity = _opinion_identity(review, opinion, ledger=ledger, round_id=round_id)
    refs = " ".join(str(eid) for eid in opinion.evidence_refs)
    return [
        f"- [{form}]{identity}关于{opinion.about}",
        f"  结论：{opinion.claim}",
        f"  理由：{opinion.because}",
        f"  依据：{refs or opinion.basis_note or '（无依据）'}",
    ]


def render_reviews_compact(
    reviews: Sequence[SeatReview],
    *,
    ledger: EvidenceLedger | None = None,
    round_id: RoundId,
) -> str:
    """席位意见的**紧凑形态**：一条一行，保留双身份（能引用），丢掉理由/依据。

    席位把它当"背景"（`seat_review.md`），一行足够"别重复、能引用"。
    """

    lines: list[str] = []
    for review in reviews:
        for opinion in review.opinions:
            form = "疑问" if opinion.form is OpinionForm.QUESTION else "判断"
            identity = _opinion_identity(review, opinion, ledger=ledger, round_id=round_id)
            lines.append(f"- [{form}]{identity}关于{opinion.about}：{opinion.claim}")
    return "\n".join(lines) or "（没有席位意见）"


def render_prior_opinions(
    reviews: Sequence[SeatReview],
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
    seat_id: str,
) -> str:
    """席位轮里"之前的意见"：**本席位自己的逐字，其它席位压成一行点名**。

    这是 `07 §7.12` 第 1 项（指针式历史）。为什么必须分流——量出来的事实：

    - 席位提示词 60.3% 是 `opinions_so_far`，而它真正要审的 `claims` 只占 2.2%**；
    - §7.7 的消费遥测：11,029 条席位意见里只有 **42 条（0.4%）**被别的席位引用，
      且全部是"更早趟"。
    - 后果不只是胖：`opinions_so_far` 每轮全量重发且随历史线性增长，10 轮的老项目
      席位轮 **90 秒 ×3 全超时**。

    所以按 §7.12 原则③「指针换内容，不摘要换内容」处理成两半：

    1. **本席位自己**的历史结论**逐字**给（一行一条，带双身份便于引用）——
       它上一轮判过什么，直接决定这一轮该说什么，这是承重信息，不压；
    2. **其它席位**压成一行点名：坐过什么位、各自看过几个题。保留"这块已经有人看过"
       的信号（避免重复提问），丢掉无人消费的正文与理由。

    `render_reviews_compact` 保留不动（一行一条的全量形态），供别处需要全量时用。
    """

    mine = [review for review in reviews if review.seat_id == seat_id]
    others = [review for review in reviews if review.seat_id != seat_id]
    if not mine and not others:
        return "（没有席位意见）"

    parts: list[str] = []
    if mine:
        parts.append(render_reviews_compact(mine, ledger=ledger, round_id=round_id))
    if others:
        roster = "、".join(
            f"{review.seat_id}（{len(review.opinions)} 条）" for review in others
        )
        parts.append(f"（另有席位给过意见，原话不重复：{roster}）")
    return "\n".join(parts)


def render_selected_opinions(
    reviews: Sequence[SeatReview],
    handles: Collection[str],
    *,
    ledger: EvidenceLedger | None = None,
    round_id: RoundId,
) -> str:
    """**只**渲染点名的那几条席位意见（归因补交代那条小调用的输入，`02` §11.0.38）。

    与 `render_reviews` 共用同一份单条渲染——两处形状必须一模一样，
    不然模型看到的"这条意见"和系统核算的"这条意见"就对不上了。
    """

    wanted = set(handles)
    lines: list[str] = []
    for review in reviews:
        for opinion in review.opinions:
            if seat_entry_handle(review.seat_id, opinion.local_id) not in wanted:
                continue
            lines.extend(_opinion_lines(review, opinion, ledger=ledger, round_id=round_id))
    return "\n".join(lines) or "（没有要点名的席位意见）"


def render_citations(
    *,
    accounted_handles: Collection[str],
    questions: Sequence[Question],
    proposals: Sequence[Proposal],
) -> str:
    """这一趟**核算真正读过的东西**：题目归因过的席位条目地址 + 题/提案引用过的编号。

    为什么只给这两样：`assembly.unaccounted_drops` 的三条边界里，① 看的是
    `accounted_handles`、② 看的是题与提案的引用编号——**判据读什么，提示词就只给什么**。
    给多了（比如把文档正文塞进去），模型就会拿"我写过类似的话"当"我用过了"。
    """

    refs: list[str] = []
    for question in questions:
        refs.extend(str(eid) for eid in question.evidence_refs)
    for proposal in proposals:
        refs.extend(str(eid) for eid in proposal.origin.refs)
    seen: dict[str, None] = dict.fromkeys(refs)
    handles = sorted(accounted_handles)
    return "\n".join(
        [
            "### 这一趟题目归因过的席位条目（归因对上了的）",
            "、".join(handles) or "（没有）",
            "### 这一趟题目与提案引用过的出处编号",
            "、".join(seen) or "（没有）",
        ]
    )


def render_answers(round_: Round, *, ledger: EvidenceLedger | None = None) -> str:
    """已答 / 跳过两段，用 `--` 分隔（整合者要分别用它们）。

    **每条答案要带上它自己的出处编号**（形如 E8）：答案进账本时就拿到了编号，
    席位想引用"用户答过的那句话"必须引 E 编号。只给题号（q1）会诱导模型把题号
    当编号引用——真模型冒烟实测过一次，整轮因此失败。

    **答案的身份是"轮次 + 题号"**（2026-09-29 拍板 D4）：题面与跳过后果回到**出题那一批**去找
    （`questionnaire_history`），编号按 `(轮次, 题号)` 认。原来两样都按裸题号在**当前批**里找，
    第二批发到同一个号就挂错题面、引错编号（真跑里两行答案同一个题面、同一个 E 编号）。
    找不到（老库、题号重排过的历史行）就只写题号：**宁可不显示，也不显示错的**。
    """

    by_key = {
        (str(form.form_id), str(question.question_id)): question
        for form in round_.questionnaire_history
        for question in form.questions
    }
    if not by_key:
        return "--\n（本轮没问过问题）"
    answered_lines: list[str] = []
    skipped_lines: list[str] = []
    for answer_set in round_.answer_sets:
        for answer in answer_set.answers:
            question = by_key.get((str(answer_set.form_id), str(answer.question_id)))
            title = question.title if question else str(answer.question_id)
            chosen = "、".join(answer.selected)
            free = answer.text.strip()
            detail = " / ".join(part for part in (chosen, free) if part)
            eid = _answer_eid(
                ledger, round_id=round_.round_id, question_id=answer.question_id
            )
            answered_lines.append(
                f"- {answer.question_id} {title} → {detail or '（空答）'}"
                + (f"〔{eid}〕" if eid else "")
            )
        for question_id in answer_set.skipped:
            question = by_key.get((str(answer_set.form_id), str(question_id)))
            if question is None:
                skipped_lines.append(f"- {question_id}（原题已不在）")
                continue
            skipped_lines.append(
                f"- {question_id} {question.title}"
                f"（跳过后果：{question.consequence_if_skipped}）"
            )
    answered = "\n".join(answered_lines) or "（这一轮还没有答过题）"
    if answered_lines:
        answered += "\n（引用用户的回答请用它的 E 编号，题号 q1 不是出处编号）"
    skipped = "\n".join(skipped_lines) or "（没有跳过的题）"
    return f"{answered}\n--\n{skipped}"


#: 那一段为空时的固定文案（与 `NO_MEMORY_TEXT` / `NO_MATERIAL_TEXT` 同一约定：
#: **不许留空段**——空段会让模型以为那里还有内容没显示）。
NO_PENDING_QUESTION_TEXT = "（这一轮问过的题都答了或跳过了）"


def render_asked_questions(round_: Round) -> str:
    """**问过、但既没答也没跳过**的题——模型自己看不见它们，只能靠这一段。

    为什么必须有：装配层 `assembly.py` 的 `seen_questions` 种子认这三类题
    （carried / 本轮历史问卷 / 系统自造），命中的新题被判重丢掉；而渲染只认
    `answer_sets`——**没有答案集时（用户直接点确认，这是正常主路径：
    整合那一步明说「答案要么被引用、要么机械落地」）题面一个字都不进提示词**。

    真跑实测（2026-10-09，10 个 eval轮次 14 条留痕）：**14/14 条题面与上一批完全相同**，
    不是措辞近似——模型根本没看见过，于是逐字重写、撞判重被丢。

    **单独一个槽，不塞进 `render_answers`**：调用侧（`use_cases.py`）用
    `.partition("--")` 把它的输出切成 `answered` / `skipped` 两段，加第三段会被
    切错位置、把没答的题显示在「用户跳过」标题下。

    身份按 `(form_id, question_id)` 认：题号跨批会重复（D4 之前的老库），
    用裸题号会把上一批的题面挂到这一批的题号上。
    """

    pending: list[str] = []
    for form in round_.questionnaire_history:
        for question in form.questions:
            if (str(form.form_id), str(question.question_id)) in round_.touched_question_keys:
                continue
            pending.append(f"- {question.question_id} {question.title}")
    if not pending:
        return NO_PENDING_QUESTION_TEXT
    return (
        "下面这些**已经问过了、用户还没答也没跳过**——**别再出一遍**："
        "重复的那条会被系统悄悄丢掉，你不会收到任何提示\n" + "\n".join(pending)
    )


def answer_eids(*, ledger: EvidenceLedger, round_id: RoundId) -> dict[str, str]:
    """题号 → 这条答案的出处编号（给席位做 `q3 → E8` 的引用翻译）。

    **只收本轮**：老实现收整个项目账本，第二轮席位写 `q1` 会被翻到第一轮那道题的答案上
    （与 `render_answers` 同一个根因，`02` §11.0.11）。同一个题号撞上两条答案（D4 之前的老库）
    就**不收录**——宁可翻译不出来（照原样判"编号不存在"），也不给一个错的编号。
    """

    mapping: dict[str, str] = {}
    seen: dict[str, int] = {}
    for evidence in ledger.evidences():
        if evidence.kind is not EvidenceKind.CARD_ANSWER:
            continue
        question_id = evidence.source.question_id
        if question_id is None or evidence.source.round_id != round_id:
            continue
        key = str(question_id)
        seen[key] = seen.get(key, 0) + 1
        mapping.setdefault(key, str(evidence.eid))
    return {key: eid for key, eid in mapping.items() if seen[key] == 1}


def _answer_eid(
    ledger: EvidenceLedger | None, *, round_id: RoundId, question_id: object
) -> str | None:
    """这条答案的出处编号；**认不出就不给**（同一轮里同一个题号有两条 = 老库重排过）。"""

    if ledger is None:
        return None
    found = [
        evidence
        for evidence in ledger.evidences()
        if evidence.kind is EvidenceKind.CARD_ANSWER
        and evidence.source.round_id == round_id
        and evidence.source.question_id == question_id
    ]
    return str(found[0].eid) if len(found) == 1 else None


def render_existing_entries(document: Document) -> str:
    """给整合者看的既有条目清单：要改 / 要删旧内容时得用它给的 entry id。"""

    lines: list[str] = []
    for block in document.blocks:
        for entry in block.entries:
            lines.append(f"- 条目 {entry.entry_id}：{entry.text}")
    for entry in document.preference_entries:
        lines.append(f"- 条目 {entry.entry_id}：（偏好区）{entry.text}")
    return "\n".join(lines) or "（文档现在没有任何条目）"


def render_document_topics(
    view: DocumentView, *, readiness: Sequence[ReadinessItem] = ()
) -> str:
    """文档的**条目摘要**：十三章骨架 + 每条目一句，**不带条目 id、不带出处编号**。

    给分析者用（`07 §7.12` 第 2 项·claims 层）。为什么单独一个函数而不是给
    `render_document` 加参数：那会变成一个只有一处消费的旋钮，而这里要的是**另一种东西**——
    一篇能扫的目录，不是缩水的全文。

    量出来的依据：`ClaimDraft` 没有任何文档条目引用（只有 `material_eids`），矛盾判据是
    **轮内**的（`claim_a`/`claim_b` 指向它自己产出的 claims 数组），而 `document_state`
    对它唯一的实际用途——web_queries 阶梯第一层"文档里已有的就别提"——在 18 轮真跑里
    **一共只发生过 1 次**（用户勾选 0 条）。它每轮都要的"文档现状概貌"已由 `$project_context`
    （覆盖 / 功能 / 决策 / 开放问题）给全。真库实测：全文 5472–6249 字 → 摘要 2649–3227 字，
    **−48~55%**，条目一条不漏。

    两条不许破的规矩：
    1. **章节不许消失**——空节写"（空）"，不留空节（空节会让模型以为这一章不存在）；
    2. **不给 id 与编号**——`ClaimDraft` 引不了文档条目，给了只会诱导它去引不存在的编号
       （本项目方括号归属出处编号 `E…`，见 `3d0d54b`）。
    """

    lines: list[str] = [DIGEST_NOTE, f"# {view.title}（v{view.version_no}）"]
    for section in view.sections:
        lines.append(f"## {section.section_id} {section.title}")
        body = [
            f"  · {_cap(entry.text, DIGEST_ENTRY_CHARS)}"
            for block in section.blocks
            for entry in _entries_of(block)
        ]
        lines.extend(body or ["（空）"])
    gaps = render_readiness(readiness)
    return f"{"\n".join(lines)}\n\n{gaps}" if gaps else "\n".join(lines)


def _entries_of(block: BlockView) -> Iterator[Entry]:
    """一个块子树里的全部条目（深度优先，按文档顺序）。"""

    yield from block.entries
    for child in block.children:
        yield from _entries_of(child)


def _cap(text: str, limit: int) -> str:
    """摘要截断：超出就截并打省略号。短于上限的原样返回（真实条目中位长 42–74 字）。"""

    return text if len(text) <= limit else f"{text[:limit]}…"


# ── 外部材料：选块 + 渲染（工具层 C-2）──────────────────────────────────
#
# 三段式里的后两段：**选块**只有一个地方（`select_chunks`），**渲染**只有一个地方
# （`render_materials` / `render_web_results`）。中间那一段（发号）在用例里，
# 它必须拿 `select_chunks` 的结果去发——否则"编号 == 带进提示词的块"这条口径立刻漏。

#: 材料块合计的预算（字）。为什么是 4000：integration 提示词已经 ~20k 字符、实测最慢 201.2s，
#: 再加几千字符风险太大；先按这个量跑，量完再抬。
MATERIAL_BUDGET = 4000
#: 检索结果合计的预算（字）。Tavily 实测单条 content ≈ 1.1k 字符，所以每条还要再截。
WEB_BUDGET = 2000
#: 单条检索结果截到多少字（截了就在那段末尾说明）
WEB_SNIPPET_CHARS = 300
#: 每个块渲染时除了正文还会多一行「[E12] 第 3 页」——算预算时把它算进去，别超太多
CHUNK_OVERHEAD = 24
#: 联网结果渲染时每条还会多一个「[E123] 」编号位：选块的成本要把这个位子也算上，
#: 否则"选进来的最后一条"可能刚好在渲染时被挤掉（账本里就多一个模型看不到的编号）。
WEB_EID_RESERVE = 12
#: 没有材料时提示词里写这句（**不留空节**：空节会让模型以为"材料被省略了"）
NO_MATERIAL_TEXT = "（这一轮没有外部材料）"


def select_chunks(
    packets: Sequence[MaterialPacket],
    *,
    budget: int = MATERIAL_BUDGET,
    cost: Callable[[MaterialPacket, MaterialChunk], int] | None = None,
    query: str = "",
) -> tuple[tuple[str, MaterialChunk], ...]:
    """**唯一**决定"哪些块进这一轮"的地方。

    返回 `(material_id, chunk)`：调用方拿它去发号（`Round.materials`）与渲染。

    `cost` 默认是"正文 + 每个块那行编号的开销"；联网结果要传 `web_block_cost`——
    那段渲染每条还要写标题与 URL，成本不一样（**同一件事不许有两把尺子**）。

    `query` 给空就是老行为（按材料顺序、块顺序取到预算为止）——联网检索那条路**必须**
    走这一支：Tavily 返回的顺序本身就是相关序，我们自己的粗排只会把它搅乱。
    给了 query（本轮输入）才走粗排，见 `_select_ranked`。
    """

    weigh = cost or _material_block_cost
    if not query.strip():
        return _select_in_order(packets, budget=budget, weigh=weigh)
    return _select_ranked(packets, budget=budget, weigh=weigh, query=query)


def _select_in_order(
    packets: Sequence[MaterialPacket],
    *,
    budget: int,
    weigh: Callable[[MaterialPacket, MaterialChunk], int],
) -> tuple[tuple[str, MaterialChunk], ...]:
    picked: list[tuple[str, MaterialChunk]] = []
    used = 0
    for packet in packets:
        for chunk in packet.parsed.chunks:
            if used + weigh(packet, chunk) > budget:
                return tuple(picked)
            picked.append((packet.material_id, chunk))
            used += weigh(packet, chunk)
    return tuple(picked)


@dataclass(frozen=True, slots=True)
class _Candidate:
    """粗排里的一个候选块：块自己的身份 + 它在这场选块里的位次。"""

    score: int
    position: int  # 材料在用户勾选顺序里的位次
    packet: MaterialPacket
    chunk: MaterialChunk


def _select_ranked(
    packets: Sequence[MaterialPacket],
    *,
    budget: int,
    weigh: Callable[[MaterialPacket, MaterialChunk], int],
    query: str,
) -> tuple[tuple[str, MaterialChunk], ...]:
    """按相关粗排取块。两步：**每份材料至少露出它自己的第一名**，再按分数补满预算。

    为什么要有保底那一步：用户勾 N 份材料等于说了"这些都要参与"。没有保底，
    一份高分材料会吃光全部预算，其余被勾的材料整份缺席——那正是缺陷 ② 的用户体感
    （勾了材料、提示词说没材料），修完块宽不保底会以另一种形式重现。
    """

    candidates = [
        _Candidate(
            score=_block_score(chunk.text, query),
            position=position,
            packet=packet,
            chunk=chunk,
        )
        for position, packet in enumerate(packets)
        for chunk in packet.parsed.chunks
    ]
    # 分数降序；同分按材料顺序、块顺序——排序**完全确定**，同样的输入永远得到同样的选择
    order = sorted(candidates, key=lambda item: (-item.score, item.position, item.chunk.ordinal))

    picked: list[tuple[str, MaterialChunk]] = []
    taken: set[tuple[str, int]] = set()
    floored: set[str] = set()
    used = 0

    def take(candidate: _Candidate) -> bool:
        """装得下就装；装不下返回 False（保底那一步靠这个跳过，不是报错）。"""

        nonlocal used
        cost = weigh(candidate.packet, candidate.chunk)
        if used + cost > budget:
            return False
        picked.append((candidate.packet.material_id, candidate.chunk))
        taken.add((candidate.packet.material_id, candidate.chunk.ordinal))
        used += cost
        return True

    # 第一步：遍历已排序的候选，每份材料的第一名第一次出现时就收下它
    for candidate in order:
        if candidate.packet.material_id in floored:
            continue
        floored.add(candidate.packet.material_id)
        take(candidate)
    # 第二步：剩下的按分数补满预算
    for candidate in order:
        if (candidate.packet.material_id, candidate.chunk.ordinal) not in taken:
            take(candidate)
    return tuple(picked)


def _block_score(text: str, query: str) -> int:
    """块与本轮输入的字面重合度：**同一把尺子**——和 `SqliteLikeRetriever` 一样按字面算。

    为什么是 2-gram 而不是"整句包含"：query 是"导出权限该怎么定"这样一句话，
    要求整句命中等于永远不命中（今天就是退化成"顺序取"，预算给排最前面的材料）。
    2-gram 也不需要分词库：中文按字滑窗即可，`re` 都不用。

    这是**粗排，不是语义排序**。语义检索在这套系统里被证据否决（注入越多越可能掉分：
    真实分布下 >60% 的查询 top-10 至少有一条硬干扰项，检索器越强干扰越毒）——
    所以这里只做"把预算从排前面的挪到提到本轮用词的"。
    """

    return sum(text.count(gram) for gram in _query_grams(query))


def _query_grams(query: str) -> tuple[str, ...]:
    """query → 去重后的 2-gram（丢掉空白与标点；不足 2 字就一个都没有）。"""

    kept = "".join(char for char in query if char.isalnum())
    return tuple(dict.fromkeys(kept[index : index + 2] for index in range(len(kept) - 1)))


def _material_block_cost(packet: MaterialPacket, chunk: MaterialChunk) -> int:
    """上传材料那条路的成本：正文 + 一行「[E12] 第 3 页」。"""

    return len(chunk.text) + CHUNK_OVERHEAD


def web_block_cost(packet: MaterialPacket, chunk: MaterialChunk) -> int:
    """联网检索结果的成本：**与 `render_web_results` 的排版同源**（标题 + URL + 摘要 + 编号位）。

    为什么必须同源：选块先发生（决定发哪些号），渲染后发生。两把尺子稍有出入，
    就会出现"发了号却没进提示词"的编号——那正是账本被污染的样子。
    """

    snippet = chunk.text[:WEB_SNIPPET_CHARS]
    block = f"{packet.parsed.title}\n{packet.parsed.source_ref}\n{snippet}"
    return len(block) + WEB_EID_RESERVE


@dataclass(frozen=True, slots=True)
class RenderedMaterial:
    """渲染结果：文本 + 是否截断 + 用掉了哪几块（界面/日志要能说清）。"""

    text: str
    truncated: bool
    used_chunk_ids: tuple[str, ...]


def render_materials(
    entries: Sequence[tuple[MaterialPacket, Mapping[str, EvidenceId]]],
    *,
    budget: int = MATERIAL_BUDGET,
) -> RenderedMaterial:
    """材料 → 提示词里那一段。**只渲染有编号的块**（没编号的进不了提示词）。

    `entries` 里的块应当来自 `select_chunks`；若包的块数多于带编号的块数，
    就写「（材料较长，只带了 N 块 / 共 M 块）」，界面与提示词都不许假装完整。
    """

    if not entries:
        return RenderedMaterial(text=NO_MATERIAL_TEXT, truncated=False, used_chunk_ids=())

    lines: list[str] = []
    used_ids: list[str] = []
    truncated = False
    for packet, eids in entries:
        parsed = packet.parsed
        head = (
            f"【{ORIGIN_TITLES.get(parsed.origin, parsed.origin)}】{parsed.title}"
            f"（{parsed.media_type} · 由 {parsed.parser_id} 抽出）"
        )
        if parsed.origin != UPLOAD_ORIGIN:
            # 非上传来源（抓来的网页…）把**出处**写出来：模型该知道自己读的是哪一页，
            # 点编号时账本里的外部指称也是它。上传材料的 `attachment:标题` 与标题重复，
            # 不占这份预算。
            head += f"\n来源：{parsed.source_ref}"
        lines.append(head)
        # 按**文档序**渲染（`select_chunks` 只决定挑哪些块，不决定怎么摆）：
        # 编号也按文档序发，所以提示词里号码顺序走，用户对着材料卡找得到。
        for chunk in parsed.chunks:
            eid = eids.get(chunk.chunk_id)
            if eid is None:
                continue
            lines.append(f"[{eid}] {chunk.anchor}")
            lines.append(chunk.text)
            used_ids.append(chunk.chunk_id)
        for warning in parsed.warnings:
            lines.append(f"（{warning}）")
        kept = len(used_ids)
        total = len(parsed.chunks)
        if kept < total:
            truncated = True
            lines.append(f"（材料较长，只带了 {kept} 块 / 共 {total} 块）")
    lines.append(
        "（以上是外部材料，不是用户说的话；不要再把它说成用户说的话。"
        "要引就引方括号里的编号）"
    )
    return RenderedMaterial(
        text="\n".join(lines), truncated=truncated, used_chunk_ids=tuple(used_ids)
    )


ORIGIN_TITLES: dict[str, str] = {
    "upload": "上传的材料",
    "web_search": "联网检索结果",
    "web_fetch": "抓来的网页",
    #: 模拟调研（M10）：探索/讨论没加显示是旧账（回落原文）；模拟的第一天就必须写清，
    #: 否则"推演"与"外部事实"在提示词里长一个样——这是 D1 诚实口径的底线。
    "simulation": "模拟调研（模型推演，不是用户数据）",
}

#: 上传材料的 origin（只有它不写"来源"——`attachment:标题` 与标题重复）
UPLOAD_ORIGIN = "upload"
#: 按网址抓来的材料的 origin。与 `infrastructure/tools/fetch.py` 无关——origin 是
#: application 层的词汇（`ORIGIN_TITLES` 也在这儿），基础设施不该定义它。
FETCH_ORIGIN = "web_fetch"


def render_web_results(
    entries: Sequence[tuple[MaterialPacket, EvidenceId]],
    *,
    budget: int = WEB_BUDGET,
) -> RenderedMaterial:
    """检索结果 → 提示词里那一段：一条一段（编号 + 标题 + URL + 摘要）。

    超预算**照实说**（与材料那段同一个口径）：模型得知道"搜到的比这多"，
    不然它会以为自己看到的就是全部。正常路径上这个截断不会发生——
    选块用的是同一套成本（`web_block_cost`），有测试钉着。
    """

    if not entries:
        return RenderedMaterial(
            text="（这一轮没有联网检索结果）", truncated=False, used_chunk_ids=()
        )

    lines: list[str] = []
    used_ids: list[str] = []
    used = 0
    truncated = False
    for packet, eid in entries:
        parsed = packet.parsed
        chunk = parsed.chunks[0] if parsed.chunks else None
        snippet = "" if chunk is None else chunk.text[:WEB_SNIPPET_CHARS]
        block = f"[{eid}] {parsed.title}\n{parsed.source_ref}\n{snippet}"
        if used + len(block) > budget:
            truncated = True
            break
        lines.append(block)
        used += len(block)
        if chunk is not None:
            used_ids.append(chunk.chunk_id)
    if truncated:
        lines.append(f"（检索结果太多，只带了前 {len(used_ids)} 条 / 共 {len(entries)} 条）")
    lines.append(
        "（以上是联网检索结果，不是用户说的话；不要再把它说成用户说的话。"
        "要引就引方括号里的编号）"
    )
    return RenderedMaterial(
        text="\n".join(lines), truncated=truncated, used_chunk_ids=tuple(used_ids)
    )


# ── L1 项目上下文构建 ──────────────────────────────────────────────

MAX_DECISIONS = 10
#: L4 摘要一次最多带几轮（`render_digests` 的 limit 与仓储 `recent(limit=…)` 共用这个数）。
DIGEST_LIMIT = 3
MAX_OPEN_QUESTIONS = 10


def _section_entry_count(section) -> int:
    """一个章节里的条目总数（含子块）。"""

    def count_block(block) -> int:
        return len(block.entries) + sum(count_block(child) for child in block.children)

    return sum(count_block(b) for b in section.blocks)


def _compute_coverage(view, adapter: TemplateAdapter) -> tuple[tuple[str, str], ...]:
    """每个章节的覆盖状态: empty / partial / complete。

    下限走**模板**（声明值 / 槽数 / 角色缺省，见 `TemplateAdapter.coverage_minimum`），
    不再是本模块另抄一份 id-keyed 表——两份阈值迟早会漂。

    版本历史章例外：`coverage_minimum` 对它抛错（它永不参与判定），这里捕获取 1，
    于是空版本章照旧显示 `empty`——与今天逐字一致，不借迁移之机改语义。
    """

    result: list[tuple[str, str]] = []
    for section in view.sections:
        sid = str(section.section_id)
        count = _section_entry_count(section)
        try:
            minimum = adapter.coverage_minimum(sid)
        except BadTemplateDefinition:
            minimum = 1

        if count == 0:
            state = "empty"
        elif count < minimum:
            state = "partial"
        else:
            state = "complete"
        result.append((sid, state))

    return tuple(result)


def build_project_context(
    document_view,
    memory_entries: Sequence[MemoryEntry],
    readiness: tuple = (),
    *,
    adapter: TemplateAdapter,
) -> ProjectContext:
    """从文档现状 + 记忆 + 就绪度构建 L1 项目上下文（纯函数）。

    `adapter` 必填：功能与未决分别取模板的**功能锚点**与**开放问题锚点**——自定义模板里
    这两章的 id 可以是任何自由文本（`02` §11.0.69）。
    """

    sections_covered = _compute_coverage(document_view, adapter)

    features_defined: list[str] = []
    for section in document_view.sections:
        if str(section.section_id) == str(adapter.feature_home):
            for block in section.blocks:
                if block.feature_slug:
                    features_defined.append(str(block.feature_slug))

    top_decisions = tuple(
        e.text for e in memory_entries if e.kind == MemoryKind.DECISION
    )
    decisions_total = len(top_decisions)
    top_decisions = top_decisions[:MAX_DECISIONS]

    open_questions: list[str] = []
    for section in document_view.sections:
        if str(section.section_id) == str(adapter.open_home):
            for block in section.blocks:
                if block.kind == "open_questions":
                    open_questions.extend(block.entries)
    questions_total = len(open_questions)
    open_questions = tuple(open_questions[:MAX_OPEN_QUESTIONS])

    return ProjectContext(
        sections_covered=sections_covered,
        features_defined=tuple(features_defined),
        top_decisions=top_decisions,
        open_questions=open_questions,
        decisions_total=decisions_total,
        open_questions_total=questions_total,
    )


def render_project_context(ctx: ProjectContext) -> str:
    """渲染 L1 上下文为提示词文本（固定格式，≤30 行）。"""
    lines: list[str] = ["<project-context>"]

    parts: list[str] = []
    if ctx.domain:
        parts.append(f"项目: {ctx.domain}")
    if ctx.target_users:
        parts.append(f"用户: {ctx.target_users}")
    if ctx.core_value:
        parts.append(f"核心价值: {ctx.core_value}")
    if parts:
        lines.append(" | ".join(parts))

    if ctx.key_constraints:
        lines.append(f"约束: {' | '.join(ctx.key_constraints)}")

    if ctx.sections_covered:
        coverage_str = " ".join(f"{sid}({state})" for sid, state in ctx.sections_covered)
        lines.append(f"覆盖: {coverage_str}")

    if ctx.features_defined:
        lines.append(f"已确认功能: {', '.join(ctx.features_defined)}")

    if ctx.top_decisions:
        for d in ctx.top_decisions:
            lines.append(f"决策: {d}")
        omitted = ctx.decisions_total - len(ctx.top_decisions)
        if omitted > 0:
            lines.append(
                f"（共 {ctx.decisions_total} 条决策，只带了前 {len(ctx.top_decisions)} 条）"
            )

    if ctx.open_questions:
        lines.append(f"开放问题: {' | '.join(ctx.open_questions)}")
        omitted = ctx.open_questions_total - len(ctx.open_questions)
        if omitted > 0:
            lines.append(
                f"（共 {ctx.open_questions_total} 条开放问题，"
                f"只带了前 {len(ctx.open_questions)} 条）"
            )

    lines.append("</project-context>")
    return "\n".join(lines)


# ── L2 轮次观察提取 ───────────────────────────────────────────────


def extract_observations_from_reviews(
    reviews,
    round_id: str,
    project_id: str,
    ids,  # IdSource protocol
) -> tuple[RoundObservation, ...]:
    """从席位意见中提炼观察（纯函数）。

    question → GAP_IDENTIFIED（标题 = about, detail = claim + because）
    judgment → SEAT_INSIGHT（标题 = about, detail = claim + because）
    """
    from pmstudio.domain.round.opinion import OpinionForm
    observations: list[RoundObservation] = []

    for review in reviews:
        for opinion in review.opinions:
            if opinion.form == OpinionForm.QUESTION:
                obs_type = ObservationType.GAP_IDENTIFIED
            else:
                obs_type = ObservationType.SEAT_INSIGHT

            detail = f"{opinion.claim}"
            if opinion.because:
                detail += f"（依据：{opinion.because}）"

            obs = RoundObservation(
                obs_id=ids.new("obs"),
                round_id=round_id,
                project_id=project_id,
                obs_type=obs_type,
                title=opinion.about,
                detail=detail,
                source_ref=f"{review.seat_id}:{opinion.local_id}",
            )
            observations.append(obs)

    return tuple(observations)


# ── L2 轮次观察渲染 ────────────────────────────────────────────────


def render_observations_index(
    observations: Sequence[RoundObservation], limit: int = 5
) -> str:
    """L2 观察索引——只给标题（≤limit 条），模型按需拉详情。"""
    if not observations:
        return ""
    shown = observations[:limit]
    lines = ["<observations-index>"]
    for obs in shown:
        mark = "？" if obs.obs_type == "gap_identified" else "●"
        lines.append(f"  {mark} [{obs.source_ref}] {obs.title}")
    omitted = len(observations) - len(shown)
    if omitted > 0:
        lines.append(f"  （共 {len(observations)} 条，只带了前 {len(shown)} 条）")
    lines.append("</observations-index>")
    return "\n".join(lines)


# ── L4 轮次摘要构建 ──────────────────────────────────────────────

MAX_OUTCOMES = 5


def build_round_digest(
    confirmed_proposals: Sequence[Proposal],
    round_id: str,
    project_id: str,
) -> RoundDigest:
    """从已确认提案构建轮次摘要（纯函数）。"""
    sections: list[str] = []
    features: list[str] = []
    outcomes: list[str] = []

    for proposal in confirmed_proposals:
        # 提取触及章节
        section = str(proposal.target.section_id) if proposal.target.section_id else None
        if section and section not in sections:
            sections.append(section)
        # 提取涉及功能
        feature = str(proposal.target.feature_slug) if proposal.target.feature_slug else None
        if feature and feature not in features:
            features.append(feature)
        # 提取关键成果（前 5 条）
        if len(outcomes) < MAX_OUTCOMES:
            outcomes.append(f"{proposal.text}")

    summary = ""
    if sections:
        summary += f"触及 {'、'.join(sections)}"
    if outcomes:
        summary += f"，确认 {len(outcomes)} 条" if not summary else f"，确认 {len(outcomes)} 条"

    return RoundDigest(
        round_id=round_id,
        project_id=project_id,
        summary=summary,
        sections_touched=tuple(sections),
        features_addressed=tuple(features),
        key_outcomes=tuple(outcomes),
        outcomes_total=len(confirmed_proposals),
    )


def render_digests(digests: Sequence[RoundDigest], *, limit: int = 3) -> str:
    """渲染轮次摘要列表。

    `limit` 是"最多带几轮"（调用侧 `recent(limit=…)` 同一个数）；**带满时才说**
    "只带最近 N 轮"——没带满说明不知道有没有更早，不虚报。
    """

    if not digests:
        return ""
    lines = ["<round-digests>"]
    for digest in digests:
        lines.append(f"### {digest.round_id}")
        if digest.sections_touched:
            lines.append(f"触及: {', '.join(digest.sections_touched)}")
        if digest.key_outcomes:
            for outcome in digest.key_outcomes:
                lines.append(f"确认: {outcome}")
            omitted = digest.outcomes_total - len(digest.key_outcomes)
            if omitted > 0:
                lines.append(
                    f"（共 {digest.outcomes_total} 条，只带了前 "
                    f"{len(digest.key_outcomes)} 条）"
                )
    if len(digests) >= limit:
        lines.append(f"（只带最近 {limit} 轮摘要）")
    lines.append("</round-digests>")
    return "\n".join(lines)


# ── L3 灵感档案渲染 ──────────────────────────────────────────────


def render_inspirations_index(
    inspirations: Sequence[InspirationItem], limit: int = 5
) -> str:
    """渲染灵感索引（≤limit 条标题）。"""
    if not inspirations:
        return ""
    shown = inspirations[:limit]
    lines = ["<inspirations-index>"]
    for item in shown:
        lines.append(f"  💡 [{item.origin.value}:{item.item_id}] {item.statement}")
    omitted = len(inspirations) - len(shown)
    if omitted > 0:
        lines.append(f"  （共 {len(inspirations)} 条，只带了前 {len(shown)} 条）")
    lines.append("</inspirations-index>")
    return "\n".join(lines)
