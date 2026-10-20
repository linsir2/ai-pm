from datetime import datetime

from pmstudio.application import context
from pmstudio.application.context import placement_text, section_slots_text, sections_text
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessItem, Severity
from pmstudio.domain.memory.memory import MemoryEntry
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.round.claim import Claim, SeatConflict
from pmstudio.domain.round.draft import Origin, OriginType, Proposal
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
    EntryId,
    EvidenceId,
    FeatureSlug,
    FormId,
    ProjectId,
    ProposalId,
    QuestionId,
    RoundId,
)
from pmstudio.domain.shared.label import ClaimLabel
from pmstudio.domain.shared.memory import MemoryKind
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import template_from_dict
from pmstudio.domain.shared.template_adapter import adapt
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.tools.limits import CHUNK_CHARS
from tests.support.builders import (
    STD,
    make_round,
    make_round_with_contradictions,
)
from tests.support.builders import (
    make_material_packet as make_packet,
)
from tests.support.builders import (
    make_web_packet as make_web,
)
from tests.support.fakes import InMemoryMaterialStore

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
                target=Target.feature_field(
                           FeatureSlug("order-export"), "one_liner",
                           home=STD.feature_home
                       ),
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
        round_id=RoundId("r1"),
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
        round_id=RoundId("r1"),
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
        (_review_with_two_opinions(),),
        titles={"seat.data_scale": "数据规模与性能"},
        round_id=RoundId("r1"),
    )
    assert "席位条目 seat.data_scale:O2" in text
    assert "出处编号" not in text


def test_render_reviews_shows_the_number_of_its_own_round():
    """真跑抓到（P22 回归）：两轮的 `p2:O2` 撞名，只按 (席位, 条目) 反查会把**上一轮的编号**
    显示给整合者——它照着引，等于把溯源链引到另一条判断上（用户点编号看到的是别的意见）。
    """

    ledger = EvidenceLedger.empty(ProjectId("p1"))
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r-old"), raw_span="想做个订单导出的功能"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="上一轮：导出要分页",
        source=EvidenceSource(
            seat_id="seat.data_scale",
            local_id="p2:O2",
            round_id=RoundId("r-old"),
            derived_from=(user,),
            rationale="同类做法",
        ),
        now=NOW,
    )
    own = ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="这一轮：导出要脱敏",
        source=EvidenceSource(
            seat_id="seat.data_scale",
            local_id="p2:O2",
            round_id=RoundId("r-new"),
            derived_from=(user,),
            rationale="合规要求",
        ),
        now=NOW,
    )

    text = context.render_reviews(
        (_review_with_two_opinions(),),
        titles={"seat.data_scale": "数据规模与性能"},
        ledger=ledger,
        round_id=RoundId("r-new"),
    )

    assert f"出处编号 {own}" in text


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


def test_the_decision_label_says_how_to_spot_one():
    """`decision` 只写"已经拍板的事"，模型识别不出来——真跑实测把
    「复核入口这期也定了，放在订单详情页」标成了 preference，而 preference **不进正文**：
    一条已经定的事被静默扔出文档。

    判据两条：① 给识别线索（用户原话里"定了"的说法）；② 划清与偏好的边界。
    """

    hint = context.LABEL_HINTS[context.ClaimLabel.DECISION]

    assert "定了" in hint                      # 识别线索
    assert "偏好" in hint                      # 边界要说清
    assert "s5 功能需求" in sections_text(STD)


def test_render_contradictions_uses_the_claim_texts():
    """给整合者看的互斥对子要用**主张原文**，不是 `c1/c2`——它才知道这两句在打架。"""

    rendered = context.render_contradictions(make_round_with_contradictions())

    assert "点检记录不能改" in rendered
    assert "点错了要能改" in rendered
    assert "一个说不能改" in rendered


def test_render_contradictions_says_none_when_there_are_none():
    assert context.render_contradictions(make_round()) == "（这一轮没有判出互斥的主张）"


def test_the_feature_field_table_says_main_flow_is_expected():
    """字段表是模型能看到的"该写什么"信号：`必填` 是**就绪度**的标记，不是"这一轮要写"的标记。

    真跑实测（P37）：分析者切出来的字段**正好是代码里那 6 个"必填"**
    （name / one_liner / goal / audience / exceptions / acceptance），
    而设计说明书 §2.6 说必填的 `main_flow` / `user_story` / `io` / `priority` 被饿死
    （两轮真跑 29 个功能里分别只有 18 / 3 / 3 / 4 条）。主流程覆盖率因此长期停在 48–62%。

    P38 的口径：**不升必填**（那会给每个缺主流程的功能加一条阻塞），
    改在字段表里把它标成"每个功能都要写"——只加**提示词压力**，不动判据。
    """

    lines = context.FEATURE_FIELDS_TEXT.splitlines()
    main_flow = next(line for line in lines if line.startswith("- main_flow"))

    assert "每个功能都要写" in main_flow
    assert "怎么走一遍" in main_flow
    # 只有它带这句：别的字段不许被同一句话污染
    assert [line for line in lines if "每个功能都要写" in line] == [main_flow]


