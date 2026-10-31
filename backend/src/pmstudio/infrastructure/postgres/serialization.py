"""领域对象 ↔ JSON。往返测试是这一层的验收方式。

清一色的手写映射，故意不用反射：反射在枚举 / NewType / 可选字段上会静默出错，
而这里的错会直接变成"文档读出来少了一个字段"。
"""

from datetime import datetime

from pmstudio.domain.authoring.blocks import Block, Entry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.version import Snapshot, Version, VersionTrigger
from pmstudio.domain.discussion.card import Card, CardState
from pmstudio.domain.discussion.card import Grounding as DiscussionGrounding
from pmstudio.domain.discussion.thread import (
    Discussion,
    FindingRef,
    Participant,
    QuestionSnapshot,
    RoundSummary,
    ThreadStatus,
    Turn,
)
from pmstudio.domain.exploration.exploration import (
    Exploration,
    ExplorationFailure,
    ExplorationKind,
    ExplorationScope,
    ExplorationStatus,
    ScopeKind,
)
from pmstudio.domain.exploration.item import (
    BlindSpot,
    CandidateState,
    Grounding,
    Hypothesis,
)
from pmstudio.domain.exploration.research import (
    ResearchFinding,
    ResearchReport,
    ResearchSource,
)
from pmstudio.domain.memory.memory import MemoryEntry, MemoryRevision, ProjectMemory
from pmstudio.domain.provenance.evidence import Evidence, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Assumption, Claim, Contradiction
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    ListItem,
    Option,
    Question,
    QuestionKind,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.research import RoundResearchSource
from pmstudio.domain.round.retrieval import (
    ManualChunk,
    RetrievalHit,
    RetrievalStage,
    RetrievalTrace,
)
from pmstudio.domain.round.round import (
    DroppedItem,
    DropSource,
    Failure,
    MaterialRef,
    MemoryProposal,
    Round,
    SeatNomination,
    SeedRef,
)
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.round.web import (
    LEGACY_REQUEST_WHY,
    MAX_WEB_QUERIES,
    WebRequest,
    WebRequestState,
    normalized_query,
)
from pmstudio.domain.shared.errors import DomainError
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import (
    BlockId,
    DiscussionId,
    DocumentId,
    DraftId,
    EntryId,
    EvidenceId,
    ExplorationId,
    FeatureSlug,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
    SectionId,
)
from pmstudio.domain.shared.label import ClaimLabel, Zone
from pmstudio.domain.shared.memory import MemoryChange, MemoryKind, MemoryOp
from pmstudio.domain.shared.sections import SlotSpec
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import (
    DocumentTemplate,
    MinEntries,
    TemplatedSection,
    TemplateRole,
    standard_template,
)
from pmstudio.domain.shared.writing import BlockKind, WriteOp


def _when(value: datetime) -> str:
    return value.isoformat()


def _unwhen(value: str | datetime) -> datetime:
    """JSON 负载内部仍是 ISO 字符串；行上的 timestamptz 已经是 datetime。"""

    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _refs(values: tuple[EvidenceId, ...]) -> list[str]:
    return [str(value) for value in values]


def _opt(value: object) -> str | None:
    return None if value is None else str(value)


# —— authoring ——


def target_to_json(target: Target) -> dict:
    return {
        "zone": target.zone.value,
        "section_id": _opt(target.section_id),
        "feature_slug": _opt(target.feature_slug),
        "field": target.field,
    }


def target_from_json(data: dict) -> Target:
    return Target(
        zone=Zone(data["zone"]),
        section_id=None if data["section_id"] is None else SectionId(data["section_id"]),
        feature_slug=(
            None if data["feature_slug"] is None else FeatureSlug(data["feature_slug"])
        ),
        field=data["field"],
    )


def entry_to_json(entry: Entry) -> dict:
    return {"entry_id": str(entry.entry_id), "text": entry.text, "refs": _refs(entry.refs)}


def entry_from_json(data: dict) -> Entry:
    return Entry(
        entry_id=EntryId(data["entry_id"]),
        text=data["text"],
        refs=tuple(EvidenceId(item) for item in data["refs"]),
    )


def block_to_json(block: Block) -> dict:
    return {
        "block_id": str(block.block_id),
        "parent_id": _opt(block.parent_id),
        "kind": block.kind.value,
        "entries": [entry_to_json(entry) for entry in block.entries],
        "field": block.field,
        "feature_slug": _opt(block.feature_slug),
        "manually_edited": block.manually_edited,
    }


def block_from_json(data: dict) -> Block:
    return Block(
        block_id=BlockId(data["block_id"]),
        parent_id=None if data["parent_id"] is None else BlockId(data["parent_id"]),
        kind=BlockKind(data["kind"]),
        entries=tuple(entry_from_json(item) for item in data["entries"]),
        field=data["field"],
        feature_slug=(
            None if data["feature_slug"] is None else FeatureSlug(data["feature_slug"])
        ),
        manually_edited=data["manually_edited"],
    )


def snapshot_to_json(snapshot: Snapshot) -> dict:
    return {
        "blocks": [block_to_json(block) for block in snapshot.blocks],
        "preference_entries": [entry_to_json(entry) for entry in snapshot.preference_entries],
    }


def snapshot_from_json(data: dict) -> Snapshot:
    return Snapshot(
        blocks=tuple(block_from_json(item) for item in data["blocks"]),
        preference_entries=tuple(entry_from_json(item) for item in data["preference_entries"]),
    )


def version_to_row(project_id: ProjectId, version: Version) -> dict:
    return {
        "project_id": str(project_id),
        "version_no": version.version_no,
        "trigger": version.trigger.value,
        "summary": version.summary,
        "changed_block_ids": [str(item) for item in version.changed_block_ids],
        "snapshot": snapshot_to_json(version.snapshot),
        "created_at": _when(version.created_at),
    }


def version_from_row(row: dict) -> Version:
    return Version(
        version_no=row["version_no"],
        trigger=VersionTrigger(row["trigger"]),
        summary=row["summary"],
        changed_block_ids=tuple(BlockId(item) for item in row["changed_block_ids"]),
        created_at=_unwhen(row["created_at"]),
        snapshot=snapshot_from_json(row["snapshot"]),
    )


def template_to_json(template: DocumentTemplate) -> dict:
    """模板规格全文 JSON（行内存快照的形状；`documents.template` 列存它）。"""

    return {
        "template_id": template.template_id,
        "title": template.title,
        "sections": [
            {
                "section_id": str(section.section_id),
                "title": section.title,
                "purpose": section.purpose,
                "role": section.role.value,
                "slots": [
                    {"name": slot.name, "title": slot.title, "kind": slot.kind.value}
                    for slot in section.slots
                ],
                # 判据声明：没声明就不写键（老行同形，见 template_from_json 的回落）
                **(
                    {}
                    if section.min_entries is None
                    else {
                        "min_entries": {
                            "count": section.min_entries.count,
                            **(
                                {}
                                if section.min_entries.kind is None
                                else {"kind": section.min_entries.kind.value}
                            ),
                        }
                    }
                ),
                **({} if section.coverage is None else {"coverage": section.coverage}),
            }
            for section in template.sections
        ],
    }


