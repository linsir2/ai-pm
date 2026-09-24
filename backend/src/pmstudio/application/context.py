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
from pmstudio.domain.authoring.schema import FEATURE_FIELDS
from pmstudio.domain.authoring.views import BlockView, DocumentView
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.opinion import OpinionForm, SeatReview
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import EvidenceId
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.sections import SECTIONS
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
SECTIONS_TEXT = "\n".join(f"- {spec.section_id} {spec.title}" for spec in SECTIONS)

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
    """

    questionnaire = round_.questionnaire
    if questionnaire is None:
        return "--\n（本轮没问过问题）"
    by_id = {question.question_id: question for question in questionnaire.questions}
    answered_lines: list[str] = []
    skipped_lines: list[str] = []
    for answer_set in round_.answer_sets:
        for answer in answer_set.answers:
            question = by_id.get(answer.question_id)
            title = question.title if question else str(answer.question_id)
            chosen = "、".join(answer.selected)
            free = answer.text.strip()
            detail = " / ".join(part for part in (chosen, free) if part)
            eid = _answer_eid(ledger, answer.question_id)
            answered_lines.append(
                f"- {answer.question_id} {title} → {detail or '（空答）'}"
                + (f"〔{eid}〕" if eid else "")
            )
        for question_id in answer_set.skipped:
            question = by_id.get(question_id)
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


def answer_eids(*, ledger: EvidenceLedger) -> dict[str, str]:
    """题号 → 这条答案的出处编号（给席位做 `q3 → E8` 的引用翻译）。"""

    mapping: dict[str, str] = {}
    for evidence in ledger.evidences():
        question_id = evidence.source.question_id
        if question_id is None or evidence.kind is not EvidenceKind.CARD_ANSWER:
            continue
        mapping.setdefault(str(question_id), str(evidence.eid))
    return mapping


def _answer_eid(ledger: EvidenceLedger | None, question_id: object) -> str | None:
    if ledger is None:
        return None
    for evidence in ledger.evidences():
        if evidence.source.question_id == question_id:
            return str(evidence.eid)
    return None


def render_existing_entries(document: Document) -> str:
    """给整合者看的既有条目清单：要改 / 要删旧内容时得用它给的 entry id。"""

    lines: list[str] = []
    for block in document.blocks:
        for entry in block.entries:
            lines.append(f"- [{entry.entry_id}] {entry.text}")
    for entry in document.preference_entries:
        lines.append(f"- [{entry.entry_id}] （偏好区）{entry.text}")
    return "\n".join(lines) or "（文档现在没有任何条目）"