# ── 材料信息包的选块与渲染（工具层 C-2）────────────────────────────────


def test_select_chunks_is_the_only_place_that_decides_what_goes_in():
    packet = make_packet(*["字" * 500 for _ in range(20)])

    picked = context.select_chunks([packet], budget=1200)

    assert [chunk.ordinal for _, chunk in picked] == [0, 1]
    assert all(material_id == "m1" for material_id, _ in picked)


def test_a_block_always_fits_the_material_budget():
    """块宽与材料预算是**一对有耦合的常数**：块比预算还宽 → 那个块永远选不中 → ② 换一身衣服回来。

    今天这两边没有守卫，而且分居两层（块宽在 `infrastructure`、预算在 `application`）——
    `CHUNK_CHARS` 抬到 4000 以上、或 `MATERIAL_BUDGET` 降到 500 以下，都会让"用户勾了材料、
    提示词说没材料"重现，且任何测试都不会红。测试同时 import 两边只在这里发生：
    **测试不构成生产依赖**，所以不违反分层。
    """

    assert CHUNK_CHARS + context.CHUNK_OVERHEAD < context.MATERIAL_BUDGET


# ── 选块粗排（`02 §11.0.53`）───────────────────────────────────────────
#
# 预算只有 4000 字 ≈ 8 块。没有 query 时"顺序取"会把预算给排在最前面的材料，而与内容无关；
# 块宽不变式修好之后一份 200 页文档会切出几百块，"只读前 8 块"比修之前更糟。


def test_without_a_query_selection_keeps_document_order():
    """不传 query = 与今天逐块一致（联网检索那条路走这里：Tavily 的序就是相关序）。"""

    packet = make_packet("甲" * 300, "导出权限" * 100, "乙" * 300)

    picked = context.select_chunks([packet], budget=800)

    assert [chunk.ordinal for _, chunk in picked] == [0, 1]


def test_a_query_pulls_the_blocks_that_mention_it_forward():
    packet = make_packet("甲" * 300, "导出权限" * 100, "乙" * 300)

    picked = context.select_chunks([packet], budget=800, query="导出权限怎么定")

    assert [chunk.ordinal for _, chunk in picked] == [1, 0]


def test_ties_keep_document_order_so_the_result_is_reproducible():
    packet = make_packet("同一段话", "同一段话", "同一段话")

    picked = context.select_chunks([packet], budget=10_000, query="导出")

    assert [chunk.ordinal for _, chunk in picked] == [0, 1, 2]


def test_a_query_no_block_mentions_falls_back_to_document_order():
    packet = make_packet("甲" * 300, "乙" * 300, "丙" * 300)

    picked = context.select_chunks([packet], budget=700, query="完全不搭的词")

    assert [chunk.ordinal for _, chunk in picked] == [0, 1]


def test_every_picked_material_gets_at_least_one_block():
    """用户勾了 N 份材料 = 说了"这些都要参与"：预算再紧，每份也得露一面。

    没有这条保底，一份高分材料会吃光全部 8 个块，其余被勾的材料整份缺席——那正是缺陷 ②
    的用户体感（勾了材料、提示词说没材料），修完块宽不加重现一次。
    """

    strong = make_packet(*(["导出权限" * 60] * 12), material_id="强", title="强.md")
    weak = make_packet("甲" * 300, "乙" * 300, material_id="弱", title="弱.md")

    picked = context.select_chunks([strong, weak], budget=800, query="导出权限")

    assert {material_id for material_id, _ in picked} == {"强", "弱"}


def test_the_floor_does_not_break_the_budget():
    """保底也不能超预算：装不下就装不下，不许为了"每份露一面"把预算撑破。"""

    strong = make_packet(*(["字" * 500] * 12), material_id="强", title="强.md")
    weak = make_packet(*(["字" * 500] * 4), material_id="弱", title="弱.md")

    picked = context.select_chunks([strong, weak], budget=1200)

    assert sum(len(chunk.text) for _, chunk in picked) <= 1200


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