def _min_entries_of_row(item: dict) -> MinEntries | None:
    """老行（切片 1 写的）没有 `min_entries` 键 = 没声明，不是坏数据。"""

    raw = item.get("min_entries")
    if raw is None:
        return None
    kind_raw = raw.get("kind")
    return MinEntries(
        count=int(raw["count"]),
        kind=None if kind_raw is None else BlockKind(str(kind_raw)),
    )


def template_from_json(data: dict) -> DocumentTemplate:
    """行内快照读回。形状坏了直接炸（行是自己写的，读不回就是 bug，不是用户错）。"""

    return DocumentTemplate(
        template_id=str(data["template_id"]),
        title=str(data["title"]),
        sections=tuple(
            TemplatedSection(
                section_id=SectionId(str(item["section_id"])),
                title=str(item["title"]),
                purpose=str(item["purpose"]),
                role=TemplateRole(str(item.get("role", "plain"))),
                slots=tuple(
                    SlotSpec(
                        name=str(slot["name"]),
                        title=str(slot["title"]),
                        kind=BlockKind(str(slot["kind"])),
                    )
                    for slot in item.get("slots", ())
                ),
                min_entries=_min_entries_of_row(item),
                coverage=item.get("coverage"),
            )
            for item in data["sections"]
        ),
    )


def document_to_row(document: Document, *, now: datetime) -> dict:
    return {
        "project_id": str(document.project_id),
        "document_id": str(document.document_id),
        "title": document.title,
        "template": template_to_json(document.template),
        "blocks": [block_to_json(block) for block in document.blocks],
        "preference_entries": [
            entry_to_json(entry) for entry in document.preference_entries
        ],
        "tombstones": [block_to_json(block) for block in document.tombstones],
        "created_at": _when(document.current_version.created_at),
        "updated_at": _when(now),
    }


def document_from_row(row: dict, versions: tuple[Version, ...]) -> Document:
    return Document(
        document_id=DocumentId(row["document_id"]),
        project_id=ProjectId(row["project_id"]),
        title=row["title"],
        # 老行没有 template 键 → STANDARD（与现行 13 章逐字节同义）。
        template=(
            template_from_json(row["template"])
            if row.get("template") is not None
            else standard_template()
        ),
        blocks=tuple(block_from_json(item) for item in row["blocks"]),
        preference_entries=tuple(
            entry_from_json(item) for item in row["preference_entries"]
        ),
        tombstones=tuple(block_from_json(item) for item in row["tombstones"]),
        versions=versions,
        created_at=_unwhen(row["created_at"]),
    )


# —— provenance ——


def evidence_to_json(evidence: Evidence) -> dict:
    source = evidence.source
    return {
        "eid": str(evidence.eid),
        "kind": evidence.kind.value,
        "text": evidence.text,
        "source": {
            "round_id": _opt(source.round_id),
            "raw_span": source.raw_span,
            "question_id": _opt(source.question_id),
            "seat_id": source.seat_id,
            "local_id": source.local_id,
            "derived_from": _refs(source.derived_from),
            "rationale": source.rationale,
            "external_ref": source.external_ref,
        },
        "created_at": _when(evidence.created_at),
        "supersedes": _opt(evidence.supersedes),
    }


def evidence_from_json(data: dict) -> Evidence:
    source = data["source"]
    return Evidence(
        eid=EvidenceId(data["eid"]),
        kind=EvidenceKind(data["kind"]),
        text=data["text"],
        source=EvidenceSource(
            round_id=None if source["round_id"] is None else RoundId(source["round_id"]),
            raw_span=source["raw_span"],
            question_id=(
                None if source["question_id"] is None else QuestionId(source["question_id"])
            ),
            seat_id=source["seat_id"],
            local_id=source["local_id"],
            derived_from=tuple(EvidenceId(item) for item in source["derived_from"]),
            rationale=source["rationale"],
            external_ref=source["external_ref"],
        ),
        created_at=_unwhen(data["created_at"]),
        supersedes=(
            None if data["supersedes"] is None else EvidenceId(data["supersedes"])
        ),
    )


def ledger_to_json(ledger: EvidenceLedger) -> list[dict]:
    return [evidence_to_json(evidence) for evidence in ledger.evidences()]


def ledger_from_json(project_id: ProjectId, items: list[dict]) -> EvidenceLedger:
    return EvidenceLedger.rehydrate(
        project_id=project_id,
        evidences=tuple(evidence_from_json(item) for item in items),
    )


# —— round ——


def claim_to_json(claim: Claim) -> dict:
    return {
        "claim_id": claim.claim_id,
        "text": claim.text,
        "label": claim.label.value,
        "target": target_to_json(claim.target),
        "raw_span": claim.raw_span,
        "eid": _opt(claim.eid),
        "assumption": (
            None
            if claim.assumption is None
            else {
                "failure_consequence": claim.assumption.failure_consequence,
                "verification": claim.assumption.verification,
            }
        ),
        "decided_by": claim.decided_by,
    }


def claim_from_json(data: dict) -> Claim:
    assumption = data["assumption"]
    return Claim(
        claim_id=data["claim_id"],
        text=data["text"],
        label=ClaimLabel(data["label"]),
        target=target_from_json(data["target"]),
        raw_span=data["raw_span"],
        eid=None if data["eid"] is None else EvidenceId(data["eid"]),
        assumption=(
            None
            if assumption is None
            else Assumption(
                failure_consequence=assumption["failure_consequence"],
                verification=assumption["verification"],
            )
        ),
        decided_by=data["decided_by"],
    )


def _opinion_to_json(opinion: Opinion) -> dict:
    return {
        "local_id": opinion.local_id,
        "form": opinion.form.value,
        "about": opinion.about,
        "claim": opinion.claim,
        "because": opinion.because,
        "evidence_refs": _refs(opinion.evidence_refs),
        "basis_note": opinion.basis_note,
    }


def _opinion_from_json(data: dict) -> Opinion:
    return Opinion(
        local_id=data["local_id"],
        form=OpinionForm(data["form"]),
        about=data["about"],
        claim=data["claim"],
        because=data["because"],
        evidence_refs=tuple(EvidenceId(item) for item in data["evidence_refs"]),
        basis_note=data["basis_note"],
    )


def review_to_json(review: SeatReview) -> dict:
    return {
        "seat_id": review.seat_id,
        "basis": review.basis,
        "opinions": [_opinion_to_json(item) for item in review.opinions],
        "pass_index": review.pass_index,
        # 老数据没有这个键（修之前落库的）：读时默认 None，不给旧轮次补编。
        "packet_id": review.packet_id,
    }


def review_from_json(data: dict) -> SeatReview:
    return SeatReview(
        seat_id=data["seat_id"],
        basis=data["basis"],
        opinions=tuple(_opinion_from_json(item) for item in data["opinions"]),
        pass_index=data["pass_index"],
        packet_id=data.get("packet_id"),
    )


def _question_to_json(question: Question) -> dict:
    return {
        "question_id": str(question.question_id),
        "target": target_to_json(question.target),
        "type": question.type.value,
        "kind": question.kind.value,
        "title": question.title,
        "why": question.why,
        "consequence_if_skipped": question.consequence_if_skipped,
        "options": [
            {"option_id": option.option_id, "text": option.text}
            for option in question.options
        ],
        "evidence_refs": _refs(question.evidence_refs),
        "asked_by": list(question.asked_by),
        "blocking": question.blocking,
        "from_entry": question.from_entry,
    }


