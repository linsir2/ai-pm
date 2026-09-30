from datetime import datetime

from pmstudio.application import context
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim
from pmstudio.domain.round.opinion import Opinion, OpinionForm, SeatReview
from pmstudio.domain.round.questionnaire import (
    Answer,
    AnswerSet,
    Option,
    Question,
    Questionnaire,
    QuestionType,
)
from pmstudio.domain.round.round import Round
from pmstudio.domain.shared.ids import (
    DocumentId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProjectId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import (
    make_material_packet as make_packet,
)
from tests.support.builders import (
    make_round,
    make_round_with_contradictions,
)

NOW = datetime(2026, 9, 22, 10, 0)
PROJECT = ProjectId("p1")


def document_with_one_feature() -> Document:
    document = Document.new(
        document_id=DocumentId("doc1"), project_id=PROJECT, title="订单中台", now=NOW
    )
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id="en1",  # type: ignore[arg-type]
                op=WriteOp.ADD,
                target=Target.feature_field(FeatureSlug("order-export"), "one_liner"),
                text="把订单导出成表格",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="干跑",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )
    return document


def test_render_document_lists_thirteen_sections_and_marks_empty_ones():
    text = context.render_document(document_with_one_feature().view())
    assert text.count("## ") == 13
    assert "## s1 背景与问题" in text
    assert "（空）" in text
    assert "feature#order-export" in text
    assert "把订单导出成表格" in text
    assert "E1" in text


def test_render_claims_keeps_numbers_labels_targets_and_raw_span():
    claim = Claim(
        claim_id="c1",
        text="要做一个订单导出功能",
        label=ClaimLabel.NEED,
        target=Target.body("s5"),
        raw_span="想做个订单导出的功能",
        eid=EvidenceId("E1"),
    )
    text = context.render_claims((claim,))
    assert "[E1]" in text
    assert "需求" in text
    assert "s5" in text
    assert "想做个订单导出的功能" in text


def _review_with_two_opinions() -> SeatReview:
    return SeatReview(
        seat_id="seat.data_scale",
        basis="读了 s5 与 E1",
        opinions=(
            Opinion(
                local_id="O1",
                form=OpinionForm.QUESTION,
                about="导出的数据量级",
                claim="量级决定同步还是异步，必须问用户",
                because="1 万以内可同步，10 万以上必须异步",
                evidence_refs=(EvidenceId("E1"),),
            ),
            Opinion(
                local_id="O2",
                form=OpinionForm.JUDGMENT,
                about="导出的性能上限",
                claim="单次导出应在 60 秒内完成",
                because="量级在 1 万级，同步实现可满足",
                evidence_refs=(EvidenceId("E1"),),
            ),
        ),
        pass_index=2,
    )


def _ledger_with_judgment_numbered() -> EvidenceLedger:
    """挑一个判断进账本，让它拿到出处编号（形状与真 assigner 一致）。"""

    ledger = EvidenceLedger.empty(PROJECT)
    ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.data_scale",
            local_id="p2:O2",
            derived_from=(EvidenceId("E1"),),
            rationale="量级在 1 万级，同步实现可满足",
        ),
        now=NOW,
    )
    return ledger


def test_render_reviews_shows_forms_basis_and_refs():
    text = context.render_reviews(
        (_review_with_two_opinions(),),
        titles={"seat.data_scale": "数据规模与性能"},
        ledger=_ledger_with_judgment_numbered(),
    )
    assert "数据规模与性能" in text
    assert "疑问" in text
    assert "判断" in text
    assert "读了 s5 与 E1" in text


def test_render_reviews_names_seat_entries_without_pretending_they_are_evidence():
    """席位条目身份与出处编号必须分开写：`[O1]` 冒充编号，就是把模型带沟里的那一下。"""

    text = context.render_reviews(
        (_review_with_two_opinions(),),
        titles={"seat.data_scale": "数据规模与性能"},
        ledger=_ledger_with_judgment_numbered(),
    )
    assert "席位条目 seat.data_scale:O2" in text
    assert "席位条目 seat.data_scale:O1" in text
    assert "[O1]" not in text
    assert "[O2]" not in text
    # 判断自己有出处编号（E2），疑问没有——两种要分别写明
    assert "出处编号 E2" in text
    assert "没有出处编号" in text


def test_render_reviews_without_ledger_still_names_seat_entries():
    text = context.render_reviews(
        (_review_with_two_opinions(),), titles={"seat.data_scale": "数据规模与性能"}
    )
    assert "席位条目 seat.data_scale:O2" in text
    assert "出处编号" not in text


def test_render_readiness_has_hard_rule_and_severity_tags():
    text = context.render_readiness(
        (
            ReadinessItem(
                "s1",
                "section_min_entries",
                "背景与问题还缺 1 条内容",
                "至少 1 条：现状 / 问题 / 不做的代价",
                Severity.BLOCKER,
            ),
            ReadinessItem(
                "s6",
                "section_kind_present",
                "业务流程与交互还没有主流程",
                "这类内容用 steps 块写",
                Severity.WARN,
            ),
        )
    )

    assert "本章还差什么" in text
    assert "s1 背景与问题还缺 1 条内容" in text and "〔阻塞〕" in text
    assert "〔提示〕" in text
    assert "补缺口不等于填满" in text


def test_render_readiness_is_empty_when_nothing_is_missing():
    assert context.render_readiness(()) == ""


def test_render_document_state_appends_gaps_after_the_document():
    view = document_with_one_feature().view()
    without = context.render_document_state(view)
    assert "本章还差什么" not in without

    with_gaps = context.render_document_state(
        view,
        readiness=(
            ReadinessItem(
                "s1",
                "section_min_entries",
                "背景与问题还没有内容",
                "至少 1 条",
                Severity.BLOCKER,
            ),
        ),
    )
    assert with_gaps.startswith(without)
    assert "本章还差什么" in with_gaps


