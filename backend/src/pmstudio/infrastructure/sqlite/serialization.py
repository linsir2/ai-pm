"""领域对象 ↔ JSON。往返测试是这一层的验收方式。

清一色的手写映射，故意不用反射：反射在枚举 / NewType / 可选字段上会静默出错，
而这里的错会直接变成"文档读出来少了一个字段"。
"""

from datetime import datetime

from pmstudio.domain.authoring.blocks import Block, Entry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.version import Snapshot, Version, VersionTrigger
from pmstudio.domain.discussion.card import Card
from pmstudio.domain.discussion.card import Grounding as DiscussionGrounding
from pmstudio.domain.discussion.thread import (
    Discussion,
    QuestionSnapshot,
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
from pmstudio.domain.round.web_query import WebQuery
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
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp


def _when(value: datetime) -> str:
    return value.isoformat()


def _unwhen(value: str) -> datetime:
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


def document_to_row(document: Document, *, now: datetime) -> dict:
    return {
        "project_id": str(document.project_id),
        "document_id": str(document.document_id),
        "title": document.title,
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
    return {
        "user_input": round_.user_input,
        "claims": [claim_to_json(claim) for claim in round_.claims],
        "seats": list(round_.seats),
        # 这一趟实际跑的名单（缩编结果）；老行没有这个键，读回落回 seats
        "active_seats": list(round_.active_seats),
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
                "material_id": item.material_id,
                "chunk_id": item.chunk_id,
                "eid": str(item.eid),
            }
            for item in round_.materials
        ],
        # 这一轮从过目台带进来的调研产物（老行没有这把键 → 读侧回落空，M8）
        "seeds": [
            {"exploration_id": item.exploration_id, "candidate_id": item.candidate_id}
            for item in round_.seeds
        ],
        # 分析者提名的检索词（老行没有这把键 → 读侧回落空）
        "web_queries": [
            {"query": item.query, "why": item.why} for item in round_.web_queries
        ],
        # 用户在复述卡上勾中的（老行没有这把键 → 空 = 不联网）
        "web_queries_chosen": list(round_.web_queries_chosen),
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
    return Round(
        round_id=round_id,
        project_id=project_id,
        user_input=payload["user_input"],
        created_at=created_at,
        step=step,
        waiting_on=waiting_on,
        claims=tuple(claim_from_json(item) for item in payload["claims"]),
        seats=tuple(payload["seats"]),
        active_seats=tuple(payload.get("active_seats") or payload["seats"]),
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
        # 老行里没有这把键（调研种子是后加的，M8）：空——不编"它从探索来过"
        seeds=tuple(
            SeedRef(
                exploration_id=str(item["exploration_id"]),
                candidate_id=str(item["candidate_id"]),
            )
            for item in payload.get("seeds") or ()
        ),
        # 老行里没有这两把键（联网检索词是后加的）：空——不联网、也不编"它建议查过"
        web_queries=tuple(
            WebQuery(query=str(item["query"]), why=str(item["why"]))
            for item in payload.get("web_queries") or ()
        ),
        web_queries_chosen=tuple(str(item) for item in payload.get("web_queries_chosen") or ()),
        answer_sets=tuple(answer_set_from_json(item) for item in payload["answer_sets"]),
        draft=None if payload["draft"] is None else draft_from_json(payload["draft"]),
        seat_round_count=payload["seat_round_count"],
        authorized_new_features={
            FeatureSlug(slug): EvidenceId(eid)
            for slug, eid in payload["authorized_new_features"].items()
        },
        dropped=_dropped_from_payload(payload),
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
    }


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
    )


# ── 讨论（M9）────────────────────────────────────────────────────────────────


def discussion_to_payload(discussion: Discussion) -> dict:
    """讨论的 payload。列字段（status / created_at）不放这里——读时从列传回来。"""

    question = discussion.question
    return {
        "topic": discussion.topic,
        "role_id": discussion.role_id,
        "role_title": discussion.role_title,
        "question": (
            None
            if question is None
            else {"title": question.title, "why": question.why}
        ),
        "turns": [
            {
                "index": turn.index,
                "role": turn.role,
                "text": turn.text,
                "based_on": [str(eid) for eid in turn.based_on],
            }
            for turn in discussion.turns
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
        # 收下记录只存"已收下"的（kept_ref 是 None 就是没收下，存了只是噪声）
        "kept": {
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
    kept = payload.get("kept") or {}
    return Discussion(
        discussion_id=discussion_id,
        project_id=project_id,
        topic=str(payload.get("topic") or ""),
        role_id=str(payload.get("role_id") or ""),
        role_title=str(payload.get("role_title") or ""),
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
            )
            for item in payload.get("turns") or ()
        ),
        cards=tuple(
            Card(
                card_id=str(item["card_id"]),
                statement=str(item["statement"]),
                grounding=DiscussionGrounding(str(item["grounding"])),
                based_on=tuple(EvidenceId(str(eid)) for eid in item.get("based_on") or ()),
                kept_ref=kept.get(str(item["card_id"])),
            )
            for item in payload.get("cards") or ()
        ),
    )