def test_a_fetched_page_shows_its_url_in_the_prompt():
    """抓来的网页（P27）：头一行写清是**抓来的网页**，并带上它的网址。

    模型该知道自己读的是"用户点名的那个页面"；点编号时，账本里的外部指称也是这个 URL。
    """

    packet = make_packet(
        "竞品把导出放在管理员菜单里。",
        title="竞品页",
        origin="web_fetch",
        source_ref="https://example.com/spec",
    )

    rendered = context.render_materials([(packet, {"m1#c00": EvidenceId("E12")})])

    assert "【抓来的网页】竞品页" in rendered.text
    assert "来源：https://example.com/spec" in rendered.text


def test_an_uploaded_material_does_not_repeat_its_attachment_ref():
    """上传材料的头**不写来源**：`attachment:需求.md` 与标题重复，白占预算。"""

    packet = make_packet("目标：导出", title="需求.md")

    rendered = context.render_materials([(packet, {"m1#c00": EvidenceId("E12")})])

    assert "来源：" not in rendered.text


def test_render_web_results_says_none_instead_of_leaving_it_blank():
    rendered = context.render_web_results([])

    assert rendered.text == "（这一轮没有联网检索结果）"
    assert rendered.used_chunk_ids == ()
    assert rendered.truncated is False


def stamp_web(*drafts):  # type: ignore[no-untyped-def]
    """草稿 → 盖章的信息包（与生产同一条路：`MaterialStore.put` 才是发 id 的地方）。"""

    store = InMemoryMaterialStore()
    return [
        store.put(
            project_id=PROJECT,
            parsed=draft,
            sha256=f"web-{index}",
            content=b"x",
            now=NOW,
        )
        for index, draft in enumerate(drafts)
    ]


def test_render_web_results_is_budgeted_and_says_how_many_it_left_out():
    """预算挤掉的那几条**要在提示词里说清**：不然模型以为"搜到的就这些"。"""

    first, second = stamp_web(
        make_web(url="https://a", text="字" * 120),
        make_web(url="https://b", text="字" * 120),
    )
    entries = [
        (first, list(first.parsed.chunks), EvidenceId("E9")),
        (second, list(second.parsed.chunks), EvidenceId("E10")),
    ]

    rendered = context.render_web_results(entries, budget=200)

    assert "[E9]" in rendered.text
    assert "[E10]" not in rendered.text
    assert "只带了前 1 条 / 共 2 条" in rendered.text
    assert rendered.truncated is True
    assert rendered.used_chunk_ids == ("m1#c00",)


def test_the_selection_cost_never_lets_the_renderer_drop_a_numbered_chunk():
    """§2.1 的核心：**选块的成本 ≥ 渲染的成本**，所以选进来的每一条都会被渲染出来。

    两把尺子量同一件事时，标题 + URL 会把渲染挤到预算外，于是账本里多出一个
    模型永远看不到的编号——那正是这条不变量要拦的事。
    """

    packets = stamp_web(
        *[make_web(url=f"https://example.com/{index}", text="字" * 250) for index in range(6)]
    )

    picked = context.select_chunks(
        packets, budget=context.WEB_BUDGET, cost=context.web_block_cost
    )
    by_id = {packet.material_id: packet for packet in packets}
    rendered = context.render_web_results(
        [
            (
                by_id[material_id],
                [chunk for mid, chunk in picked if mid == material_id],
                EvidenceId(f"E{index + 9}"),
            )
            for index, (material_id, _) in enumerate(picked)
        ],
        budget=context.WEB_BUDGET,
    )

    assert len(picked) > 1                                   # 这个预算下确实选出了几条
    assert rendered.truncated is False                       # 渲染一条都没再丢
    assert rendered.used_chunk_ids == tuple(chunk.chunk_id for _, chunk in picked)


# —— 归因补交代的两个渲染（P24：判据读什么，提示词就只给什么） ——


def test_render_selected_opinions_renders_only_the_named_handles():
    """"只出申报"那条小调用只该看到**点名的**那几条意见，不该把整批再喂一遍。"""

    review = _review_with_two_opinions()  # 一条疑问 O1 + 一条判断 O2（pass_index=2）
    ledger = _ledger_with_judgment_numbered()

    text = context.render_selected_opinions(
        (review,),
        {"seat.data_scale:O2"},
        ledger=ledger,
        round_id=RoundId("r1"),
    )

    assert "seat.data_scale:O2" in text
    assert "seat.data_scale:O1" not in text  # 没点名的不出现
    assert "单次导出应在 60 秒内完成" in text  # 正文要给（不然没法判断用没用上）