def _question_from_json(data: dict) -> Question:
    return Question(
        question_id=QuestionId(data["question_id"]),
        target=target_from_json(data["target"]),
        type=QuestionType(data["type"]),
        kind=QuestionKind(data["kind"]),
        title=data["title"],
        why=data["why"],
        consequence_if_skipped=data["consequence_if_skipped"],
        options=tuple(
            Option(option_id=item["option_id"], text=item["text"])
            for item in data["options"]
        ),
        evidence_refs=tuple(EvidenceId(item) for item in data["evidence_refs"]),
        asked_by=tuple(data["asked_by"]),
        # 老行里没有这个键：按"非阻塞"读
        blocking=bool(data.get("blocking", False)),
        # 老行里没有这个键：新题一律没有来源
        from_entry=data.get("from_entry"),
    )


def questionnaire_to_json(questionnaire: Questionnaire) -> dict:
    return {
        "form_id": str(questionnaire.form_id),
        "round_id": str(questionnaire.round_id),
        "questions": [_question_to_json(item) for item in questionnaire.questions],
        "list_items": [_list_item_to_json(item) for item in questionnaire.list_items],
        # 留痕不写在这里：`Round.dropped` 是唯一通道（老行的读法见 _legacy_dropped）
    }


def _list_item_to_json(item: ListItem) -> dict:
    return {"text": item.text, "target": target_to_json(item.target), "reason": item.reason}


def _list_item_from_json(data: dict) -> ListItem:
    return ListItem(
        text=data["text"],
        target=target_from_json(data["target"]),
        reason=data["reason"],
    )


def questionnaire_from_json(data: dict) -> Questionnaire:
    return Questionnaire(
        form_id=FormId(data["form_id"]),
        round_id=RoundId(data["round_id"]),
        questions=tuple(_question_from_json(item) for item in data["questions"]),
        list_items=tuple(_list_item_from_json(item) for item in data["list_items"]),
    )


def _answer_to_json(answer: Answer) -> dict:
    return {
        "question_id": str(answer.question_id),
        "text": answer.text,
        "selected": list(answer.selected),
    }


def _answer_from_json(data: dict) -> Answer:
    return Answer(
        question_id=QuestionId(data["question_id"]),
        text=data["text"],
        selected=tuple(data["selected"]),
    )


def answer_set_to_json(answer_set: AnswerSet) -> dict:
    return {
        "form_id": str(answer_set.form_id),
        "answers": [_answer_to_json(item) for item in answer_set.answers],
        "answered_at": _when(answer_set.answered_at),
        "skipped": [str(item) for item in answer_set.skipped],
    }


def answer_set_from_json(data: dict) -> AnswerSet:
    return AnswerSet(
        form_id=FormId(data["form_id"]),
        answers=tuple(_answer_from_json(item) for item in data["answers"]),
        answered_at=_unwhen(data["answered_at"]),
        skipped=tuple(QuestionId(item) for item in data["skipped"]),
    )


def proposal_to_json(proposal: Proposal) -> dict:
    return {
        "proposal_id": str(proposal.proposal_id),
        "entry_id": str(proposal.entry_id),
        "op": proposal.op.value,
        "target": target_to_json(proposal.target),
        "text": proposal.text,
        "origin": {
            "type": proposal.origin.type.value,
            "refs": _refs(proposal.origin.refs),
            "rationale": proposal.origin.rationale,
        },
        "kind": None if proposal.kind is None else proposal.kind.value,
        # D5 例外位（M11）：老行没有这把键 → False（行为与今天一致）
        "allow_manual_override": proposal.allow_manual_override,
        # add 防线（2026-10-29）：机械判定快照随草案落库；**无条件写**——
        # 键存在而值是 None 与键不存在语义不同（后者是旧轮，没有这只手表）。
        "overlap": proposal.overlap,
        "base_version_no": proposal.base_version_no,
    }


def proposal_from_json(data: dict) -> Proposal:
    origin = data["origin"]
    return Proposal(
        proposal_id=ProposalId(data["proposal_id"]),
        entry_id=EntryId(data["entry_id"]),
        op=WriteOp(data["op"]),
        target=target_from_json(data["target"]),
        text=data["text"],
        origin=Origin(
            type=OriginType(origin["type"]),
            refs=tuple(EvidenceId(item) for item in origin["refs"]),
            rationale=origin["rationale"],
        ),
        kind=None if data["kind"] is None else BlockKind(data["kind"]),
        allow_manual_override=bool(data.get("allow_manual_override", False)),
        # 旧轮没有这把键 → None（先例：allow_manual_override 的可选键缺省）
        overlap=data.get("overlap"),
        base_version_no=data.get("base_version_no"),
    )


def draft_to_json(draft: Draft) -> dict:
    return {
        "draft_id": str(draft.draft_id),
        "round_id": str(draft.round_id),
        "proposals": [proposal_to_json(item) for item in draft.proposals],
        "gaps": list(draft.gaps),
    }


def draft_from_json(data: dict) -> Draft:
    return Draft(
        draft_id=DraftId(data["draft_id"]),
        round_id=RoundId(data["round_id"]),
        proposals=tuple(proposal_from_json(item) for item in data["proposals"]),
        gaps=tuple(data["gaps"]),
    )


def _failure_to_json(failure: Failure | None) -> dict | None:
    if failure is None:
        return None
    return {
        "at_step": failure.at_step.value,
        "waiting_on": failure.waiting_on.value,
        "reason": failure.reason,
    }


def _failure_from_json(data: dict | None) -> Failure | None:
    if data is None:
        return None
    return Failure(
        at_step=Step(data["at_step"]),
        waiting_on=WaitingOn(data["waiting_on"]),
        reason=data["reason"],
    )


