"""把领域现状渲染成提示词上下文。纯函数，无 IO，无模型——这样它可被单测钉住。

席位与整合者拿到的是**全文渲染**（作者要求：不读全文档怎么确保信息读全）。
文档真变大以后的索引层在设计里留位（P6），P2 不做。

**这是智能体上下文的唯一出口**：将来的 `PROJECT.md`（必读层）只是这条渲染链上的一节，
不是新的真相源（计划 §3.13）。
"""

from collections.abc import Mapping, Sequence

from pmstudio.application.ports import SeatDefinition
from pmstudio.domain.authoring.blocks import Entry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.authoring.schema import FEATURE_FIELDS
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.opinion import OpinionForm, SeatReview
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

# 功能字段表：从 `authoring/schema.py` 生成（结构的唯一真相源），不许手抄。
FEATURE_FIELDS_TEXT = "\n".join(
    f"- {spec.name}（{spec.title}，{spec.kind.value}"
    f"{'，可多条' if spec.multiple else ''}{'，必填' if spec.required else ''}）"
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
    review: SeatReview, local_id: str, *, ledger: EvidenceLedger | None
) -> EvidenceId | None:
    """这条意见自己的出处编号（只有"判断"有；"疑问"不进账本，所以没有）。"""

    if ledger is None:
        return None
    evidence = ledger.find_by_local(
        seat_id=review.seat_id, local_id=f"p{review.pass_index}:{local_id}"
    )
    return None if evidence is None else evidence.eid


def render_reviews(
    reviews: Sequence[SeatReview],
    *,
    titles: Mapping[str, str],
    ledger: EvidenceLedger | None = None,
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
            form = "疑问" if opinion.form is OpinionForm.QUESTION else "判断"
            handle = seat_entry_handle(review.seat_id, opinion.local_id)
            own_eid = _own_eid(review, opinion.local_id, ledger=ledger)
            refs = " ".join(str(eid) for eid in opinion.evidence_refs)
            if own_eid is not None:
                identity = f"（席位条目 {handle} · 出处编号 {own_eid}）"
            elif ledger is not None:
                identity = f"（席位条目 {handle} · 没有出处编号）"
            else:
                identity = f"（席位条目 {handle}）"
            lines.append(f"- [{form}]{identity}关于{opinion.about}")
            lines.append(f"  结论：{opinion.claim}")
            lines.append(f"  理由：{opinion.because}")
            lines.append(f"  依据：{refs or opinion.basis_note or '（无依据）'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or "（没有席位意见）"


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