def test_render_citations_lists_exactly_what_the_accounting_reads():
    """核算读的是"题的席位条目地址 + 题/提案引用过的编号"，渲染就只给这两样。

    给多了（比如把整篇文档塞进去），模型就会拿"我写过类似的话"当"我用过了"。
    """

    question = Question(
        question_id=QuestionId("q1"),
        target=Target.body("s8"),
        type=QuestionType.SINGLE,
        title="单次导出最大行数？",
        why="量级决定同步还是异步",
        consequence_if_skipped="按同步实现",
        options=(Option("a", "少于 1 万"), Option("b", "1 万–10 万")),
        evidence_refs=(EvidenceId("E4"),),
        asked_by=("seat.data_scale",),
    )
    proposal = Proposal(
        proposal_id=ProposalId("p01"),
        entry_id=EntryId("entry-abc"),
        op=WriteOp.ADD,
        target=Target.body("s1"),
        text="现状是纸单子",
        origin=Origin(type=OriginType.USER, refs=(EvidenceId("E9"),)),
    )

    text = context.render_citations(
        accounted_handles=frozenset({"seat.data_scale:O2"}),
        questions=(question,),
        proposals=(proposal,),
    )

    assert "seat.data_scale:O2" in text
    assert "E4" in text and "E9" in text
    assert "纸单子" not in text  # 不渲染正文：判据不看它，提示词也就不给


# —— 跨轮记忆（M6c）：限量、空态、与正文冲突时的口径 ——


def memory_entry(index: int, *, kind: MemoryKind = MemoryKind.DECISION):
    return MemoryEntry(
        memory_id=f"mem{index}",
        kind=kind,
        text=f"第 {index} 条已经定过的口径",
        refs=(EvidenceId("E1"),),
        round_id="r1",
        created_at=datetime(2026, 10, 3, 20, 0, 0),
        updated_at=datetime(2026, 10, 3, 20, 0, 0),
    )


def test_no_memory_renders_a_plain_sentence_and_no_empty_section():
    """空记忆不留空节：一句实话，让模型知道"这里本来就没有"，而不是"被截掉了"。"""

    assert context.render_memory(()) == context.NO_MEMORY_TEXT
    assert context.NO_MEMORY_TEXT.strip()


def test_memory_render_is_capped_and_says_it_was_capped():
    """60 条只渲 20 行，并**写明**只带了前 20 条（静默截断会让模型以为剩下那些不存在）。"""

    entries = tuple(memory_entry(index) for index in range(1, 41))

    text = context.render_memory(entries)

    assert text.count("- 〔") == context.MEMORY_LINE_LIMIT == 20
    assert "共 40 条，这里只带了前 20 条" in text
    assert "第 21 条已经定过的口径" not in text   # 第 21 条起不再出现


def test_memory_section_carries_the_document_wins_rule():
    """冲突口径写在渲染里（席位与整合者共用这一处实现）。"""

    text = context.render_memory((memory_entry(1),))

    assert "以正文为准" in text
    assert "E1" in text
    assert "已定口径" in text             # kind 用人话，不给模型看枚举值


def test_the_memory_we_show_the_agents_never_carries_a_memory_id():
    """M6f（真跑 L2 第 2 轮的现场）：席位把行首的 `mem…` 当成了出处编号 →
    `evidence_refs` 里写 `mem-12565550`，整趟席位判失败。

    所以给席位/整合者的那一份**一个 id 都不许出现**；id 只给记忆整理者（它要写 target_id）。
    """

    entries = (memory_entry(1),)
    injected = context.render_memory(entries)

    assert "mem1" not in injected
    assert "（出处：E1）" in injected
    assert "才是出处编号" in injected

    state = context.render_memory_state(entries)
    assert "mem1" in state                     # 整理者要拿它写 target_id
    assert "只用来填 `target_id`" in state


def _ledger_with_two_seat_judgments() -> EvidenceLedger:
    """两条席位判断进账本（seat.a / seat.b 各一条），形状与真 assigner 一致。"""

    ledger = EvidenceLedger.empty(PROJECT)
    user = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span="想做个订单导出"),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="导出应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.a",
            local_id="p1:O1",
            round_id=RoundId("r1"),
            derived_from=(user,),
            rationale="体验优先",
        ),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="导出不应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.b",
            local_id="p1:O1",
            round_id=RoundId("r1"),
            derived_from=(user,),
            rationale="数据量大",
        ),
        now=NOW,
    )
    return ledger


def _seat_conflict() -> SeatConflict:
    return SeatConflict(
        about="导出性能",
        seat_a="seat.a",
        claim_a="导出应在 60 秒内完成",
        handle_a="p1:O1",
        seat_b="seat.b",
        claim_b="导出不应在 60 秒内完成",
        handle_b="p1:O1",
    )