def round_to_payload(round_: Round) -> dict:
    payload = {
        "user_input": round_.user_input,
        "analysis_basis": round_.analysis_basis,
        "claims": [claim_to_json(claim) for claim in round_.claims],
        "seats": list(round_.seats),
        # 这一趟实际跑的名单（缩编结果）；老行没有这个键，读回落回 seats
        "active_seats": list(round_.active_seats),
        # 本轮跳过的席位（§11.0.67）；老行没有这个键 → 读侧回落为空
        "skipped_seats": list(round_.skipped_seats),
        "nominated_seats": [
            {"seat_id": item.seat_id, "why": item.why} for item in round_.nominated_seats
        ],
        "seat_reviews": [review_to_json(review) for review in round_.seat_reviews],
        "questionnaire": (
            None if round_.questionnaire is None else questionnaire_to_json(round_.questionnaire)
        ),
        # 本轮每一批问卷（老行没有这个键 → 读侧回落到"只有当前这一批"）
        "questionnaire_history": [
            questionnaire_to_json(item) for item in round_.questionnaire_history
        ],
        "answer_sets": [answer_set_to_json(item) for item in round_.answer_sets],
        "draft": None if round_.draft is None else draft_to_json(round_.draft),
        "seat_round_count": round_.seat_round_count,
        "authorized_new_features": {
            str(slug): str(eid) for slug, eid in round_.authorized_feature_basis.items()
        },
        "dropped": [
            {
                "handle": item.handle,
                "reason": item.reason,
                "source": item.source.value,
                "seat_id": item.seat_id,
                "pass_index": item.pass_index,
                "packet_id": item.packet_id,
            }
            for item in round_.dropped
        ],
        # 轮内未决队列（老行没有这个键 → 读侧 .get）
        "deferred_questions": [_question_to_json(item) for item in round_.deferred],
        # 这一轮问回来的 s11 未决条目（老行没有这个键 → 读侧 .get）
        "carried_sources": round_.carried_sources(),
        # 这一轮历次记下的清单项（老行没有这个键 → 读侧回落到当轮问卷的那批）
        "list_items": [_list_item_to_json(item) for item in round_.list_items],
        # 被判成立场互斥的主张对子（老行没有这个键 → 空）
        "contradictions": [
            {"claim_id_a": item.claim_id_a, "claim_id_b": item.claim_id_b, "why": item.why}
            for item in round_.contradictions
        ],
        # 上次失败的报文类原因（重试时递给第一次尝试；老行没有这个键 → None）
        "retry_hint": round_.retry_hint,
        # 确认时记下的"它提议的"给/收（老行没有这两把键 → None，不编 0）
        "proposed_offered": round_.proposed_offered,
        "proposed_accepted": round_.proposed_accepted,
        # 出草案时算出来的"连续几轮没采用席位提议"（老行没有这个键 → 0，不提示）
        "proposal_ignore_streak": round_.proposal_ignore_streak,
        # 用户"继续深挖"多出来的批次额度（老行没有这个键 → 0）
        "extra_batches": round_.extra_batches,
        "integration_seq": round_.integration_seq,
        # 以及随之放宽的席位轮额度（"批后那一趟"，老行 → 0）
        "extra_seat_rounds": round_.extra_seat_rounds,
        "failure": _failure_to_json(round_.failure),
        "confirmed_version_no": round_.confirmed_version_no,
        "abandon_reason": round_.abandon_reason,
        # 这一轮带进提示词的材料块（老行没有这把键 → 空）
        "materials": [
            {
                "material_id": ref.material_id,
                "chunk_id": ref.chunk_id,
                "eid": str(ref.eid),
            }
            for ref in round_.materials
        ],
        # 挂到这一轮的来源卡（老行没有这把键 → 空）。快照，不是对探索的实时引用。
        "research_sources": [
            {
                "eid": str(item.eid),
                "title": item.title,
                "url": item.url,
                "excerpt": item.excerpt,
            }
            for item in round_.research_sources
        ],
        # 每一步检索的轨迹（老行没有这把键 → 空）。**不存 eid**：编号真相源是 materials+账本。
        "retrievals": [
            {
                "stage": item.stage.value,
                "pass_index": item.pass_index,
                "query": item.query,
                "hits": [
                    {
                        "material_id": hit.material_id,
                        "chunk_id": hit.chunk_id,
                        "score": hit.score,
                    }
                    for hit in item.hits
                ],
                "selected": [
                    {
                        "material_id": hit.material_id,
                        "chunk_id": hit.chunk_id,
                        "score": hit.score,
                    }
                    for hit in item.selected
                ],
                "ok": item.ok,
                "error": item.error,
                "created_at": _when(item.created_at) if item.created_at is not None else None,
            }
            for item in round_.retrievals
        ],
        # 手勾材料（投影时合计 ≤60%）；老行没有这把键 → 空
        "pinned_materials": list(round_.pinned_materials),
        # 搜索框「带进本轮」的块；老行没有这把键 → 空
        "manual_chunks": [
            {"material_id": item.material_id, "chunk_id": item.chunk_id}
            for item in round_.manual_chunks
        ],
        "unreferenced_materials": list(round_.unreferenced_materials),
        # 这一轮带进来的过目产物（老行没有这把键 → 读侧回落空；老行的两键形状
        # 读回来就是探索来源，M12 起两种来源共用 origin/ref_id/item_id）
        "seeds": [
            {"origin": item.origin, "ref_id": item.ref_id, "item_id": item.item_id}
            for item in round_.seeds
        ],
        # 三方结构化联网申请（P3a；老行没有这把键 → 读侧按 step 从旧两把键迁移）
        "web_requests": [
            {
                "request_id": item.request_id,
                "query": item.query,
                "why": item.why,
                "origin": item.origin,
                "seat_id": item.seat_id,
                "pass_index": item.pass_index,
                "state": item.state.value,
                "result_note": item.result_note,
                "created_at": (
                    _when(item.created_at) if item.created_at is not None else None
                ),
            }
            for item in round_.web_requests
        ],
        # 是否允许后续申请自动批准（老行没有 → False）
        "allow_web": round_.allow_web,
        # 这一轮攒下的记忆提议（老行没有这把键 → 空：那时候还没有记忆这回事）
        "memory_proposals": [
            {
                "proposal_id": item.proposal_id,
                "change": _memory_change_to_json(item.change),
            }
            for item in round_.memory_proposals
        ],
        # 确认时用户勾了哪几条记忆提议（老行 → 空，不是 None）
        "accepted_memory_ids": list(round_.accepted_memory_ids),
        # 模拟调研产物（老行没有这几把键 → 空/0：那时候还没跑过模拟）
        "sim_slices": [dict(item) for item in round_.sim_slices],
        "sim_hypotheses": [dict(item) for item in round_.sim_hypotheses],
        "sim_unknowns": [dict(item) for item in round_.sim_unknowns],
        "sim_inquiry_runs": round_.sim_inquiry_runs,
        "sim_inquiry_chosen": list(round_.sim_inquiry_chosen),
        # 模拟评测产物（M11；老行没有这两把键 → None/0）
        "sim_review": dict(round_.sim_review) if round_.sim_review is not None else None,
        "sim_review_runs": round_.sim_review_runs,
    }
    # `None` = 旧 payload / 还没执行过联网：不写这个键，读侧回退"全部旧 web refs"。
    if round_.web_selection is not None:
        payload["web_selection"] = [
            [material_id, chunk_id] for material_id, chunk_id in round_.web_selection
        ]
    return payload


