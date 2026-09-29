"""领域对象 ↔ JSON。往返测试是这一层的验收方式。

清一色的手写映射，故意不用反射：反射在枚举 / NewType / 可选字段上会静默出错，
而这里的错会直接变成"文档读出来少了一个字段"。
"""

from datetime import datetime

from pmstudio.domain.authoring.blocks import Block, Entry
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.version import Snapshot, Version, VersionTrigger
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
from pmstudio.domain.round.round import DroppedItem, DropSource, Failure, Round, SeatNomination
from pmstudio.domain.round.step import Step, WaitingOn
from pmstudio.domain.shared.evidence_kind import EvidenceKind
from pmstudio.domain.shared.ids import (
    BlockId,
    DocumentId,
    DraftId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
    SectionId,
)
from pmstudio.domain.shared.label import ClaimLabel, Zone
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
    }


def review_from_json(data: dict) -> SeatReview:
    return SeatReview(
        seat_id=data["seat_id"],
        basis=data["basis"],
        opinions=tuple(_opinion_from_json(item) for item in data["opinions"]),
        pass_index=data["pass_index"],
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
        "failure": _failure_to_json(round_.failure),
        "confirmed_version_no": round_.confirmed_version_no,
        "abandon_reason": round_.abandon_reason,
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