def test_render_seat_conflicts_shows_the_judgment_eids():
    """席位冲突要带两条判断的出处编号——整合者才引得到，机械盖章才盖得上。"""

    text = context.render_seat_conflicts(
        (_seat_conflict(),), ledger=_ledger_with_two_seat_judgments(), round_id=RoundId("r1")
    )

    assert "出处编号 E2" in text
    assert "出处编号 E3" in text
    assert "导出性能" in text


def test_render_seat_conflicts_without_ledger_omits_eids():
    """没有账本时不假装有编号（与 `render_reviews` 同口径）。"""

    text = context.render_seat_conflicts((_seat_conflict(),), round_id=RoundId("r1"))

    assert "出处编号" not in text
    assert "导出性能" in text


# ── 席位意见按趟折叠（消费遥测 §7.5 ③）────────────────────────────


def _judgment_at(local_id: str, claim: str, because: str) -> Opinion:
    return Opinion(
        local_id=local_id,
        form=OpinionForm.JUDGMENT,
        about="导出",
        claim=claim,
        because=because,
        evidence_refs=(EvidenceId("E1"),),
    )


def _review_at(pass_index: int, local_id: str, claim: str, because: str) -> SeatReview:
    return SeatReview(
        seat_id="seat.data_scale",
        basis="依据",
        opinions=(_judgment_at(local_id, claim, because),),
        pass_index=pass_index,
    )


def test_the_seat_gets_its_own_past_opinions_verbatim():
    """指针式的第一半：**本席位自己**的历史结论逐字保留——它要的是"别重复自己"。

    本席位的原话是承重信息（它上一轮判过什么，直接决定这一轮该说什么），
    所以不压、不摘要，一行一条原样给。
    """

    mine = _review_at(1, "O1", "导出要按角色授予", "因为越权")
    theirs = SeatReview(
        seat_id="seat.permissions",
        basis="依据",
        opinions=(_judgment_at("O9", "字段级掩码要做", "因为合规"),),
        pass_index=1,
    )

    text = context.render_prior_opinions(
        (mine, theirs), ledger=None, round_id=RoundId("r1"), seat_id="seat.data_scale"
    )

    assert "导出要按角色授予" in text
    assert "字段级掩码要做" not in text        # 别人的原话不给


def test_other_seats_are_reduced_to_a_one_line_roster():
    """指针式的第二半：**其它席位**压成一行点名（坐过什么位、看过什么题）。

    为什么不全给：实测跨席位引用率 0.4%（`07 §7.7` 遥测），而它们的原话占了席位
    提示词 60.3%。§7.12 原则①「删优先于排」——压成点名仍保留"已有人看过这个题"的
    信号（避免重复提问），但丢掉无人消费的正文。
    """

    mine = _review_at(1, "O1", "导出要按角色授予", "因为越权")
    theirs = SeatReview(
        seat_id="seat.permissions",
        basis="依据",
        opinions=(_judgment_at("O9", "字段级掩码要做", "因为合规"),),
        pass_index=1,
    )

    text = context.render_prior_opinions(
        (mine, theirs), ledger=None, round_id=RoundId("r1"), seat_id="seat.data_scale"
    )

    assert "seat.permissions" in text          # 点名还在：知道这块有人看过了
    assert "因为合规" not in text               # 理由不给
    assert text.count("\n") <= 2                # 别人的部分就一行


def test_no_history_says_so():
    """没有历史意见时说一句，不留空节（空节会让模型以为历史被省略了）。"""

    assert (
        context.render_prior_opinions(
            (), ledger=None, round_id=RoundId("r1"), seat_id="seat.data_scale"
        )
        == "（没有席位意见）"
    )


def test_the_pointer_form_is_materially_shorter_than_giving_everything():
    """这条是 ①「删优先于排」的量化验收：指针式必须显著短于全量。"""

    mine = _review_at(1, "O1", "导出要按角色授予", "因为越权")
    others = tuple(
        SeatReview(
            seat_id=f"seat.other_{index}",
            basis="依据",
            opinions=(
                _judgment_at(f"O{index}a", f"一段相当长的判断正文{index}" * 8, "理由" * 8),
            ),
            pass_index=1,
        )
        for index in range(8)
    )

    pointer = context.render_prior_opinions(
        (mine, *others), ledger=None, round_id=RoundId("r1"), seat_id="seat.data_scale"
    )
    everything = context.render_reviews_compact(
        (mine, *others), ledger=None, round_id=RoundId("r1")
    )

    assert len(pointer) * 2 < len(everything)