def round_from_payload(
    payload: dict,
    *,
    round_id: RoundId,
    project_id: ProjectId,
    created_at: datetime,
    step: Step,
    waiting_on: WaitingOn,
) -> Round:
    questionnaire = (
        None
        if payload["questionnaire"] is None
        else questionnaire_from_json(payload["questionnaire"])
    )
    web_requests, legacy_web_drops = _web_requests_from_payload(payload, step=step)
    web_selection = (
        None
        if payload.get("web_selection") is None
        else tuple(
            (str(pair[0]), str(pair[1])) for pair in payload["web_selection"]
        )
    )
    return Round(
        round_id=round_id,
        project_id=project_id,
        user_input=payload["user_input"],
        # 老行没有这个键：回落空串（分析者的"读了什么"是后加的）
        analysis_basis=payload.get("analysis_basis", ""),
        created_at=created_at,
        step=step,
        waiting_on=waiting_on,
        claims=tuple(claim_from_json(item) for item in payload["claims"]),
        seats=tuple(payload["seats"]),
        active_seats=tuple(payload.get("active_seats") or payload["seats"]),
        # 老行里没有这个键（跳过席位是后加的，§11.0.67）：空——谁都不跳过
        skipped_seats=tuple(str(item) for item in payload.get("skipped_seats") or ()),
        # 老行里没有这个键（席位提名是后加的）：给空元组，读回来照样能跑
        nominated_seats=tuple(
            SeatNomination(seat_id=item["seat_id"], why=item["why"])
            for item in payload.get("nominated_seats", ())
        ),
        seat_reviews=tuple(review_from_json(item) for item in payload["seat_reviews"]),
        questionnaire=questionnaire,
        # 老行里没有这个键（问卷历史是后加的）：构造器会回落到"只有当前这一批"
        questionnaire_history=tuple(
            questionnaire_from_json(item) for item in payload.get("questionnaire_history", ())
        ),
        # 老行里没有这个键（清单项改成轮次累积是后加的）：回落到"当轮问卷记下的那批"
        list_items=(
            tuple(_list_item_from_json(item) for item in payload["list_items"])
            if "list_items" in payload
            else (() if questionnaire is None else questionnaire.list_items)
        ),
        # 老行里没有这个键（互斥对子是后加的）：空——不编对子
        contradictions=tuple(
            Contradiction(**item) for item in payload.get("contradictions", ())
        ),
        # 老行里没有这个键（重试提示是后加的）：None——不编提示
        retry_hint=payload.get("retry_hint"),
        # 老行里没有这两把键（C4 的计数是后加的）：None——不编 0
        proposed_offered=payload.get("proposed_offered"),
        proposed_accepted=payload.get("proposed_accepted"),
        # 老行里没有这个键（C4 的 streak 是后加的）：0——不提示
        proposal_ignore_streak=payload.get("proposal_ignore_streak") or 0,
        # 老行里没有这个键（dig 的额度是后加的）：0
        extra_batches=payload.get("extra_batches") or 0,
        integration_seq=payload.get("integration_seq") or 0,
        extra_seat_rounds=payload.get("extra_seat_rounds") or 0,
        materials=tuple(
            MaterialRef(
                material_id=item["material_id"],
                chunk_id=item["chunk_id"],
                eid=EvidenceId(item["eid"]),
            )
            for item in payload.get("materials") or ()
        ),
        # 老行没有这把键（来源卡是 P3b-1 后加的）：空——不编"它挂过"
        research_sources=tuple(
            RoundResearchSource(
                eid=EvidenceId(str(item["eid"])),
                title=str(item["title"]),
                url=str(item["url"]),
                excerpt=str(item["excerpt"]),
            )
            for item in payload.get("research_sources") or ()
        ),
        # 老行没有这把键（检索 trace 是 P0 后加的）：空——不编"它查过"
        retrievals=tuple(
            RetrievalTrace(
                stage=RetrievalStage(str(item["stage"])),
                pass_index=int(item["pass_index"]),
                query=str(item.get("query") or ""),
                hits=tuple(
                    RetrievalHit(
                        material_id=str(hit["material_id"]),
                        chunk_id=str(hit["chunk_id"]),
                        score=float(hit.get("score") or 0.0),
                    )
                    for hit in item.get("hits") or ()
                ),
                selected=tuple(
                    RetrievalHit(
                        material_id=str(hit["material_id"]),
                        chunk_id=str(hit["chunk_id"]),
                        score=float(hit.get("score") or 0.0),
                    )
                    for hit in item.get("selected") or ()
                ),
                ok=bool(item.get("ok", True)),
                error=item.get("error"),
                created_at=(
                    _unwhen(item["created_at"]) if item.get("created_at") else None
                ),
            )
            for item in payload.get("retrievals") or ()
            if isinstance(item, dict)
        ),
        pinned_materials=tuple(
            str(item) for item in payload.get("pinned_materials") or ()
        ),
        manual_chunks=tuple(
            ManualChunk(
                material_id=str(item["material_id"]),
                chunk_id=str(item["chunk_id"]),
            )
            for item in payload.get("manual_chunks") or ()
            if isinstance(item, dict)
        ),
        unreferenced_materials=tuple(
            str(item) for item in payload.get("unreferenced_materials") or ()
        ),
        # 老行里没有这把键（调研种子是后加的，M8）：空——不编"它从探索来过"。
        # 老行里的种子只有探索两键（exploration_id/candidate_id）→ 回落成探索来源。
        seeds=tuple(
            SeedRef(
                origin=str(item.get("origin") or "exploration"),
                ref_id=str(item.get("ref_id") or item.get("exploration_id") or ""),
                item_id=str(item.get("item_id") or item.get("candidate_id") or ""),
            )
            for item in payload.get("seeds") or ()
        ),
        # 三方结构化联网申请；老行没有这个键 → 按 step 从旧两把检索词键迁移（不重跑已执行的）
        web_requests=web_requests,
        allow_web=bool(payload.get("allow_web", False)),
        web_selection=web_selection,
        answer_sets=tuple(answer_set_from_json(item) for item in payload["answer_sets"]),
        draft=None if payload["draft"] is None else draft_from_json(payload["draft"]),
        seat_round_count=payload["seat_round_count"],
        authorized_new_features={
            FeatureSlug(slug): EvidenceId(eid)
            for slug, eid in payload["authorized_new_features"].items()
        },
        dropped=_dropped_from_payload(payload) + legacy_web_drops,
        failure=_failure_from_json(payload["failure"]),
        confirmed_version_no=payload["confirmed_version_no"],
        # 老行里没有这个键（放弃是后加的）
        abandon_reason=payload.get("abandon_reason"),
        # 老行里没有这个键（轮内未决队列是后加的）
        deferred=tuple(
            _question_from_json(item) for item in payload.get("deferred_questions", ())
        ),
        carried_sources=dict(payload.get("carried_sources") or {}),
        # 老行没有这两把键（记忆是后加的）：空——不编"它攒过记忆"
        memory_proposals=tuple(
            MemoryProposal(
                proposal_id=str(item["proposal_id"]),
                change=change,
            )
            for item in payload.get("memory_proposals") or ()
            if isinstance(item, dict)
            and (change := _memory_change_from_json(item.get("change"))) is not None
        ),
        accepted_memory_ids=tuple(
            str(item) for item in payload.get("accepted_memory_ids") or ()
        ),
        # 老行里没有这几把键（模拟调研是后加的，M10）：空/0——不编"它跑过模拟"
        sim_slices=tuple(dict(item) for item in payload.get("sim_slices") or ()),
        sim_hypotheses=tuple(dict(item) for item in payload.get("sim_hypotheses") or ()),
        sim_unknowns=tuple(dict(item) for item in payload.get("sim_unknowns") or ()),
        sim_inquiry_runs=int(payload.get("sim_inquiry_runs") or 0),
        sim_inquiry_chosen=tuple(
            str(item) for item in payload.get("sim_inquiry_chosen") or ()
        ),
        # 老行里没有这两把键（模拟评测是后加的，M11）：None/0——不编"它跑过评测"
        sim_review=dict(payload["sim_review"]) if payload.get("sim_review") else None,
        sim_review_runs=int(payload.get("sim_review_runs") or 0),
    )