def test_render_existing_entries_lists_entry_ids_for_rewrites():
    text = context.render_existing_entries(document_with_one_feature())
    assert "en1" in text
    assert "把订单导出成表格" in text


def round_after_one_answer_one_skip() -> Round:
    round_ = Round.start(
        round_id=RoundId("r1"), project_id=PROJECT, user_input="想做个订单导出", now=NOW
    )
    round_.attach_claims(
        (
            Claim(
                claim_id="c1",
                text="要做一个订单导出功能",
                label=ClaimLabel.NEED,
                target=Target.body("s5"),
                raw_span="想做个订单导出",
                eid=EvidenceId("E1"),
            ),
        )
    )
    round_.submit_opening(seats=("seat.data_scale",))
    round_.integrate(
        questionnaire=Questionnaire(
            form_id=FormId("f1"),
            round_id=RoundId("r1"),
            questions=(
                Question(
                    question_id=QuestionId("q1"),
                    target=Target.body("s8"),
                    type=QuestionType.SINGLE,
                    title="单次导出最大行数？",
                    why="量级决定同步还是异步",
                    consequence_if_skipped="按同步实现",
                    options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
                ),
                Question(
                    question_id=QuestionId("q2"),
                    target=Target.body("s3"),
                    type=QuestionType.SINGLE,
                    title="谁能导出？",
                    why="决定权限口径",
                    consequence_if_skipped="默认只有运营专员",
                    options=(Option("a", "运营专员"), Option("b", "运营 + 主管")),
                ),
            ),
        )
    )
    round_.submit_answers(
        AnswerSet(
            form_id=FormId("f1"),
            answers=(Answer(QuestionId("q1"), "1 万–10 万", ("b",)),),
            skipped=(QuestionId("q2"),),
            answered_at=NOW,
        )
    )
    return round_


def test_render_answers_separates_answered_and_skipped():
    text = context.render_answers(round_after_one_answer_one_skip())
    answered, _, skipped = text.partition("--")
    assert "1 万–10 万" in answered
    assert "q1" in answered
    assert "q2" in skipped
    assert "默认只有运营专员" in skipped  # 跳过后果要带给整合者


def test_labels_and_sections_text_come_from_domain_constants():
    assert "猜测" in context.LABELS_TEXT
    assert "s5 功能需求" in context.SECTIONS_TEXT


def test_render_contradictions_uses_the_claim_texts():
    """给整合者看的互斥对子要用**主张原文**，不是 `c1/c2`——它才知道这两句在打架。"""

    rendered = context.render_contradictions(make_round_with_contradictions())

    assert "点检记录不能改" in rendered
    assert "点错了要能改" in rendered
    assert "一个说不能改" in rendered


def test_render_contradictions_says_none_when_there_are_none():
    assert context.render_contradictions(make_round()) == "（这一轮没有判出互斥的主张）"


# ── 材料信息包的选块与渲染（工具层 C-2）────────────────────────────────


def test_select_chunks_is_the_only_place_that_decides_what_goes_in():
    packet = make_packet(*["字" * 500 for _ in range(20)])

    picked = context.select_chunks([packet], budget=1200)

    assert [chunk.ordinal for _, chunk in picked] == [0, 1]
    assert all(material_id == "m1" for material_id, _ in picked)


def test_select_chunks_stops_when_the_budget_runs_out_across_materials():
    first = make_packet("字" * 700, title="甲.md")
    second = make_packet("字" * 700, title="乙.md")

    picked = context.select_chunks([first, second], budget=800)

    assert [material_id for material_id, _ in picked] == ["m1"]


def test_materials_render_with_number_anchor_and_parser_identity():
    packet = make_packet("目标：导出", "边界：只给管理员", title="需求.md", parser_id="pdfminer")
    eids = {"m1#c00": EvidenceId("E12"), "m1#c01": EvidenceId("E13")}

    rendered = context.render_materials([(packet, eids)])

    assert "需求.md" in rendered.text
    assert "pdfminer" in rendered.text        # 解析器身份照实写
    assert "[E12]" in rendered.text and "第 1 行" in rendered.text
    assert "目标：导出" in rendered.text
    assert "不要再把它说成用户说的话" in rendered.text
    assert rendered.truncated is False
    assert rendered.used_chunk_ids == ("m1#c00", "m1#c01")


def test_materials_that_were_cut_short_say_so():
    packet = make_packet(*["字" * 500 for _ in range(20)])
    eids = {
        chunk.chunk_id: EvidenceId(f"E{index + 10}")
        for index, chunk in enumerate(packet.parsed.chunks)
    }
    picked = context.select_chunks([packet], budget=1200)

    rendered = context.render_materials(
        [(packet, {chunk.chunk_id: eids[chunk.chunk_id] for _, chunk in picked})]
    )

    assert rendered.truncated is True
    assert "共 20 块" in rendered.text and "只带了" in rendered.text
    assert rendered.used_chunk_ids == tuple(chunk.chunk_id for _, chunk in picked)


def test_no_materials_renders_a_plain_sentence():
    rendered = context.render_materials([])

    assert rendered.text == "（这一轮没有外部材料）"
    assert rendered.used_chunk_ids == ()
    assert rendered.truncated is False


def test_render_materials_ignores_chunks_without_an_eid():
    """没有编号的块**不进提示词**——"编号 == 带进去的块"这条不变量的另一半。"""

    packet = make_packet("目标：导出", "边界：只给管理员")

    rendered = context.render_materials([(packet, {"m1#c00": EvidenceId("E12")})])

    assert "[E12]" in rendered.text
    assert "边界：只给管理员" not in rendered.text
