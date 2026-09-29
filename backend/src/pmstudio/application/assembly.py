"""组装：把 Integrator 的报文变成"能进文档的内容"和"能问人的题目"。

这一层只做机械的事：编号、形状、限额、降级、机械去重、结构兜底。
专业判断不在这一层（那是席位与整合者的事），文档规则也不在这一层（那是 `Document` 的事）。
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from pmstudio.application.agents import schemas
from pmstudio.application.context import seat_entry_handle
from pmstudio.application.errors import PacketParseError, unknown_ref_message
from pmstudio.application.evidence_refs import candidates_for, normalize_ref
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.schema import REQUIRED_FEATURE_FIELDS, field_spec
from pmstudio.domain.provenance.evidence import EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    MAX_QUESTIONS_PER_BATCH,
    ListItem,
    Option,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import DroppedItem, DropSource, Round
from pmstudio.domain.services.traceability import TraceabilityChecker
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import Zone
from pmstudio.domain.shared.sections import FEATURE_HOME, section_slot
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

PURPOSE = "integration"

NEW_FEATURE_YES = "new_feature_yes"
NEW_FEATURE_NO = "new_feature_no"

OPEN_QUESTION_HOME = "s11"

#: 缺了这些章节的内容就"交不出去"——命中它们的题目算阻塞型（`01` §4 / `02` §⑤）
BLOCKING_SECTIONS = frozenset({"s1", "s2", "s3", "s4", "s5", "s8", "s10", "s12"})
REQUIRED_FEATURE_FIELD_NAMES = frozenset(spec.name for spec in REQUIRED_FEATURE_FIELDS)
BLOCKING_MARK = "〔未决·阻塞〕"


@dataclass(frozen=True, slots=True)
class Assembly:
    questions: tuple[Question, ...]
    list_items: tuple[ListItem, ...]
    proposals: tuple[Proposal, ...]
    #: 本批没问上的题（进轮内队列；不是"清单项"）
    deferred: tuple[Question, ...] = ()
    #: 这一趟没进卡片的意见。三个来源合成一条通道（`04` C5 后半）：
    #: 模型申报的丢弃 / 归因对不上号 / 装配层核出来"根本没交代去处"。
    dropped: tuple[DroppedItem, ...] = ()
    #: 这一趟真的被题目用到的席位条目地址（整合者写对了的那些）。
    #: 去处核查要拿它当"算交代过"的依据之一，所以由装配层如实记下来。
    accounted_handles: frozenset[str] = frozenset()
    degrade_reason: str | None = None
    """整合失败、改走降级通道时的原因原文（见 `degraded_question_proposals`）。"""


@dataclass(frozen=True, slots=True)
class LandedAnswers:
    """机械落地的答案（`D3`）：能落的落成提案，落不了的**说清为什么**。"""

    proposals: tuple[Proposal, ...] = ()
    notes: tuple[str, ...] = ()


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def question_key(question: Question) -> tuple[str, str]:
    """题目的身份：**同一个去处 + 同一个问法**算同一件事（与 eval 的重复题判据同一口径）。"""

    return (question.target.label(), " ".join(question.title.split()))


def _first_question_number(round_: Round) -> int:
    """这一批的题号从几开始：接着本轮**已经问出去**的数量往下发（D4：题号轮内唯一）。

    为什么不能每批从 1 重排：`(轮次, 题号)` 是答案的身份，重排会让"按题号回查"的六处地方
    指到别的批（计划 §0.1）——真跑里第二批的答案挂着第一批的题面与 E 编号。
    没问上的题不占号（它们在队列里，下一批重新编号）。
    """

    return round_.asked_question_count + 1


#: s11 未决条目的标准文本（`open_question_proposal` 写进去的那个格式）：
#: 可选的阻塞标记 + 标题 + （未决定，待决人：…；跳过后果：…）
_OPEN_ITEM = re.compile(
    r"^(?:〔未决·阻塞〕)?(?P<title>.+?)（未决定，待决人：[^；]+；跳过后果：(?P<consequence>.*)）$"
)

#: 去处标记：写在条目**末尾**，与旧格式向后兼容（老条目没有它就是 `None`）
_TARGET_MARK = re.compile(r"〔去处：(?P<label>[^〕]+)〕\s*$")


@dataclass(frozen=True, slots=True)
class OpenItem:
    """s11 未决条目的解析结果（2026-09-29 P11）。

    比原来的三元组多一个 `target_label`：**去处要跟着内容走**——没有它，问回来只能回到 s11，
    答案也就永远落不回正文（真跑 `p-0b212145` 里四条答案就是这么蒸发的）。
    """

    title: str
    consequence: str
    blocking: bool
    target_label: str | None = None


def parse_open_item(text: str) -> OpenItem | None:
    """s11 未决条目的标准文本 → `OpenItem`；解析不了返回 None。

    格式是我们自己写进去的（有测试钉住）；用户手写的条目、老版本写的条目都可能长别的样子——
    那就**跳过**（调用方把它算进缺口），绝不猜。**老格式（没有去处标记）照旧认**，`target_label=None`。
    """

    stripped = text.strip()
    target_label: str | None = None
    mark = _TARGET_MARK.search(stripped)
    if mark is not None:
        target_label = mark.group("label").strip()
        stripped = stripped[: mark.start()].rstrip()
    match = _OPEN_ITEM.match(stripped)
    if match is None:
        return None
    return OpenItem(
        title=match.group("title"),
        consequence=match.group("consequence"),
        blocking=stripped.startswith(BLOCKING_MARK),
        target_label=target_label,
    )


def deferred_from_open_items(
    document: Document,
    *,
    ledger: EvidenceLedger,
    limit: int,
    skip_entries: frozenset[str] = frozenset(),
) -> tuple[Question, ...]:
    """文档 s11 里**最老的** N 条未决条目 → carried 题（跨轮队列就是这些条目本身）。

    每一条都记住 `from_entry`：它被回答之后，装配层要机械地出一条 `remove` 把它撤下
    （`02` §11.0.10），这样 s11 才不会只涨不消。
    """

    view = document.view()
    section = next(
        (item for item in view.sections if str(item.section_id) == OPEN_QUESTION_HOME), None
    )
    if section is None:
        return ()
    carried: list[Question] = []
    for block in section.blocks:
        for entry in block.entries:
            if str(entry.entry_id) in skip_entries:
                # 这一轮已经问过它了（答没答都要等出草案那一刻才处理）——别再问一遍
                continue
            parsed = parse_open_item(entry.text)
            if parsed is None:
                continue
            title, consequence, blocking = parsed.title, parsed.consequence, parsed.blocking
            carried.append(
                Question(
                    question_id=QuestionId("q0"),  # 占位：assemble 统一重编号
                    target=Target.body(OPEN_QUESTION_HOME),
                    type=QuestionType.TEXT,
                    kind=QuestionKind.DECISION if blocking else QuestionKind.GAP,
                    title=title,
                    why="这是前几轮留下的未决项，这一轮把它定下来",
                    consequence_if_skipped=consequence,
                    blocking=blocking,
                    from_entry=str(entry.entry_id),
                )
            )
            if len(carried) >= limit:
                return tuple(carried)
    return tuple(carried)


def _seat_of_handle(handle: str, *, seats: frozenset[str]) -> str | None:
    """从席位条目地址里解析席位（解析不到就留 None——留痕照样留，不猜）。"""

    if ":" not in handle:
        return None
    seat_id = handle.split(":", 1)[0].strip()
    return seat_id if seat_id in seats else None


def dropped_from_packet(
    packet: schemas.IntegrationPacket,
    *,
    reviews: Sequence[SeatReview],
    pass_index: int,
) -> tuple[DroppedItem, ...]:
    """模型申报的丢弃：原因它自己写；席位解析不出来也照收（留痕比归类重要）。"""

    seats = frozenset(review.seat_id for review in reviews)
    return tuple(
        DroppedItem(
            handle=item.handle,
            reason=item.reason,
            source=DropSource.INTEGRATOR,
            seat_id=_seat_of_handle(item.handle, seats=seats),
            pass_index=pass_index,
        )
        for item in packet.dropped_opinions
    )


def unaccounted_drops(
    reviews: Sequence[SeatReview],
    *,
    ledger: EvidenceLedger,
    accounted_handles: frozenset[str],
    accounted_eids: frozenset[EvidenceId],
    declared: frozenset[str],
) -> tuple[DroppedItem, ...]:
    """装配层的核查：这条意见**既没被引用、也没被申报** → 留一条痕。

    这三条边界是为了把假阳性压到最低（任一条成立就算"交代过"）：
    1. 地址出现在某道题的 `from_opinions` 里；
    2. 它自己的出处编号（只有判断有）出现在某条提案或某道题的引用里；
    3. 模型在 `dropped_opinions` 里申报过。
    """

    items: list[DroppedItem] = []
    for review in reviews:
        for opinion in review.opinions:
            handle = seat_entry_handle(review.seat_id, opinion.local_id)
            if handle in accounted_handles or handle in declared:
                continue
            own = ledger.find_by_local(
                seat_id=review.seat_id,
                local_id=f"p{review.pass_index}:{opinion.local_id}",
            )
            if own is not None and own.eid in accounted_eids:
                continue
            items.append(
                DroppedItem(
                    handle=handle,
                    reason="整合既没说丢掉它，也没交代它落到了哪儿",
                    source=DropSource.UNACCOUNTED,
                    seat_id=review.seat_id,
                    pass_index=review.pass_index,
                )
            )
    return tuple(items)


def assemble(
    packet: schemas.IntegrationPacket,
    *,
    round_: Round,
    document: Document,
    ledger: EvidenceLedger,
    carried: Sequence[Question] = (),
) -> Assembly:
    known = frozenset(evidence.eid for evidence in ledger.evidences())
    existing_features = frozenset(
        block.feature_slug for block in document.features() if block.feature_slug is not None
    )
    authorized = round_.authorized_new_features
    existing = _existing_content(document)

    proposals: list[Proposal] = []
    feature_questions: list[Question] = []
    pending_features: dict[str, list[str]] = {}
    pass_index = round_.seat_round_count
    dropped: list[DroppedItem] = []
    used_handles: set[str] = set()

    for index, draft in enumerate(packet.proposals, start=1):
        target = _target_or_drop(
            draft.target, dropped=dropped, pass_index=pass_index, what="提案", index=index
        )
        if target is None:
            continue
        refs = _refs(draft.evidence_refs, known=known)
        slug = str(target.feature_slug) if target.feature_slug is not None else None
        if slug is not None and slug not in existing_features and slug not in authorized:
            pending_features.setdefault(slug, []).extend(_asking_seats(refs, ledger=ledger))
            continue
        if (target.label(), normalize(draft.text)) in existing:
            continue
        proposals.append(
            _proposal(
                draft,
                target=target,
                refs=refs,
                index=index,
                round_id=round_.round_id,
                document=document,
                ledger=ledger,
            )
        )

    for slug, seats in pending_features.items():
        feature_questions.append(
            Question(
                question_id=QuestionId("q0"),  # 占位，收口时统一重编
                target=Target.body(FEATURE_HOME, feature_slug=FeatureSlug(slug)),
                type=QuestionType.SINGLE,
                title=f"「{slug}」是一个独立功能，还是现有功能的一部分？",
                why="新建功能子树会改变文档结构，只有用户能拍板（席位不许自己建）",
                consequence_if_skipped="先按「不新建」处理，这条能力记进开放性清单",
                options=(
                    Option(NEW_FEATURE_YES, "是独立功能（新建一棵功能子树）"),
                    Option(NEW_FEATURE_NO, "不是，属于现有功能的一部分"),
                ),
                kind=QuestionKind.DECISION,
                asked_by=tuple(sorted(set(seats))),
            )
        )

    # carried 排最前：上一批/上一轮没问上的题**优先问**（`02` §11.0.10）
    ordered = list(carried)
    seen_questions = {question_key(item) for item in ordered}
    # 本轮**问过的**题也进种子（含已跳过）：同一轮里问过就不再问第二遍（D2）。
    # 原来只看"carried + 本批"，第一批量级那道题在第二批被原样再问一遍（报告 F6）。
    seen_questions |= {
        question_key(question)
        for form in round_.questionnaire_history
        for question in form.questions
    }
    for item in feature_questions:
        ordered.append(item)
        seen_questions.add(question_key(item))
    for index, item in enumerate(packet.questions, start=1):
        target = _target_or_drop(
            item.target, dropped=dropped, pass_index=pass_index, what="题目", index=index
        )
        if target is None:
            continue
        question = _question(
            item,
            target=target,
            known=known,
            ledger=ledger,
            reviews=round_.current_reviews,
            pass_index=pass_index,
            dropped=dropped,
            used_handles=used_handles,
        )
        if question_key(question) in seen_questions:
            continue  # 模型又问了同一件事：留 carried 那份，丢新出的
        seen_questions.add(question_key(question))
        ordered.append(question)
    kept = [
        replace(question, question_id=QuestionId(f"q{index}"))
        for index, question in enumerate(
            ordered[:MAX_QUESTIONS_PER_BATCH], start=_first_question_number(round_)
        )
    ]
    # 没问上的题进轮内队列（不是"清单项"）：下一批优先问，出草案时落 s11
    deferred = tuple(ordered[MAX_QUESTIONS_PER_BATCH:])
    list_items: list[ListItem] = []
    for index, item in enumerate(packet.list_items, start=1):
        target = _target_or_drop(
            item.target, dropped=dropped, pass_index=pass_index, what="清单项", index=index
        )
        if target is None:
            continue
        list_items.append(ListItem(text=item.text, target=target, reason=item.reason))
    return Assembly(
        questions=tuple(kept),
        deferred=deferred,
        list_items=tuple(list_items),
        proposals=tuple(proposals),
        dropped=tuple(dropped)
        + dropped_from_packet(
            packet, reviews=round_.current_reviews, pass_index=pass_index
        ),
        accounted_handles=frozenset(used_handles),
    )


def questionnaire_of(
    round_id: RoundId,
    form_id: FormId,
    questions: Sequence[Question],
    list_items: Sequence[ListItem],
) -> Questionnaire | None:
    if not questions:
        return None
    return Questionnaire(
        form_id=form_id,
        round_id=round_id,
        questions=tuple(questions),
        list_items=tuple(list_items),
    )


def draft_of(
    round_id: RoundId,
    draft_id: DraftId,
    proposals: Sequence[Proposal],
    gaps: Sequence[str],
) -> Draft | None:
    if not proposals:
        return None
    return Draft(
        draft_id=draft_id, round_id=round_id, proposals=tuple(proposals), gaps=tuple(gaps)
    )


def seat_questions(reviews: Sequence[SeatReview]) -> tuple[tuple[str, Opinion], ...]:
    """本轮席位交回的**疑问**（带上是哪个席位说的）。

    疑问不进出处账本，所以整合失败时它是唯一能救回来的东西：原样落成开放问题，
    总比把整轮判死、让用户对着九条问不出来的疑问干瞪眼强（`04-未决.md` C9）。
    """

    return tuple(
        (review.seat_id, opinion)
        for review in reviews
        for opinion in review.opinions
        if opinion.form is OpinionForm.QUESTION
    )


def degraded_question_proposals(
    reviews: Sequence[SeatReview],
    *,
    ledger: EvidenceLedger,
    round_id: RoundId,
    now: datetime,
    start_index: int,
    reason: str,
) -> tuple[Proposal, ...]:
    """整合失败时的出口：把席位疑问**原样**落成 s11 开放问题，并出草案卡。

    与"跳过问卷"那条降级（`_system_defaults` + `open_question_proposals`）同一套语义：
    内容不改写、来源写清是系统降级、原因摆在卡片上——差别只是触发者是"整合失败"。
    """

    proposals: list[Proposal] = []
    for offset, (seat_id, opinion) in enumerate(seat_questions(reviews)):
        eid = ledger.record(
            kind=EvidenceKind.SYSTEM_DEFAULT,
            text=f"{seat_id} 的疑问（整合失败，未归并）：{opinion.claim}",
            source=EvidenceSource(
                round_id=round_id,
                rationale=f"整合失败降级（{reason}）：席位疑问原样落到 s11",
            ),
            now=now,
        )
        index = start_index + offset
        proposals.append(
            Proposal(
                proposal_id=ProposalId(f"p{index:02d}"),
                entry_id=EntryId(f"{round_id}#open{index:02d}"),
                op=WriteOp.ADD,
                target=Target.body(OPEN_QUESTION_HOME),
                text=f"{opinion.about}：{opinion.claim}（整合失败，未归并；待决人：PM）",
                origin=Origin(
                    OriginType.SYSTEM_DEFAULT, (eid,), f"整合失败降级：{reason}"
                ),
                kind=BlockKind.QUESTION,
            )
        )
    return tuple(proposals)


def open_question_proposals(
    round_: Round,
    *,
    system_defaults: Mapping[QuestionId, EvidenceId],
    start_index: int,
) -> tuple[Proposal, ...]:
    """跳过的题按题面写明的后果落成 s11 开放问题（`02` §5）。

    题面与跳过后果回**出题那一批**去找（`questionnaire_history`）：题号在一轮里唯一之后，
    按 `(表单, 题号)` 查得到；拿当前批反查会把上一批的题静默丢掉（计划 §0.1 第 3 处）。
    """

    questionnaire = round_.questionnaire
    if questionnaire is None:
        return ()
    by_key = {
        (str(form.form_id), str(question.question_id)): question
        for form in round_.questionnaire_history
        for question in form.questions
    }
    seen: set[QuestionId] = set()
    pairs: list[tuple[Question, EvidenceId]] = []
    for answer_set in round_.answer_sets:
        for question_id in answer_set.skipped:
            question = by_key.get((str(answer_set.form_id), str(question_id)))
            eid = system_defaults.get(question_id)
            if question is None or question_id in seen:
                continue
            if eid is None:
                raise PacketParseError(
                    "跳过的题缺少 system_default 出处编号",
                    purpose=PURPOSE,
                    question_id=str(question_id),
                )
            seen.add(question_id)
            pairs.append((question, eid))

    proposals: list[Proposal] = []
    for offset, (question, eid) in enumerate(pairs):
        proposals.append(
            open_question_proposal(
                question,
                eid=eid,
                index=start_index + offset,
                round_id=round_.round_id,
                rationale="跳过的题按题面后果落开放问题",
            )
        )
    return tuple(proposals)


def downgraded_question_proposals(
    questions: Sequence[Question],
    *,
    system_defaults: Mapping[QuestionId, EvidenceId],
    start_index: int,
    round_id: RoundId,
    reason: str = "到席位轮上限",
) -> tuple[Proposal, ...]:
    """停止追问时，把"还想问的题"降级成 s11 开放问题。

    `02` §7 / `04-未决.md` C6+C7：到闸门仍有缺口就**全部显式化**——用户永远能走到草案卡，
    不会被系统卡在无限提问里。（这条是 P2 真模型冒烟才暴露出来漏实现的规格。）
    `reason` 写清是哪道闸门响的（问卷已满两批 / 已到席位轮上限），免得日后只看理由猜不出。
    """

    proposals: list[Proposal] = []
    for offset, question in enumerate(questions):
        eid = system_defaults.get(question.question_id)
        if eid is None:
            raise PacketParseError(
                "降级的题目缺少 system_default 出处编号",
                purpose=PURPOSE,
                question_id=str(question.question_id),
            )
        proposals.append(
            open_question_proposal(
                question,
                eid=eid,
                index=start_index + offset,
                round_id=round_id,
                rationale=f"{reason}，不再追问，转为开放性记录",
            )
        )
    return tuple(proposals)


def open_question_proposal(
    question: Question,
    *,
    eid: EvidenceId,
    index: int,
    round_id: RoundId,
    rationale: str,
) -> Proposal:
    return Proposal(
        proposal_id=ProposalId(f"p{index:02d}"),
        entry_id=EntryId(f"{round_id}#open{index:02d}"),
        op=WriteOp.ADD,
        target=Target.body(OPEN_QUESTION_HOME),
        text=(
            f"{BLOCKING_MARK if question.blocking else ''}{question.title}（未决定，待决人：PM；"
            f"跳过后果：{question.consequence_if_skipped}）"
            f"〔去处：{question.target.label()}〕"
        ),
        origin=Origin(OriginType.SYSTEM_DEFAULT, (eid,), rationale),
        kind=BlockKind.QUESTION,
    )


def landed_answer_proposals(
    round_: Round,
    *,
    ledger: EvidenceLedger,
    document: Document,
    cited: frozenset[EvidenceId] = frozenset(),
    start_index: int = 1,
) -> LandedAnswers:
    """答案要么被引用、要么机械落地（2026-09-29 拍板 `D3`）。

    跨轮去重只认文档（`P9` 定过：不新建第二真相源）：一条答案要是没落进文档，下一轮必然再问一遍
    （报告 F6）。所以出草案那一刻核查：这条答案的出处编号被这一趟的提案/题目引用了吗？
    `cited` 由调用方从**本轮**的提案与题目里收（那是一趟的产物，装配层看不到）。

    三种**不落地**，各自在 `notes` 里说清为什么（绝不静默）：
    ① `target` 是未授权的新功能 slug——落下去等于凭空建功能子树，撞硬约束；
    ② 这道题来自 s11 未决条目（`carried_sources` 命中）——答完已有机械 `remove`
       撤下它，再 ADD 一条会对冲；
    ③ 答案已经被引用（整合者干完了活）。

    空答（既没选项也没自由文本）没有内容可落：跳过，也不留噪声。
    """

    by_key = {
        (str(form.form_id), str(question.question_id)): question
        for form in round_.questionnaire_history
        for question in form.questions
    }
    existing_features = frozenset(
        block.feature_slug for block in document.features() if block.feature_slug is not None
    )
    authorized = round_.authorized_new_features
    carried = round_.carried_sources()

    landed: list[Proposal] = []
    notes: list[str] = []
    index = start_index
    for answer_set in round_.answer_sets:
        for answer in answer_set.answers:
            key = (str(answer_set.form_id), str(answer.question_id))
            question = by_key.get(key)
            if question is None:
                continue  # 老库/题面丢了：认不出这道题，就没法给它选去处（不猜）
            detail = _answer_detail(question, answer)
            if not detail:
                continue  # 空答：没有内容可落
            eid = _uncited_answer_eid(
                ledger,
                round_id=round_.round_id,
                question_id=answer.question_id,
                cited=cited,
            )
            if eid is None:
                continue  # 已经被引用（或认不出编号）
            slug = question.target.feature_slug
            if slug is not None and slug not in existing_features and slug not in authorized:
                notes.append(
                    f"「{question.title}」的答案没落进正文："
                    f"它想写进功能 {slug}，但这个功能还没被授权"
                    "（要写就先答一次「这是不是独立功能」）。"
                )
                continue
            if f"{answer_set.form_id}#{answer.question_id}" in carried:
                continue  # 已有机械 remove 撤下那条未决项，别再 ADD（会当场对冲）
            try:
                kind = _kind_for(question.target, None)
            except (PacketParseError, DomainError) as error:
                # 题的去处在**内容层**不合法（如功能字段表里没有这个名字）：跳过并说清，
                # 绝不把整轮打崩——用户刚答完题，一个字打错不该让这一趟全丢。
                notes.append(
                    f"「{question.title}」的答案没落进正文：去处 "
                    f"{question.target.label()} 不合法（{error}）。"
                )
                continue
            landed.append(
                Proposal(
                    proposal_id=ProposalId(f"p{index:02d}"),
                    entry_id=EntryId(f"{round_.round_id}#{index:02d}"),
                    op=WriteOp.ADD,
                    target=question.target,
                    text=f"用户已定：{detail}",
                    origin=Origin(
                        OriginType.USER,
                        (eid,),
                        "问卷答案机械落地（整合者没有引用它）",
                    ),
                    kind=kind,
                )
            )
            index += 1
    return LandedAnswers(proposals=tuple(landed), notes=tuple(notes))


def _answer_detail(question: Question, answer: object) -> str:
    """答案的人话：优先用**选项文案**（`new_feature_no` 这种机器 id 不能进正文），否则自由文本。"""

    selected = tuple(getattr(answer, "selected", ()) or ())
    chosen = [option.text for option in question.options if option.option_id in selected]
    text = str(getattr(answer, "text", "") or "").strip()
    return " / ".join(part for part in ("、".join(chosen), text) if part)


def _uncited_answer_eid(
    ledger: EvidenceLedger,
    *,
    round_id: RoundId,
    question_id: QuestionId,
    cited: frozenset[EvidenceId],
) -> EvidenceId | None:
    """这条答案自己的出处编号；**已经被引用**、或**认不出**（老库同号两条）都不返回。"""

    found = [
        evidence
        for evidence in ledger.evidences()
        if evidence.kind is EvidenceKind.CARD_ANSWER
        and evidence.source.round_id == round_id
        and evidence.source.question_id == question_id
    ]
    if len(found) != 1 or found[0].eid in cited:
        return None
    return found[0].eid


def is_blocking(target: Target) -> bool:
    """这道题不答，文档就交不出去吗？**规则算，不问模型**（`01` §7）。

    命中必填功能字段，或落在"那几章不写就没有交付物"的章节上 → 阻塞；
    偏好区永远不算（偏好不进正文）。
    """

    if target.zone is Zone.PREFERENCE:
        return False
    if target.field is not None and target.feature_slug is not None:
        return target.field in REQUIRED_FEATURE_FIELD_NAMES
    return str(target.section_id) in BLOCKING_SECTIONS


# —— 内部 ——


def _target(draft: schemas.TargetDraft) -> Target:
    try:
        return schemas.target_of(draft)
    except DomainError as error:
        # 把模型给的原样 target 带回给它：重装一次时它得看得见自己错在哪，
        # 否则只会重犯同一处（真模型冒烟实测过这一条）。
        raise PacketParseError(
            f"去处不合法：{error.message}；你给的是 {draft.model_dump_json(exclude_none=True)}",
            purpose=PURPOSE,
            target=draft.model_dump(),
        ) from error


def _target_or_drop(
    draft: schemas.TargetDraft,
    *,
    dropped: list[DroppedItem],
    pass_index: int,
    what: str,
    index: int,
) -> Target | None:
    """去处写错：丢**这一条**并留痕，不放整轮失败（2026-09-28 作者拍板）。

    为什么它跟"编号写错"不一样：编号写错 = 内容没有出处，追溯链断了（GB1）→ 必须失败；
    **去处**写错只是"不知道该放哪儿"，那条内容不进文档，但其它内容与整轮进度不该被它拖死
    （真模型实测：一次笔误让两次整合都过不了校验，整轮降级、所有席位疑问掉进 s11）。
    """

    try:
        return _target(draft)
    except PacketParseError as error:
        dropped.append(
            DroppedItem(
                handle=f"{what} #{index}",
                reason=error.message,
                source=DropSource.ASSEMBLY,
                seat_id=None,
                pass_index=pass_index,
            )
        )
        return None


def _refs(raw: Sequence[str], *, known: frozenset[EvidenceId]) -> tuple[EvidenceId, ...]:
    # 归一化写法（E08 → E8）后再校验：写法差异不该判死整轮，编造编号照样判死（报告 F3）。
    refs = tuple(EvidenceId(normalize_ref(item, known=known)) for item in raw)
    if not refs:
        raise PacketParseError("提案必须带 evidence_refs", purpose=PURPOSE)
    unknown = [str(ref) for ref in refs if ref not in known]
    if unknown:
        raise PacketParseError(
            unknown_ref_message(unknown),
            purpose=PURPOSE,
            candidates=[candidates_for(item, known=known) for item in unknown],
        )
    return refs


def _asking_seats(refs: Sequence[EvidenceId], *, ledger: EvidenceLedger) -> list[str]:
    seats: list[str] = []
    for ref in refs:
        seat_id = ledger.get(ref).source.seat_id
        if seat_id and seat_id not in seats:
            seats.append(seat_id)
    return seats


def _kind_for(target: Target, name: str | None) -> BlockKind:
    try:
        if target.field is not None:
            if target.feature_slug is not None:
                return field_spec(target.field).kind
            slot = section_slot(target.section_id, target.field)
            assert slot is not None  # Target 已经校验过
            return slot.kind
        return BlockKind(name) if name else BlockKind.TEXT
    except (DomainError, ValueError) as error:
        raise PacketParseError(
            f"给不出 block 类型：{error}", purpose=PURPOSE, field=target.field, kind=name
        ) from error


def _proposal(
    draft: schemas.ProposalDraft,
    *,
    target: Target,
    refs: tuple[EvidenceId, ...],
    index: int,
    round_id: RoundId,
    document: Document,
    ledger: EvidenceLedger,
) -> Proposal:
    try:
        op = WriteOp(draft.op)
    except ValueError as error:
        raise PacketParseError(f"未知写入操作：{draft.op}", purpose=PURPOSE) from error
    if op is WriteOp.ADD:
        entry_id = EntryId(f"{round_id}#{index:02d}")
    else:
        if not draft.entry_id:
            raise PacketParseError(
                f"{draft.op} 必须给 entry_id（用既有条目清单里的 id）", purpose=PURPOSE
            )
        if not _entry_exists(document, draft.entry_id):
            raise PacketParseError(
                f"要改的条目不存在：{draft.entry_id}",
                purpose=PURPOSE,
                entry_id=draft.entry_id,
            )
        entry_id = EntryId(draft.entry_id)
    try:
        return Proposal(
            proposal_id=ProposalId(f"p{index:02d}"),
            entry_id=entry_id,
            op=op,
            target=target,
            text=draft.text.strip(),
            origin=Origin(
                type=_origin_type(draft.origin_type, refs=refs, ledger=ledger),
                refs=refs,
                rationale=draft.rationale,
            ),
            kind=_kind_for(target, draft.kind),
        )
    except DomainError as error:
        raise PacketParseError(
            f"提案过不了领域校验：{error.message}", purpose=PURPOSE, proposal_index=index
        ) from error


def _origin_type(
    claimed: str, *, refs: tuple[EvidenceId, ...], ledger: EvidenceLedger
) -> OriginType:
    """来源分组**按账本算**，不采信模型自报（`02` §4.1）。

    模型把"依据全是席位自己的判断"的内容标成 `derived`（甚至 `user`）时，
    它会进"它替你判断的"那一组；用户勾了、提交时才撞 `invariant:E4`（真模型实测过）。
    规则：说 `user` / `derived` 的，必须真能回溯到用户输入或问卷答案，否则降级成 `proposed`
    （"它提议的"，默认不勾）——这正是 `02` §4.1 早就写着的口径。
    """

    origin = OriginType(claimed)
    if origin not in (OriginType.USER, OriginType.DERIVED):
        return origin
    result = TraceabilityChecker().check(refs, ledger=ledger)
    return origin if result.traceable else OriginType.PROPOSED


def _question(
    draft: schemas.QuestionDraft,
    *,
    target: Target,
    known: frozenset[EvidenceId],
    ledger: EvidenceLedger,
    reviews: Sequence[SeatReview],
    pass_index: int,
    dropped: list[DroppedItem],
    used_handles: set[str],
) -> Question:
    """题目：依据编号照旧校验，**归因由装配层算**，不信模型自己写的提问者。

    归因有两个来源，都指向"这条问题出在哪个席位"：

    - `evidence_refs` 里那些带 `seat_id` 的出处（席位判断进了账本，反查得到是谁）；
    - `from_opinions` 里的席位条目地址（`seat.xxx:O1`）——疑问不进账本，
      只能靠这个地址找回来。

    解析不出的地址只丢归因、写进 `dropped` 留痕：归属写错不该把一道好题判死。
    """

    refs = tuple(EvidenceId(normalize_ref(item, known=known)) for item in draft.evidence_refs)
    unknown = [str(ref) for ref in refs if ref not in known]
    if unknown:
        raise PacketParseError(
            unknown_ref_message(unknown, what="题目的依据编号不存在"),
            purpose=PURPOSE,
            candidates=[candidates_for(item, known=known) for item in unknown],
        )
    by_handle = {
        seat_entry_handle(review.seat_id, opinion.local_id): review.seat_id
        for review in reviews
        for opinion in review.opinions
    }
    seats = list(_asking_seats(refs, ledger=ledger))
    for raw in draft.from_opinions:
        handle = raw.strip()
        seat_id = by_handle.get(handle)
        if seat_id is None:
            dropped.append(
                DroppedItem(
                    handle=handle,
                    reason="归因对不上号：这一轮没有这条席位条目",
                    source=DropSource.ASSEMBLY,
                    seat_id=_seat_of_handle(handle, seats=frozenset(by_handle.values())),
                    pass_index=pass_index,
                )
            )
        else:
            used_handles.add(handle)
            if seat_id not in seats:
                seats.append(seat_id)
    try:
        return Question(
            question_id=QuestionId("q0"),
            target=target,
            type=QuestionType(draft.type),
            title=draft.title,
            why=draft.why,
            consequence_if_skipped=draft.consequence_if_skipped,
            options=tuple(Option(item.option_id, item.text) for item in draft.options),
            evidence_refs=refs,
            asked_by=tuple(sorted(set(seats))),
            kind=QuestionKind(draft.kind),
            blocking=is_blocking(target),
        )
    except DomainError as error:
        raise PacketParseError(
            f"题目过不了领域校验：{error.message}", purpose=PURPOSE, title=draft.title
        ) from error


def _entry_exists(document: Document, entry_id: str) -> bool:
    for block in document.blocks:
        if any(str(entry.entry_id) == entry_id for entry in block.entries):
            return True
    return any(str(entry.entry_id) == entry_id for entry in document.preference_entries)


def _existing_content(document: Document) -> set[tuple[str, str]]:
    """文档里已有内容的 (target.label(), 归一化文本)，用来机械去重。"""

    by_id = {block.block_id: block for block in document.blocks}
    keys: set[tuple[str, str]] = set()
    for block in document.blocks:
        if block.field is not None:
            parent = by_id.get(block.parent_id) if block.parent_id is not None else None
            slug = parent.feature_slug if parent is not None else None
            label = f"{FEATURE_HOME}/{slug}.{block.field}" if slug else str(block.block_id)
        elif block.kind is BlockKind.FEATURE:
            label = f"{FEATURE_HOME}/{block.feature_slug}"
        elif block.parent_id is not None:
            label = str(block.parent_id)
        else:
            continue
        keys.update((label, normalize(entry.text)) for entry in block.entries)
    keys.update(("preference", normalize(entry.text)) for entry in document.preference_entries)
    return keys