def _two_feature_document() -> Document:
    """两个功能 + 一段节级正文：够验「只留点名的、别的节说一句没展开」。"""

    document = Document.new(
        document_id=DocumentId("doc1"), project_id=PROJECT, title="订单中台", now=NOW
    )
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id="en1",  # type: ignore[arg-type]
                op=WriteOp.ADD,
                target=Target.body("s1"),
                text="运营每天导不出订单，靠人肉汇总",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            ),
            WriteInstruction(
                instruction_id="i2",
                entry_id="en2",
                op=WriteOp.ADD,
                target=Target.feature_field(
                           FeatureSlug("order-export"), "one_liner",
                           home=STD.feature_home
                       ),
                text="把订单导出成表格",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            ),
            WriteInstruction(
                instruction_id="i3",
                entry_id="en3",
                op=WriteOp.ADD,
                target=Target.feature_field(
                           FeatureSlug("wechat-login"), "one_liner",
                           home=STD.feature_home
                       ),
                text="用微信扫码登录",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            ),
        ],
        now=NOW,
        summary="两个功能",
        authorized_new_features=frozenset(
            {FeatureSlug("order-export"), FeatureSlug("wechat-login")}
        ),
    )
    return document


# ── 材料节的呈现顺序 = 文档顺序（`02 §11.0.54` 撤掉 U 型之后的终态）────────────
#
# 2026-10-10 曾把材料节改成 U 型重排（高分摆首尾），随后撤掉：唯一直接检验 U 型重排的
# 论文（Jin et al. arXiv 2410.05983）说 **k 小时收益可忽略**，而两篇大样本复现
# （Cuconasu EMNLP'25 / Gabín SIGIR'26）在真实 top-k 里测不出显著收益；实测份额也小——
# 分析者提示词里材料节只占 19%，那一轮的整合者提示词里占 0%。
# 复现最稳的证据是"长度伤性能"，不是位置。所以选块只决定**挑哪些**，呈现一律按文档序。


def test_the_material_section_follows_document_order():
    """按文档序渲染，编号也按文档序发——提示词里号码顺序走，人对着材料卡找得到。"""

    packet = make_packet(*[f"导出权限说明{index}" for index in range(6)])
    eids = {f"m1#c{index:02d}": EvidenceId(f"E{10 + index}") for index in range(6)}

    rendered = context.render_materials([(packet, eids)])

    shown = [
        line[1 : line.index("]")] for line in rendered.text.splitlines() if line.startswith("[E")
    ]
    assert shown == ["E10", "E11", "E12", "E13", "E14", "E15"]


def test_the_material_section_is_byte_identical_when_rendered_twice():
    """KV 缓存的前提：同一份输入渲染两遍必须逐字相同（`02 §11.0.55`）。"""

    packet = make_packet(*[f"块{index}" for index in range(9)])
    eids = {f"m1#c{index:02d}": EvidenceId(f"E{20 + index}") for index in range(9)}

    first = context.render_materials([(packet, eids)])
    second = context.render_materials([(packet, eids)])

    assert first.text == second.text
    assert first.used_chunk_ids == second.used_chunk_ids


# ── document_state 按功能窄化（`07 §7.12` 第 2 项·sim_review 层）──────────────
#
# sim_review（M11）点名要走查 `feature#slug`，却一直拿**全文十三章**——而它的机械筛
# （`sim_review.py:174-183`）自己划定合法引用只在目标功能内（"指到了别的功能的内容"是 issue，
# `acceptance[slug]` 必须全覆盖且只许这些，`verdict_because` 只引本地摩擦）。
# 所以窄化不省证据，只省干扰——正是判据①「删优先于排」。


def test_render_document_without_slugs_is_unchanged():
    """不传 `feature_slugs` 时必须与今天逐字相同——除了 sim_review 之外的调用方零行为变化。"""

    document = _two_feature_document()
    view = document.view()

    assert context.render_document(view) == context.render_document(view, feature_slugs=None)


def test_render_document_with_slugs_keeps_only_those_features():
    document = _two_feature_document()

    text = context.render_document(document.view(), feature_slugs=frozenset({"order-export"}))

    assert "feature#order-export" in text
    assert "把订单导出成表格" in text
    assert "feature#wechat-login" not in text
    assert "用微信扫码登录" not in text


def test_a_section_without_a_named_feature_says_so_instead_of_vanishing():
    """章节不许消失：否则模型会以为这一章不存在（本项目"不留空节"的同一条纪律）。"""

    document = _two_feature_document()

    text = context.render_document(document.view(), feature_slugs=frozenset({"order-export"}))

    assert "## s1" in text                        # 节还在
    assert "本节没有点名的功能" in text
    assert "另见本节" in text and "条与功能无关" in text   # 且说清少了什么


