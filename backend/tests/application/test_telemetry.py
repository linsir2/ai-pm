"""消费遥测 v1 的纯函数 + 读库测试（RIPR：可达 / 感染 / 传播 / 可揭示）。

口径：`injected_by_slot` = 整合者这一轮看到的、按来源分槽的 E 编号；
      `coverage` = 每个槽的 (注入数, 被引用数)；引用 = 提案 refs ∪ 题目 refs。
"""

from datetime import UTC, datetime

from scripts.telemetry import (
    analyze_db,
    coverage,
    injected_by_slot,
    real_prompt_length,
)

from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.draft import Draft, Origin, OriginType, Proposal
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import MaterialRef, Round
from pmstudio.domain.shared.ids import (
    DraftId,
    EntryId,
    EvidenceId,
    FormId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from pmstudio.harness.model.prompt_log import PROMPT_LIMIT, TRUNCATION_MARK
from pmstudio.infrastructure.sqlite import database
from pmstudio.infrastructure.sqlite.repositories import (
    SqliteDocumentRepository,
    SqliteEvidenceRepository,
    SqliteRoundRepository,
)
from tests.support.builders import PROJECT, SEAT, make_claim, make_document

NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
ROUND = RoundId("r-test")


def _opinion(local_id: str) -> Opinion:
    return Opinion(
        local_id=local_id,
        form=OpinionForm.JUDGMENT,
        about="导出",
        claim="应支持导出",
        because="理由",
        evidence_refs=(EvidenceId("E1"),),
    )


def _proposal(*, refs) -> Proposal:
    return Proposal(
        proposal_id=ProposalId("p1"),
        entry_id=EntryId("r1#01"),
        op=WriteOp.ADD,
        target=Target.body("s5"),
        text="测试提案",
        origin=Origin(type=OriginType.USER, refs=tuple(EvidenceId(e) for e in refs)),
    )


def _question(qid: str, *, refs) -> Question:
    return Question(
        question_id=QuestionId(qid),
        target=Target.body("s5"),
        type=QuestionType.SINGLE,
        title="测试题",
        why="为什么问",
        consequence_if_skipped="跳过的后果",
        options=(Option("a", "A"), Option("b", "B")),
        evidence_refs=tuple(EvidenceId(e) for e in refs),
    )


def _ledger(*, answer: bool = False, opinion: bool = False) -> EvidenceLedger:
    """账本：user_input 恒有（E1）；可选 card_answer（E2）、agent_derived（E3）。"""

    ledger = EvidenceLedger.empty(PROJECT)
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="测试输入",
        source=EvidenceSource(round_id=ROUND, raw_span="测试输入"),
        now=NOW,
    )
    if answer:
        ledger.record(
            kind=EvidenceKind.CARD_ANSWER,
            text="答案",
            source=EvidenceSource(round_id=ROUND, question_id=QuestionId("q1")),
            now=NOW,
        )
    if opinion:
        ledger.record(
            kind=EvidenceKind.AGENT_DERIVED,
            text="应支持导出",
            source=EvidenceSource(
                seat_id=SEAT,
                local_id="p1:O1",
                round_id=ROUND,
                derived_from=(user,),
                rationale="理由",
            ),
            now=NOW,
        )
    return ledger


def _round_with_draft(
    *, proposal_refs=("E1",), question_refs=None, with_opinion: bool = True
) -> Round:
    """走到草案的一轮：主张 E1 + 材料 E5（+ 可选席位判断 / 一道被跳过的题）。"""

    round_ = Round.start(
        round_id=ROUND, project_id=PROJECT, user_input="测试", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    if with_opinion:
        round_.attest(
            SeatReview(seat_id=SEAT, basis="依据", opinions=(_opinion("O1"),))
        )
    round_.add_materials(
        (MaterialRef(material_id="m1", chunk_id="m1#c0", eid=EvidenceId("E5")),)
    )
    if question_refs is not None:
        round_.integrate(
            questionnaire=Questionnaire(
                form_id=FormId("f1"),
                round_id=ROUND,
                questions=(_question("q1", refs=question_refs),),
            )
        )
        round_.submit_answers(
            AnswerSet(
                form_id=FormId("f1"),
                answers=(),
                answered_at=NOW,
                skipped=(QuestionId("q1"),),
            )
        )
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"),
            round_id=ROUND,
            proposals=(_proposal(refs=proposal_refs),),
        )
    )
    return round_