def _web_requests_from_payload(
    payload: dict, *, step: Step
) -> tuple[tuple[WebRequest, ...], tuple[DroppedItem, ...]]:
    """联网申请：新行读 `web_requests`；老行按 step 从旧两把检索词键迁移。

    旧语义只有"分析者提名 + 用户勾选"，所以迁移出来的全是 analyzer 申请：

    - 停在复述卡（`claims_ready`）：勾中的是 approved（还没执行）；
    - 已经走过复述卡：勾中的是 done（**不重跑**），没勾的是 proposed。

    未提名的勾选补一条 analyzer 申请；补完仍按每方 3 条截断，余下 TOOL 留痕
    （旧版 UI 允许勾一条分析者没提名的词，不补就等于把用户的选择静默丢掉）。

    **截断先保 chosen、再填未勾选提案**：名额不够时丢的是没被用户勾过的旧提案，
    不是用户真的点过的选择。drop 文案据此分成"未勾选提案"与"未提名勾选"两类。
    """

    if "web_requests" in payload:
        return (
            tuple(
                WebRequest(
                    request_id=str(item["request_id"]),
                    query=str(item["query"]),
                    why=str(item["why"]),
                    origin=str(item["origin"]),
                    seat_id=item.get("seat_id"),
                    pass_index=item.get("pass_index"),
                    state=WebRequestState(str(item["state"])),
                    result_note=item.get("result_note"),
                    created_at=(
                        _unwhen(item["created_at"])
                        if item.get("created_at")
                        else None
                    ),
                )
                for item in payload.get("web_requests") or ()
                if isinstance(item, dict)
            ),
            (),
        )

    chosen_queries: list[str] = []
    chosen_keys: set[str] = set()
    for raw in payload.get("web_queries_chosen") or ():
        query = str(raw).strip()
        if not query:
            continue
        key = normalized_query(query)
        if key in chosen_keys:
            continue
        chosen_keys.add(key)
        chosen_queries.append(query)

    declared: dict[str, tuple[str, str]] = {}
    for item in payload.get("web_queries") or ():
        query = str(item["query"])
        declared.setdefault(normalized_query(query), (query, str(item["why"])))

    chosen_state = (
        WebRequestState.APPROVED
        if step is Step.CLAIMS_READY
        else WebRequestState.DONE
    )
    # 顺序：先保用户真的勾过的（含未提名补的），再填没勾的旧提案。
    # 名额不够时先丢未勾选提案，而不是把用户的选择挤掉。
    ordered: list[tuple[str, str, WebRequestState, bool]] = []
    for query in chosen_queries:
        key = normalized_query(query)
        declared_query, why = declared.get(key, (query, LEGACY_REQUEST_WHY))
        ordered.append((declared_query, why, chosen_state, key in declared))
    for key, (query, why) in declared.items():
        if key in chosen_keys:
            continue
        ordered.append((query, why, WebRequestState.PROPOSED, True))

    kept = ordered[:MAX_WEB_QUERIES]
    requests = tuple(
        WebRequest(
            request_id=f"w{index + 1}",
            query=query,
            why=why,
            origin="analyzer",
            state=state,
        )
        for index, (query, why, state, _nominated) in enumerate(kept)
    )
    if len(ordered) <= MAX_WEB_QUERIES:
        return requests, ()

    pass_index = int(payload.get("seat_round_count") or 1)
    drops: list[DroppedItem] = []
    for query, _why, state, nominated in ordered[MAX_WEB_QUERIES:]:
        if state is WebRequestState.PROPOSED:
            reason = f"每方最多 {MAX_WEB_QUERIES} 条提案，未勾选提案没进本轮"
        elif not nominated:
            reason = f"每方最多 {MAX_WEB_QUERIES} 条提案，未提名勾选没进本轮"
        else:
            reason = f"每方最多 {MAX_WEB_QUERIES} 条提案，旧版勾选（已提名）没进本轮"
        drops.append(
            DroppedItem(
                handle=f"联网申请 {query}",
                reason=reason,
                source=DropSource.TOOL,
                seat_id=None,
                pass_index=pass_index,
            )
        )
    return requests, tuple(drops)


def _dropped_from_payload(payload: dict) -> tuple[DroppedItem, ...]:
    """留痕：新行读 `dropped`；老行读问卷上那串裸字符串，迁移成 `assembly` 条目。

    老行里没有新键（留痕是后加的），但**不许静默丢历史**——用户过两天回来还要查得到。
    """

    items = payload.get("dropped")
    if items:
        return tuple(
            DroppedItem(
                handle=item["handle"],
                reason=item["reason"],
                source=DropSource(item["source"]),
                seat_id=item.get("seat_id"),
                pass_index=int(item.get("pass_index", 1)),
                packet_id=item.get("packet_id"),
            )
            for item in items
        )
    questionnaire = payload.get("questionnaire") or {}
    pass_index = int(payload.get("seat_round_count", 1)) or 1
    return tuple(
        DroppedItem(
            handle=str(handle),
            reason="归因对不上号（旧记录：只留了地址，没留原因）",
            source=DropSource.ASSEMBLY,
            seat_id=None,
            pass_index=pass_index,
        )
        for handle in questionnaire.get("dropped", ())
    )


# —— 跨轮记忆（M6）——
def memory_to_payload(memory: ProjectMemory) -> dict:
    return {
        "entries": [
            {
                "memory_id": item.memory_id,
                "kind": item.kind.value,
                "text": item.text,
                "refs": _refs(item.refs),
                "round_id": item.round_id,
                "created_at": _when(item.created_at),
                "updated_at": _when(item.updated_at),
            }
            for item in memory.entries
        ],
        "revisions": [
            {
                "revision_no": revision.revision_no,
                "round_id": revision.round_id,
                "changes": [_memory_change_to_json(change) for change in revision.changes],
                "created_at": _when(revision.created_at),
            }
            for revision in memory.revisions
        ],
    }


def memory_from_payload(payload: object, *, project_id: ProjectId) -> ProjectMemory:
    """坏行**跳过不抛**：手工改坏的 payload 不该让整个项目打不开（与索引层同一条口径）。

    条目的形状校验（必须有 id / 正文 / 出处）在 `MemoryEntry.__post_init__` 里，
    这里只负责把认不出来的那些挑出去；`kind` 认不出来也算"认不出来"。
    """

    if not isinstance(payload, dict):
        return ProjectMemory.empty(project_id)
    entries = [
        entry
        for item in _memory_rows(payload.get("entries"))
        if (entry := _memory_entry(item)) is not None
    ]
    revisions = [
        revision
        for item in _memory_rows(payload.get("revisions"))
        if (revision := _memory_revision(item)) is not None
    ]
    return ProjectMemory(
        project_id=project_id, entries=tuple(entries), revisions=tuple(revisions)
    )


def _memory_rows(value: object) -> list:
    return list(value) if isinstance(value, list) else []


def _memory_entry(item: object) -> MemoryEntry | None:
    if not isinstance(item, dict):
        return None
    try:
        return MemoryEntry(
            memory_id=str(item["memory_id"]),
            kind=MemoryKind(str(item["kind"])),
            text=str(item["text"]),
            refs=tuple(EvidenceId(str(ref)) for ref in item.get("refs") or ()),
            round_id=str(item["round_id"]),
            created_at=_unwhen(str(item["created_at"])),
            updated_at=_unwhen(str(item["updated_at"])),
        )
    except (KeyError, TypeError, ValueError, DomainError):
        return None


