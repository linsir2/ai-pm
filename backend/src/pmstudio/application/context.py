"""把领域现状渲染成提示词上下文。纯函数，无 IO，无模型——这样它可被单测钉住。

席位与整合者拿到的是**全文渲染**（作者要求：不读全文档怎么确保信息读全）。
文档真变大以后的索引层在设计里留位（P6），P2 不做。

**这是智能体上下文的唯一出口**：将来的 `PROJECT.md`（必读层）只是这条渲染链上的一节，
不是新的真相源（计划 §3.13）。
"""

from collections.abc import Callable, Collection, Mapping, Sequence
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
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId, RoundId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.sections import SECTION_SLOTS, SECTIONS, slot_block_id
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
    ClaimLabel.DECISION: "已经拍板的事，必须写清谁拍的",
}

LABELS_TEXT = "\n".join(
    f"- {label.value}（{LABEL_TITLES[label]}）：{LABEL_HINTS[label]}" for label in ClaimLabel
)
# 章节表：**只给短 id 与标题**，并明说"section_id 只能填前面那个短 id"。
# 真模型实测：写成 `section_id: "s3 用户与受众"`（把整行抄进来了）→ 未知章节 → 整轮 502。
SECTIONS_TEXT = "\n".join(f"- {spec.section_id} {spec.title}" for spec in SECTIONS) + (
    "\n（section_id 只能填上面那个短 id，如 s3；**不要带标题、不要带空格**）"
)

# 章节槽：section 级内容也可以指定"落到哪个槽"（目前只有 s8 的六项表态）。
# 与功能字段表分开列，免得模型把槽名当功能字段。
SECTION_SLOTS_TEXT = "\n".join(
    f"- {slot_block_id(section_id, slot.name)}（{slot.title}，章节槽；"
    f"写它时给 section_id={section_id} + field={slot.name}，不要给 feature_slug）"
    for section_id, slots in SECTION_SLOTS.items()
    for slot in slots
) or "（没有章节槽）"

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
    refs = " ".join(str(eid) for eid in entry.refs)
    return f"[{entry.entry_id}] {entry.text}（{refs}）"


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


def render_document(view: DocumentView) -> str:
    lines: list[str] = [f"# {view.title}（v{view.version_no}）"]
    for section in view.sections:
        lines.append(f"## {section.section_id} {section.title}")
        body: list[str] = []
        for block in section.blocks:
            body.extend(_block_lines(block))
        lines.extend(body or ["（空）"])
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
    view: DocumentView, *, readiness: Sequence[ReadinessItem] = ()
) -> str:
    """智能体看到的"文档现状" = 全文渲染 + 缺口清单。

    缺口必须进提示词，否则就绪度只是个界面上的仪表盘，不改变 AI 行为
    （`01-规格.md` §7.2）。
    """

    gaps = render_readiness(readiness)
    text = render_document(view)
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


def render_seat_conflicts(conflicts: tuple[SeatConflict, ...]) -> str:
    """给整合者看的"席位间冲突"。"""
    lines: list[str] = []
    for c in conflicts:
        lines.append(
            f"- [{c.seat_a}]「{c.claim_a}」 ↔ [{c.seat_b}]「{c.claim_b}」"
            f"（两个席位对「{c.about}」持相反判断：要出一道**让用户拍板**的题，`kind` 写 conflict）"
        )
    return "\n".join(lines) or "（这一轮没有检测出席位间冲突）"


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