def test_the_narrowed_state_says_it_is_a_slice():
    """开头必须声明这是走查切片——否则模型会对自己没看到的部分下判断。"""

    document = _two_feature_document()

    text = context.render_document_state(document.view(), feature_slugs=frozenset({"order-export"}))

    assert "走查切片" in text
    assert "不是全文" in text


def test_the_narrowed_state_still_carries_the_gap_list():
    """缺口清单必须还在——否则就绪度又变回界面上的仪表盘（`01` §7.2）。"""

    document = _two_feature_document()

    text = context.render_document_state(document.view(), feature_slugs=frozenset({"order-export"}))

    assert "s6" in text or "补缺口" in text


# —— 提示词按模板渲染（`02` §11.0.69），standard **逐字不变** ——

STANDARD_SECTIONS_TEXT = """- s1 背景与问题
- s2 目标与非目标
- s3 用户与受众
- s4 范围与边界
- s5 功能需求
- s6 业务流程与交互
- s7 数据与接口
- s8 非功能需求
- s9 技术方案与约束
- s10 假设、约束与依赖
- s11 风险与开放问题
- s12 上线、验收与迭代
- s13 版本历史
（section_id 只能填上面那个短 id，如 s3；**不要带标题、不要带空格**）"""

STANDARD_SLOTS_TEXT = "\n".join(
    f"- s8.{name}（{title}，章节槽；写它时给 section_id=s8 + field={name}，不要给 feature_slug）"
    for name, title in (
        ("performance", "性能"),
        ("availability", "可用性"),
        ("security", "安全"),
        ("compatibility", "兼容"),
        ("observability", "可观测"),
        ("cost", "成本"),
    )
)


def test_sections_text_reproduces_the_standard_prompt_byte_for_byte():
    assert sections_text(STD) == STANDARD_SECTIONS_TEXT


def test_section_slots_text_reproduces_the_standard_prompt_byte_for_byte():
    assert section_slots_text(STD) == STANDARD_SLOTS_TEXT


def test_sections_text_follows_a_custom_template():
    """自由 id 的模板：章节表照模板列，末尾那句示例也跟着走。"""

    adapter = adapt(
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {"id": "overview", "title": "概览", "purpose": "p"},
                    {"id": "feat", "title": "功能", "purpose": "p", "role": "feature"},
                    {"id": "open", "title": "未决", "purpose": "p", "role": "open_questions"},
                    {"id": "history", "title": "版本", "purpose": "p", "role": "version_history"},
                ],
            }
        )
    )

    text = sections_text(adapter)

    assert text.startswith("- overview 概览\n- feat 功能\n- open 未决\n- history 版本")
    assert "如 open" in text  # 示例取第三章（与 standard 同款取法）


def test_section_slots_text_says_so_when_the_template_has_none():
    adapter = adapt(
        template_from_dict(
            {
                "template_id": "t",
                "title": "x",
                "sections": [
                    {"id": "feat", "title": "功能", "purpose": "p", "role": "feature"},
                    {"id": "open", "title": "未决", "purpose": "p", "role": "open_questions"},
                    {"id": "history", "title": "版本", "purpose": "p", "role": "version_history"},
                ],
            }
        )
    )

    assert section_slots_text(adapter) == "（没有章节槽）"


def test_placement_text_names_each_chapter_and_its_role():
    """归位表（`$placement`）：逐章一行 + 角色提示；有槽的章把槽写清楚。"""

    text = placement_text(STD)

    assert "- s5 功能需求：每个功能一棵子树" in text
    assert "- s6 业务流程与交互：主流程与异常路径，按步骤写清谁在什么时候做什么。" in text
    assert "写 s8 里的「性能」用章节槽：section_id=s8 + field=performance" in text
    assert "版本历史" in text and "不用手写" in text


def test_placement_text_drops_the_steps_chapter_rule_when_there_is_none():
    """light 没有步骤章：「跨功能流程写哪一章」那句就不该出现（说了就是教模型往不存在的章写）。"""

    from pmstudio.infrastructure.templates import loader

    light = adapt(loader.load_template_file(loader.TEMPLATES_DIR / "light.toml"))
    text = placement_text(light)

    assert "s6" not in text
    assert "- s5 功能需求" in text


def test_placement_text_carves_chain_exceptions_out_of_feature_fields():
    """`§J-B` 的修复在 T6 搬家时丢了（`3b7faf5` 修的是 analyzer.md 里那张硬编码表，
    T6 用生成的 `placement_text` 取代了它，但新表没带上 s5 侧的排除句）。

    R：模板重构不许丢归位语义。I：standard 模板渲染；P：读文本；
    R：链路本身 + 链上异常只归步骤章、功能字段只写单功能内部——两句都必须在，
    且都用适配器的章号（不许写死 s5/s6，light 等模板章号不同）。
    """

    text = placement_text(STD)

    assert "只写**单个功能内部**的事" in text
    assert "不许一段流程散两处" in text
    # 章号来自适配器：standard 下是 s5/s6，换模板跟着变
    assert "s5" in text and "s6" in text


