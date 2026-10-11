from datetime import datetime

from pmstudio.application import context
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
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from pmstudio.infrastructure.tools.limits import CHUNK_CHARS
from tests.support.builders import (
    make_material_packet as make_packet,
)
from tests.support.builders import (
    make_round,
    make_round_with_contradictions,
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
    assert "s5 功能需求" in context.SECTIONS_TEXT


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
        (first, EvidenceId("E9")),
        (second, EvidenceId("E10")),
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
            (by_id[material_id], EvidenceId(f"E{index + 9}"))
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


def test_render_reviews_compact_is_one_line_per_opinion():
    """紧凑形态：一条一行，丢掉理由/依据（席位只当背景用）。"""

    text = context.render_reviews_compact(
        (_review_at(1, "O1", "应支持", "因为快"),), ledger=None, round_id=RoundId("r1")
    )

    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "应支持" in lines[0]
    assert "理由" not in text


def test_render_reviews_compact_empty():
    """没有历史意见时空消息（席位槽直接用这个渲染器）。"""

    assert (
        context.render_reviews_compact((), ledger=None, round_id=RoundId("r1"))
        == "（没有席位意见）"
    )


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
    """KV 缓存的前提：同一份输入渲染两遍必须逐字相同（`02 §11.0.55`）。

    同一轮里分析者、每个席位、整合者渲染的是**同一段**材料节；它只要抖一个字节，
    `prompt_sha` 就漂、跨轮的前缀命中也别想。
    """

    packet = make_packet(*[f"块{index}" for index in range(9)])
    eids = {f"m1#c{index:02d}": EvidenceId(f"E{20 + index}") for index in range(9)}

    first = context.render_materials([(packet, eids)])
    second = context.render_materials([(packet, eids)])

    assert first.text == second.text
    assert first.used_chunk_ids == second.used_chunk_ids
