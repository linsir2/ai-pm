"""组装：把 Integrator 的报文变成"能进文档的内容"和"能问人的题目"。

这一层只做机械的事：编号、形状、限额、降级、机械去重、结构兜底。
专业判断不在这一层（那是席位与整合者的事），文档规则也不在这一层（那是 `Document` 的事）。
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from pmstudio.application.agents import schemas
from pmstudio.application.errors import PacketParseError
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.schema import field_spec
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.questionnaire import (
    MAX_QUESTIONS_PER_BATCH,
    ListItem,
    Option,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.errors import DomainError
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
from pmstudio.domain.shared.sections import FEATURE_HOME
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

PURPOSE = "integration"

NEW_FEATURE_YES = "new_feature_yes"
NEW_FEATURE_NO = "new_feature_no"

OPEN_QUESTION_HOME = "s11"


@dataclass(frozen=True, slots=True)
class Assembly:
    questions: tuple[Question, ...]
    list_items: tuple[ListItem, ...]
    proposals: tuple[Proposal, ...]


def normalize(text: str) -> str:
    return " ".join(text.split()).casefold()


def assemble(
    packet: schemas.IntegrationPacket,
    *,
    round_: Round,
    document: Document,
    ledger: EvidenceLedger,
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

    for index, draft in enumerate(packet.proposals, start=1):
        target = _target(draft.target)
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

    ordered = feature_questions + [_question(item, known=known) for item in packet.questions]
    kept = [
        replace(question, question_id=QuestionId(f"q{index}"))
        for index, question in enumerate(ordered[:MAX_QUESTIONS_PER_BATCH], start=1)
    ]
    list_items = [_list_item(item) for item in packet.list_items]
    list_items += [
        ListItem(text=item.title, target=item.target, reason="本批只问 5 题，这条留到下一轮")
        for item in ordered[MAX_QUESTIONS_PER_BATCH:]
    ]
    return Assembly(
        questions=tuple(kept), list_items=tuple(list_items), proposals=tuple(proposals)
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


def open_question_proposals(
    round_: Round,
    *,
    system_defaults: Mapping[QuestionId, EvidenceId],
    start_index: int,
) -> tuple[Proposal, ...]:
    """跳过的题按题面写明的后果落成 s11 开放问题（`02` §5）。"""

    questionnaire = round_.questionnaire
    if questionnaire is None:
        return ()
    by_id = {question.question_id: question for question in questionnaire.questions}
    seen: set[QuestionId] = set()
    pairs: list[tuple[Question, EvidenceId]] = []
    for answer_set in round_.answer_sets:
        for question_id in answer_set.skipped:
            question = by_id.get(question_id)
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
) -> tuple[Proposal, ...]:
    """到席位轮上限时，把"还想问的题"降级成 s11 开放问题（不再追问）。

    `02` §7 / `04-未决.md` C7：到上限仍有缺口就**全部显式化**——用户永远能走到草案卡，
    不会被系统卡在无限提问里。（这条是 P2 真模型冒烟才暴露出来漏实现的规格。）
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
                rationale="到席位轮上限，不再追问，转为开放性记录",
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
            f"{question.title}（未决定，待决人：PM；"
            f"跳过后果：{question.consequence_if_skipped}）"
        ),
        origin=Origin(OriginType.SYSTEM_DEFAULT, (eid,), rationale),
        kind=BlockKind.QUESTION,
    )


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


def _refs(raw: Sequence[str], *, known: frozenset[EvidenceId]) -> tuple[EvidenceId, ...]:
    refs = tuple(EvidenceId(item) for item in raw)
    if not refs:
        raise PacketParseError("提案必须带 evidence_refs", purpose=PURPOSE)
    unknown = [str(ref) for ref in refs if ref not in known]
    if unknown:
        raise PacketParseError(f"引用了不存在的编号：{'、'.join(unknown)}", purpose=PURPOSE)
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
            return field_spec(target.field).kind
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
                type=OriginType(draft.origin_type),
                refs=refs,
                rationale=draft.rationale,
            ),
            kind=_kind_for(target, draft.kind),
        )
    except DomainError as error:
        raise PacketParseError(
            f"提案过不了领域校验：{error.message}", purpose=PURPOSE, proposal_index=index
        ) from error


def _question(draft: schemas.QuestionDraft, *, known: frozenset[EvidenceId]) -> Question:
    target = _target(draft.target)
    refs = tuple(EvidenceId(item) for item in draft.evidence_refs)
    unknown = [str(ref) for ref in refs if ref not in known]
    if unknown:
        raise PacketParseError(
            f"题目的依据编号不存在：{'、'.join(unknown)}", purpose=PURPOSE
        )
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
            asked_by=tuple(draft.asked_by),
            kind=QuestionKind(draft.kind),
        )
    except DomainError as error:
        raise PacketParseError(
            f"题目过不了领域校验：{error.message}", purpose=PURPOSE, title=draft.title
        ) from error


def _list_item(draft: schemas.ListItemDraft) -> ListItem:
    return ListItem(text=draft.text, target=_target(draft.target), reason=draft.reason)


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