def test_placement_text_carve_out_only_with_a_steps_chapter():
    """R：light 没有步骤章，排除句也不该出现——说了就是教模型往不存在的章写
    （与 `test_placement_text_drops_the_steps_chapter_rule_when_there_is_none` 同口径）。"""

    from pmstudio.infrastructure.templates import loader

    light = adapt(loader.load_template_file(loader.TEMPLATES_DIR / "light.toml"))
    text = placement_text(light)

    assert "单个功能内部" not in text
    assert "散两处" not in text


# —— 联网结果：整段落库 + 一个结果一个编号 + 成本/渲染按块（批 6a Task 7）——


def _multi_chunk_web(*, url: str = "https://a", title: str = "长文", blocks: int = 3):
    """一条**多块**的联网结果（今天恒定 1 块；切块后一条结果可能是好几段）。"""
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    return ParsedMaterial(
        origin="web_search",
        title=title,
        source_ref=url,
        media_type="text/html",
        bytes_len=100,
        chunks=tuple(
            MaterialChunk(
                chunk_id=f"tavily#c{index:02d}",
                ordinal=index,
                text=f"第{index}段正文" * 10,
                heading_path=(),
                anchor=f"网页摘要 · 第 {index + 1} 段",
            )
            for index in range(blocks)
        ),
        parser_id="tavily",
        parser_version="1",
    )


def test_a_web_result_costs_its_title_and_url_once():
    """R：标题 + URL **只算一次**（不是每块一次）。

    为什么：一条结果现在可能是好几段（切块后）。每段都算一次标题+URL，
    同一段信息重复付费，`WEB_BUDGET` 只够带进一条结果的一两段。
    """
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    packet = ParsedMaterial(
        origin="web_search", title="很长的标题" * 5, source_ref="https://example.com/a",
        media_type="text/html", bytes_len=10,
        chunks=(
            MaterialChunk(chunk_id="tavily#c00", ordinal=0, text="甲" * 100,
                          heading_path=(), anchor="网页摘要 · 第 1 段"),
            MaterialChunk(chunk_id="tavily#c01", ordinal=1, text="乙" * 100,
                          heading_path=(), anchor="网页摘要 · 第 2 段"),
        ),
        parser_id="tavily", parser_version="1",
    )
    store = InMemoryMaterialStore()
    stamped = store.put(project_id=PROJECT, parsed=packet, sha256="web-x", content=b"x", now=NOW)

    first = context.web_block_cost(stamped, stamped.parsed.chunks[0])
    second = context.web_block_cost(stamped, stamped.parsed.chunks[1])

    assert second == 100, f"第二块只该算正文（100），实得 {second}"
    assert first == 100 + len(stamped.parsed.title) + len(stamped.parsed.source_ref) \
        + context.WEB_EID_RESERVE, f"第一块还要算标题+URL+编号位，实得 {first}"


def test_the_rendered_web_text_contains_every_selected_chunk():
    """R：渲染要把**选中的每一段**都拼出来。

    今天只渲染 `chunks[0]`——结果切成多段后，其余段就**从提示词里消失了**
    （但它们的编号还在账本里 → 账本被污染）。
    """
    from pmstudio.application.dto import MaterialChunk, ParsedMaterial

    packet = ParsedMaterial(
        origin="web_search", title="长文", source_ref="https://example.com/a",
        media_type="text/html", bytes_len=10,
        chunks=(
            MaterialChunk(chunk_id="tavily#c00", ordinal=0, text="第一段正文",
                          heading_path=(), anchor="网页摘要 · 第 1 段"),
            MaterialChunk(chunk_id="tavily#c01", ordinal=1, text="第二段正文",
                          heading_path=(), anchor="网页摘要 · 第 2 段"),
        ),
        parser_id="tavily", parser_version="1",
    )
    store = InMemoryMaterialStore()
    stamped = store.put(project_id=PROJECT, parsed=packet, sha256="web-y", content=b"x", now=NOW)

    rendered = context.render_web_results(
        [(stamped, list(stamped.parsed.chunks), EvidenceId("E12"))], budget=2000
    )

    assert "第一段正文" in rendered.text
    assert "第二段正文" in rendered.text
    # 块 id 入库时被材料仓改写成 `m1#c00`（盖章在那儿）
    assert rendered.used_chunk_ids == ("m1#c00", "m1#c01")