def _opinion_lines(
    review: SeatReview,
    opinion: Opinion,
    *,
    ledger: EvidenceLedger | None,
    round_id: RoundId,
) -> list[str]:
    """一条意见的渲染（`render_reviews` 与 `render_selected_opinions` 共用同一份）。"""

    form = "疑问" if opinion.form is OpinionForm.QUESTION else "判断"
    handle = seat_entry_handle(review.seat_id, opinion.local_id)
    own_eid = _own_eid(review, opinion.local_id, ledger=ledger, round_id=round_id)
    refs = " ".join(str(eid) for eid in opinion.evidence_refs)
    if own_eid is not None:
        identity = f"（席位条目 {handle} · 出处编号 {own_eid}）"
    elif ledger is not None:
        identity = f"（席位条目 {handle} · 没有出处编号）"
    else:
        identity = f"（席位条目 {handle}）"
    return [
        f"- [{form}]{identity}关于{opinion.about}",
        f"  结论：{opinion.claim}",
        f"  理由：{opinion.because}",
        f"  依据：{refs or opinion.basis_note or '（无依据）'}",
    ]


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
            lines.append(f"- [{entry.entry_id}] {entry.text}")
    for entry in document.preference_entries:
        lines.append(f"- [{entry.entry_id}] （偏好区）{entry.text}")
    return "\n".join(lines) or "（文档现在没有任何条目）"


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
) -> tuple[tuple[str, MaterialChunk], ...]:
    """**唯一**决定"哪些块进这一轮"的地方：按材料顺序、块顺序取，到预算为止。

    返回 `(material_id, chunk)`：调用方拿它去发号（`Round.materials`）与渲染。

    `cost` 默认是"正文 + 每个块那行编号的开销"；联网结果要传 `web_block_cost`——
    那段渲染每条还要写标题与 URL，成本不一样（**同一件事不许有两把尺子**）。
    """

    weigh = cost or _material_block_cost
    picked: list[tuple[str, MaterialChunk]] = []
    used = 0
    for packet in packets:
        for chunk in packet.parsed.chunks:
            if used + weigh(packet, chunk) > budget:
                return tuple(picked)
            picked.append((packet.material_id, chunk))
            used += weigh(packet, chunk)
    return tuple(picked)


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
MAX_OPEN_QUESTIONS = 10


def _section_entry_count(section) -> int:
    """一个章节里的条目总数（含子块）。"""

    def count_block(block) -> int:
        return len(block.entries) + sum(count_block(child) for child in block.children)

    return sum(count_block(b) for b in section.blocks)


def _compute_coverage(view) -> tuple[tuple[str, str], ...]:
    """计算每个章节的覆盖状态: empty / partial / complete。"""
    from pmstudio.domain.authoring.readiness import SECTION_MIN_ENTRIES
    section_minimums: dict[str, int] = {
        sid: min_val for sid, (min_val, _) in SECTION_MIN_ENTRIES.items()
    }
    for sid in ("s5", "s6", "s7", "s9", "s11"):
        section_minimums.setdefault(sid, 2)
    section_minimums.setdefault("s8", 6)

    result: list[tuple[str, str]] = []
    for section in view.sections:
        sid = str(section.section_id)
        count = _section_entry_count(section)
        minimum = section_minimums.get(sid, 1)

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
) -> ProjectContext:
    """从文档现状 + 记忆 + 就绪度构建 L1 项目上下文（纯函数）。"""
    sections_covered = _compute_coverage(document_view)

    features_defined: list[str] = []
    for section in document_view.sections:
        if str(section.section_id) == "s5":
            for block in section.blocks:
                if block.feature_slug:
                    features_defined.append(str(block.feature_slug))

    top_decisions = tuple(
        e.text for e in memory_entries if e.kind == MemoryKind.DECISION
    )[:MAX_DECISIONS]

    open_questions: list[str] = []
    for section in document_view.sections:
        if str(section.section_id) == "s11":
            for block in section.blocks:
                if block.kind == "open_questions":
                    open_questions.extend(block.entries)
    open_questions = tuple(open_questions[:MAX_OPEN_QUESTIONS])

    return ProjectContext(
        sections_covered=sections_covered,
        features_defined=tuple(features_defined),
        top_decisions=top_decisions,
        open_questions=open_questions,
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

    if ctx.open_questions:
        lines.append(f"开放问题: {' | '.join(ctx.open_questions)}")

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
    )


def render_digests(digests: Sequence[RoundDigest]) -> str:
    """渲染轮次摘要列表（≤3 轮）。"""
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
    lines.append("</inspirations-index>")
    return "\n".join(lines)