def test_injected_slots_partition_by_source():
    """Reach：四类齐；Infect：若把意见引用的 refs 当 own EID，reviews 会混入别的东西。"""

    round_ = _round_with_draft(question_refs=None, with_opinion=True)
    ledger = _ledger(answer=True, opinion=True)

    injected = injected_by_slot(round_, ledger=ledger)

    assert injected["claims"] == frozenset({EvidenceId("E1")})
    assert injected["answers"] == frozenset({EvidenceId("E2")})
    assert injected["reviews"] == frozenset({EvidenceId("E3")})
    assert injected["materials"] == frozenset({EvidenceId("E5")})
    union = frozenset().union(*injected.values())
    assert sum(len(ids) for ids in injected.values()) == len(union)


def test_coverage_counts_cited_across_proposals_and_questions():
    """Reach：提案引 E1、题目引 E5；Infect：只看提案会漏掉题目那条引用。"""

    round_ = _round_with_draft(
        proposal_refs=("E1",), question_refs=("E5",), with_opinion=False
    )

    result = {
        c.slot: (c.injected, c.cited)
        for c in coverage(round_, ledger=EvidenceLedger.empty(PROJECT))
    }

    assert result == {
        "claims": (1, 1),
        "answers": (0, 0),
        "reviews": (0, 0),
        "materials": (1, 1),
    }


class TestRealPromptLength:
    """口径：`model_calls.prompt_text` 是留样截断后的，真实长度要把略去的 N 加回去。"""

    def test_untruncated_text_is_its_own_length(self):
        assert real_prompt_length("短提示词") == len("短提示词")

    def test_truncated_text_adds_back_the_omitted_characters(self):
        body = "x" * 100
        text = body + TRUNCATION_MARK.format(count=500) + body

        assert real_prompt_length(text) == len(body) * 2 + 500

    def test_full_house_truncation_adds_up(self):
        """留样是头尾各半 + 中间标记：还原后应超过 PROMPT_LIMIT。"""

        mark = TRUNCATION_MARK.format(count=4321)
        text = "a" * (PROMPT_LIMIT // 2) + mark + "b" * (PROMPT_LIMIT // 2)

        assert real_prompt_length(text) == len(text) - len(mark) + 4321
        assert real_prompt_length(text) > PROMPT_LIMIT


def test_opinion_without_ledger_entry_is_not_injected():
    """Infect：不过滤 find_by_local=None，会 AttributeError。"""

    round_ = _round_with_draft(question_refs=None, with_opinion=True)

    injected = injected_by_slot(round_, ledger=EvidenceLedger.empty(PROJECT))

    assert injected["reviews"] == frozenset()


def test_empty_slot_reports_zero_rate_without_div_zero():
    """Infect：cited/injected 不防 0 会 ZeroDivisionError。"""

    round_ = Round.start(
        round_id=ROUND, project_id=PROJECT, user_input="测试", now=NOW
    )
    round_.attach_claims((make_claim(eid=EvidenceId("E1")),))
    round_.submit_opening(seats=(SEAT,))
    round_.integrate(
        draft=Draft(
            draft_id=DraftId("d1"), round_id=ROUND, proposals=(_proposal(refs=("E1",)),)
        )
    )

    materials = {
        c.slot: c for c in coverage(round_, ledger=EvidenceLedger.empty(PROJECT))
    }["materials"]

    assert materials.injected == 0
    assert materials.cited == 0
    assert materials.rate == 0.0


def test_materials_slot_agrees_with_unreferenced_materials_for():
    """交叉核验：遥测的 materials 与生产函数同口径（injected - cited == 零引用数）。"""

    from pmstudio.application.use_cases import _unreferenced_materials_for

    round_ = _round_with_draft(
        proposal_refs=("E1",), question_refs=None, with_opinion=False
    )
    unreferenced = _unreferenced_materials_for(
        round_, proposals=round_.draft.proposals, questions=()
    )

    materials = {
        c.slot: c for c in coverage(round_, ledger=EvidenceLedger.empty(PROJECT))
    }["materials"]

    assert materials.injected - materials.cited == len(unreferenced)


def test_analyze_db_reads_a_real_round(tmp_path):
    """Reach：真实仓储写进临时库；Infect：连库/反序列化错则读不回来。"""

    db = tmp_path / "t.db"
    connection = database.connect(db)
    SqliteDocumentRepository(connection).save(make_document())  # 外键：轮/账本挂在文档上
    SqliteRoundRepository(connection).save(
        _round_with_draft(question_refs=None, with_opinion=True)
    )
    SqliteEvidenceRepository(connection).save(_ledger(answer=True, opinion=True))
    connection.commit()
    connection.close()

    measured, skipped = analyze_db(db)

    assert skipped == 0
    assert len(measured) == 1
    round_id, slots = measured[0]
    assert round_id == ROUND
    assert {c.slot for c in slots} == {"claims", "answers", "reviews", "materials"}