def _memory_revision(item: object) -> MemoryRevision | None:
    if not isinstance(item, dict):
        return None
    changes = [
        change
        for raw in _memory_rows(item.get("changes"))
        if (change := _memory_change_from_json(raw)) is not None
    ]
    try:
        return MemoryRevision(
            revision_no=int(item["revision_no"]),
            round_id=str(item["round_id"]),
            changes=tuple(changes),
            created_at=_unwhen(str(item["created_at"])),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _memory_change_from_json(item: object) -> MemoryChange | None:
    if not isinstance(item, dict):
        return None
    try:
        return MemoryChange(
            op=MemoryOp(str(item["op"])),
            kind=MemoryKind(str(item["kind"])),
            text=str(item.get("text") or ""),
            refs=tuple(str(ref) for ref in item.get("refs") or ()),
            target_id=None if item.get("target_id") is None else str(item["target_id"]),
            reason=str(item.get("reason") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _memory_change_to_json(change: MemoryChange) -> dict:
    return {
        "op": change.op.value,
        "kind": change.kind.value,
        "text": change.text,
        "refs": list(change.refs),
        "target_id": change.target_id,
        "reason": change.reason,
    }


# ── 探索（M8）────────────────────────────────────────────────────────────────


def exploration_to_payload(exploration: Exploration) -> dict:
    """探索的 payload。列字段（kind / status / created_at）不放这里——读时从列传回来。"""

    candidates = {item.candidate_id: item for item in exploration.candidates()}
    return {
        "scope": {
            "kind": exploration.scope.kind.value,
            "ref": exploration.scope.ref,
            "text": exploration.scope.text,
        },
        "hypotheses": [
            {
                "local_id": item.local_id,
                "statement": item.statement,
                "grounding": item.grounding.value,
                "based_on": [str(eid) for eid in item.based_on],
                "falsification": item.falsification,
                "evidence_needed": item.evidence_needed,
            }
            for item in exploration.hypotheses
        ],
        "blind_spots": [
            {
                "local_id": item.local_id,
                "statement": item.statement,
                "next_step": item.next_step,
            }
            for item in exploration.blind_spots
        ],
        # 候选状态只存"非 new"的（new 是默认值，存了只是噪声）
        "states": {
            candidate_id: item.state.value
            for candidate_id, item in candidates.items()
            if item.state is not CandidateState.NEW
        },
        "kept": {
            candidate_id: item.kept_as
            for candidate_id, item in candidates.items()
            if item.kept_as is not None
        },
        "kept_ref": {
            candidate_id: item.kept_ref
            for candidate_id, item in candidates.items()
            if item.kept_ref is not None
        },
        "failure": (
            None
            if exploration.failure is None
            else {"reason": exploration.failure.reason}
        ),
        "abandon_reason": exploration.abandon_reason,
        "overflow_count": exploration.overflow_count,
        # 研究的产物（P3b-1）。调研那一路恒为 None——**不写一把空键当占位**，
        # 老行缺这把键的读法就是 None，两者在读侧同一件事。
        "research": _research_to_payload(exploration.research),
    }


def _research_to_payload(research: ResearchReport | None) -> dict | None:
    if research is None:
        return None
    return {
        "answer": research.answer,
        "answer_refs": [str(ref) for ref in research.answer_refs],
        "findings": [
            {
                "finding_id": item.finding_id,
                "statement": item.statement,
                "refs": [str(ref) for ref in item.refs],
            }
            for item in research.findings
        ],
        "sources": [
            {
                "eid": str(item.eid),
                "material_id": item.material_id,
                "title": item.title,
                "url": item.url,
                "excerpt": item.excerpt,
            }
            for item in research.sources
        ],
        "open_questions": list(research.open_questions),
        "notes": list(research.notes),
    }


def _research_from_payload(data: object) -> ResearchReport | None:
    """`None` 只表示"这把键不存在"（老行）。**非 dict / 字段坏了不再当成 None**：
    仓储的坏行口径是 `except (AttributeError, TypeError, KeyError, ValueError)`，
    把坏数据翻成 `ValueError` 才能让那一行被跳过，而不是把整页列表打成 500。"""

    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError(f"research 不是对象：{type(data).__name__}")
    try:
        return _build_research(data)
    except DomainError as error:
        raise ValueError(str(error)) from error


def _build_research(data: dict) -> ResearchReport | None:
    findings = tuple(
        ResearchFinding(
            finding_id=str(item.get("finding_id") or ""),
            statement=str(item.get("statement") or ""),
            refs=tuple(EvidenceId(str(ref)) for ref in item.get("refs") or ()),
        )
        for item in data.get("findings") or ()
    )
    sources = tuple(
        ResearchSource(
            eid=EvidenceId(str(item["eid"])),
            material_id=str(item.get("material_id") or ""),
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            excerpt=str(item.get("excerpt") or ""),
        )
        for item in data.get("sources") or ()
    )
    if "answer" in data:
        answer = str(data.get("answer") or "")
        answer_refs = tuple(
            EvidenceId(str(ref)) for ref in data.get("answer_refs") or ()
        )
    elif findings:
        # S4 之前的报告没有 answer：用第一条 finding 重建，旧行不丢。
        answer = findings[0].statement
        answer_refs = findings[0].refs
    elif sources:
        answer = sources[0].excerpt or sources[0].title
        answer_refs = (sources[0].eid,)
    else:
        return None
    return ResearchReport(
        answer=answer,
        answer_refs=answer_refs,
        findings=findings,
        sources=sources,
        open_questions=tuple(str(item) for item in data.get("open_questions") or ()),
        notes=tuple(str(item) for item in data.get("notes") or ()),
    )


def exploration_from_payload(
    payload: dict,
    *,
    exploration_id: ExplorationId,
    project_id: ProjectId,
    kind: ExplorationKind,
    status: ExplorationStatus,
    created_at: datetime,
) -> Exploration:
    scope_data = payload.get("scope") or {}
    failure_data = payload.get("failure")
    # 聚合守卫（kind⇔scope、done 的研究必须有报告）抛的是 `DomainError`，
    # 而仓储的坏行网只接 `(AttributeError, TypeError, KeyError, ValueError)`
    # ——不在这里翻一手，一行坏 payload 就会让整页列表 500 / 让 clear_stale 永远 422。
    try:
        return _exploration_from_parts(
            payload,
            exploration_id=exploration_id,
            project_id=project_id,
            kind=kind,
            status=status,
            created_at=created_at,
            scope_data=scope_data,
            failure_data=failure_data,
        )
    except DomainError as error:
        raise ValueError(str(error)) from error


def _exploration_from_parts(
    payload: dict,
    *,
    exploration_id: ExplorationId,
    project_id: ProjectId,
    kind: ExplorationKind,
    status: ExplorationStatus,
    created_at: datetime,
    scope_data: dict,
    failure_data: dict | None,
) -> Exploration:
    return Exploration(
        exploration_id=exploration_id,
        project_id=project_id,
        kind=kind,
        status=status,
        created_at=created_at,
        scope=ExplorationScope(
            kind=ScopeKind(str(scope_data.get("kind") or "document")),
            ref=scope_data.get("ref"),
            text=scope_data.get("text"),
        ),
        hypotheses=tuple(
            Hypothesis(
                local_id=str(item["local_id"]),
                statement=str(item["statement"]),
                grounding=Grounding(str(item["grounding"])),
                based_on=tuple(EvidenceId(str(eid)) for eid in item.get("based_on") or ()),
                falsification=str(item["falsification"]),
                evidence_needed=str(item["evidence_needed"]),
            )
            for item in payload.get("hypotheses") or ()
        ),
        blind_spots=tuple(
            BlindSpot(
                local_id=str(item["local_id"]),
                statement=str(item["statement"]),
                next_step=str(item["next_step"]),
            )
            for item in payload.get("blind_spots") or ()
        ),
        states={
            str(key): CandidateState(str(value))
            for key, value in (payload.get("states") or {}).items()
        },
        kept={str(key): str(value) for key, value in (payload.get("kept") or {}).items()},
        kept_ref={
            str(key): str(value) for key, value in (payload.get("kept_ref") or {}).items()
        },
        failure=(
            None
            if not failure_data
            else ExplorationFailure(reason=str(failure_data["reason"]))
        ),
        abandon_reason=payload.get("abandon_reason"),
        overflow_count=int(payload.get("overflow_count") or 0),
        research=_research_from_payload(payload.get("research")),
    )


# ── 讨论（M9）────────────────────────────────────────────────────────────────


def discussion_to_payload(discussion: Discussion) -> dict:
    """讨论的 payload。列字段（status / created_at）不放这里——读时从列传回来。

    M18 起 `role_id`/`role_title` 不再写（首位成员即旧值，读侧照旧能拿到）；
    只写 `participants`。老行没有这把键 → 读侧回落单成员（M12 稀疏映射同纪律）。
    """

    question = discussion.question
    return {
        "topic": discussion.topic,
        "participants": [
            {
                "role_id": member.role_id,
                "role_title": member.role_title,
                "joined_round": member.joined_round,
            }
            for member in discussion.participants
        ],
        "question": (
            None
            if question is None
            else {"title": question.title, "why": question.why}
        ),
        # 开题调研底稿（M13）：只存引用；老行没有这把键 → 读侧回落空
        "findings": [
            {"exploration_id": ref.exploration_id, "local_id": ref.local_id}
            for ref in discussion.findings
        ],
        "turns": [
            {
                "index": turn.index,
                "role": turn.role,
                "text": turn.text,
                "based_on": [str(eid) for eid in turn.based_on],
                "author": turn.author,
                "round": turn.round,
            }
            for turn in discussion.turns
        ],
        "summaries": [
            {
                "index": summary.index,
                "agreed": list(summary.agreed),
                "disagreed": list(summary.disagreed),
                "open": list(summary.open),
            }
            for summary in discussion.summaries
        ],
        "cards": [
            {
                "card_id": card.card_id,
                "statement": card.statement,
                "grounding": card.grounding.value,
                "based_on": [str(eid) for eid in card.based_on],
            }
            for card in discussion.cards
        ],
        # 过目状态只存"非 new"的（new 是默认值，存了只是噪声）——照探索的稀疏映射口径。
        # 老键 `kept`（card_id → material_id）自 M12 起不再写，只在读侧兼容老行。
        "states": {
            card.card_id: card.state.value
            for card in discussion.cards
            if card.state is not CardState.NEW
        },
        "kept_as": {
            card.card_id: card.kept_as
            for card in discussion.cards
            if card.kept_as is not None
        },
        "kept_ref": {
            card.card_id: card.kept_ref
            for card in discussion.cards
            if card.kept_ref is not None
        },
    }


def discussion_from_payload(
    payload: dict,
    *,
    discussion_id: DiscussionId,
    project_id: ProjectId,
    status: ThreadStatus,
    created_at: datetime,
) -> Discussion:
    question_data = payload.get("question")
    # 过目状态的两种存储形状（M12）：新行有 states/kept_as/kept_ref 三张稀疏映射；
    # 老行（M9）只有 `kept`（card_id → material_id）——读回来就是"已收下为材料"。
    new_state_shape = any(
        key in payload for key in ("states", "kept_as", "kept_ref")
    )
    states = payload.get("states") or {}
    kept_as = payload.get("kept_as") or {}
    kept_refs = payload.get("kept_ref") or {}
    legacy_kept = payload.get("kept") or {}
    cards: list[Card] = []
    for item in payload.get("cards") or ():
        card_id = str(item["card_id"])
        if new_state_shape:
            state = CardState(str(states.get(card_id) or CardState.NEW.value))
            kept = kept_as.get(card_id)
            ref = kept_refs.get(card_id)
        else:
            ref = legacy_kept.get(card_id)
            state = CardState.KEPT if ref is not None else CardState.NEW
            kept = "material" if ref is not None else None
        cards.append(
            Card(
                card_id=card_id,
                statement=str(item["statement"]),
                grounding=DiscussionGrounding(str(item["grounding"])),
                based_on=tuple(EvidenceId(str(eid)) for eid in item.get("based_on") or ()),
                kept_ref=ref,
                state=state,
                kept_as=kept,
            )
        )
    return Discussion(
        discussion_id=discussion_id,
        project_id=project_id,
        topic=str(payload.get("topic") or ""),
        participants=_participants_of(payload),
        created_at=created_at,
        question=(
            None
            if not question_data
            else QuestionSnapshot(
                title=str(question_data.get("title") or ""),
                why=question_data.get("why"),
            )
        ),
        status=status,
        turns=tuple(
            Turn(
                index=int(item["index"]),
                role=str(item["role"]),
                text=str(item["text"]),
                based_on=tuple(EvidenceId(str(eid)) for eid in item.get("based_on") or ()),
                author=str(item.get("author") or "assistant"),
                round=int(item.get("round") or 1),
            )
            for item in payload.get("turns") or ()
        ),
        cards=tuple(cards),
        # 老行里没有这把键（开题调研底稿是后加的，M13）：空——不编"它带过底稿"
        findings=tuple(
            FindingRef(
                exploration_id=str(item["exploration_id"]),
                local_id=str(item["local_id"]),
            )
            for item in payload.get("findings") or ()
        ),
        # 老行里没有这两把键（M18 panel）：综合缺省空
        summaries=tuple(
            RoundSummary(
                index=int(item["index"]),
                agreed=tuple(str(line) for line in item.get("agreed") or ()),
                disagreed=tuple(str(line) for line in item.get("disagreed") or ()),
                open=tuple(str(line) for line in item.get("open") or ()),
            )
            for item in payload.get("summaries") or ()
        ),
    )


def _participants_of(payload: dict) -> tuple[Participant, ...]:
    """读 panel 成员：新行读 `participants`；M9 老行只有 `role_id`/`role_title` → 单成员回落。

    空 participants 在领域层是非法的（start 要求 1–4 个），所以回落分支兜住空串，
    保证读回来的聚合永远合法——坏行不该在读侧炸（坏行跳过是仓储层的职责）。
    """

    stored = payload.get("participants")
    if isinstance(stored, list) and stored:
        return tuple(
            Participant(
                role_id=str(item["role_id"]),
                role_title=str(item.get("role_title") or ""),
                joined_round=int(item.get("joined_round") or 1),
            )
            for item in stored
        )
    return (
        Participant(
            role_id=str(payload.get("role_id") or ""),
            role_title=str(payload.get("role_title") or ""),
            joined_round=1,
        ),
    )
