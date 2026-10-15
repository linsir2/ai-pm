"""交付用 Markdown：纯函数、可判据、确定性。

四条硬规矩（`01-规格.md` §7.3）：空章显式；s13 由版本生成；偏好区不进正文；
附录只带编号与摘要（`04` D5 的原话留在系统内）。
"""

import re
from datetime import datetime

from pmstudio.application.deliverable import render_markdown
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessChecker, Severity
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.ids import (
    DocumentId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    ProjectId,
    RoundId,
    SectionId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template_adapter import adapt
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, STD, document_with_feature, make_document

EXPORTED_AT = datetime(2026, 9, 24, 12, 40)
RAW_SPAN = "想做个订单导出的功能"


def ledger_with_two_entries() -> EvidenceLedger:
    ledger = EvidenceLedger.empty(PROJECT)
    first = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text="要做一个订单导出功能",
        source=EvidenceSource(round_id=RoundId("r1"), raw_span=RAW_SPAN),
        now=NOW,
    )
    ledger.record(
        kind=EvidenceKind.AGENT_DERIVED,
        text="单次导出应在 60 秒内完成",
        source=EvidenceSource(
            seat_id="seat.data_scale",
            local_id="p1:O1",
            derived_from=(first,),
            rationale="量级在 1 万级",
        ),
        now=NOW,
    )
    return ledger


def ledger_with_three_entries() -> EvidenceLedger:
    """E1 会被正文引用·E2 只被未决清单引用·E3 谁都没引用（B 的三档正好各一条）。"""

    ledger = ledger_with_two_entries()
    ledger.record(
        kind=EvidenceKind.SYSTEM_DEFAULT,
        text="导出记录的保存期限是多少？（未决定，待决人：PM；跳过后果：先搁着）",
        source=EvidenceSource(
            round_id=RoundId("r1"), rationale="跳过的题按题面后果落成 s11 开放问题"
        ),
        now=NOW,
    )
    return ledger


def with_open_question(document: Document, *, refs: tuple[EvidenceId, ...] = (EvidenceId("E2"),)):
    """往 s11 落一条未决条目（与 `assembly.open_item_proposal` 同形）。"""

    document.apply(
        [
            WriteInstruction(
                instruction_id="i-open",
                entry_id=EntryId("en-open"),
                op=WriteOp.ADD,
                target=Target.body("s11"),
                text="导出记录的保存期限是多少？（未决定，待决人：PM；跳过后果：先搁着）〔去处：s7〕",
                refs=refs,
                kind=BlockKind.QUESTION,
            )
        ],
        now=NOW,
        summary="落一条未决",
    )
    return document


def export(document: Document, ledger: EvidenceLedger, readiness=()) -> str:  # type: ignore[no-untyped-def]
    return render_markdown(
        view=document.view(),
        readiness=readiness,
        versions=document.versions,
        ledger=ledger,
        exported_at=EXPORTED_AT,
        adapter=adapt(document.template),
    ).markdown


def visible_tokens(text: str) -> set[str]:
    """正文里出现的编号。〔…〕里还有「未决·阻塞」这类标记与「去处：…」这类地址，只认 E<数字>。"""

    found: set[str] = set()
    for block in re.findall(r"〔([^〕]*)〕", text):
        found.update(part for part in block.split() if re.fullmatch(r"E\d+", part))
    return found


def appendix_a_of(text: str) -> str:
    """从导出里切出附录 A 那一段（不含后面的附录 B/C）。"""

    _, rest = text.split("## 附录 A", 1)
    appendix, _, _ = rest.partition("## 附录")
    return appendix


def test_header_carries_title_version_time_and_readiness_stamp():
    document = make_document()
    text = export(
        document,
        ledger_with_two_entries(),
        ReadinessChecker().check(document.view(), adapter=STD),
    )

    assert text.startswith("# 产品文档 · 订单中台（v1）")
    assert "导出时间 2026-09-24 12:40" in text
    assert "就绪度：未通过" in text
    assert "阻塞" in text and "提示" in text


def test_lists_thirteen_sections_in_order():
    text = export(make_document(), ledger_with_two_entries())
    positions = [text.index(f"\n## {index} ") for index in range(1, 14)]
    assert positions == sorted(positions)
    assert "## 13 版本历史" in text


def test_empty_section_is_explicit_instead_of_silently_skipped():
    text = export(
        make_document(),
        ledger_with_two_entries(),
        ReadinessChecker().check(make_document().view(), adapter=STD),
    )

    assert "## 1 背景与问题" in text
    assert "（本章为空" in text
    assert "至少 1 条" in text  # 空章要连"差什么"一起说


def test_entries_carry_evidence_labels():
    text = export(document_with_feature(), ledger_with_two_entries())
    assert "把订单导出成表格〔E1〕" in text


def test_feature_subtree_is_rendered_under_s5_with_field_titles():
    text = export(document_with_feature(), ledger_with_two_entries())

    assert "\n## 5 功能需求" in text
    assert "### 5.1 " in text and "order-export" in text
    assert "一句话描述" in text
    # 非必填但为空的字段要列出来，方便继续补
    assert "待补" in text


def test_section_13_comes_from_versions_not_from_blocks():
    document = make_document()
    text = export(document, ledger_with_two_entries())

    assert "| v1 |" in text and "初始化 13 章骨架" in text
    assert "| 版本 |" in text


def test_preference_area_is_appendix_only():
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-pref",
                entry_id=EntryId("en-pref"),
                op=WriteOp.ADD,
                target=Target.preference(),
                text="登录页要暗色",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="加一条偏好",
    )
    text = export(document, ledger_with_two_entries())
    body = text.split("## 附录")[0]

    assert "登录页要暗色" not in body
    assert "登录页要暗色" in text
    assert "不进交付物" in text


def test_appendix_has_no_raw_span():
    # 附录只列"本文引用过"的编号（P31），所以这条得用一份真的引用了 E1 的文档做夹具
    text = export(document_with_feature(), ledger_with_two_entries())

    assert "- E1 ·" in text
    assert "要做一个订单导出功能" in text  # 摘要可以给
    assert RAW_SPAN not in text  # 原话（raw_span）留在系统内（D5）


def test_appendix_lists_only_evidence_the_document_cites():
    # 账本是项目级、跨轮、只增的：没进正文的那些（未采纳的提案、跳过的题、更早的轮次）
    # 读者在正文里找不到落点，进交付物只会稀释真正的出处（P31）
    text = export(document_with_feature(), ledger_with_two_entries())

    assert "- E1 · 用户输入 · 要做一个订单导出功能" in text
    assert "E2" not in text
    assert "单次导出应在 60 秒内完成" not in text


def test_appendix_says_how_many_are_left_out_of_the_document():
    text = export(document_with_feature(), ledger_with_two_entries())

    assert "另有 1 条出处没有被本文引用" in text


def test_appendix_counts_the_preference_area_as_cited():
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-pref",
                entry_id=EntryId("en-pref"),
                op=WriteOp.ADD,
                target=Target.preference(),
                text="登录页要暗色",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="加一条偏好",
    )
    text = export(document, ledger_with_two_entries())

    # 偏好区会带自己的〔E1〕（附录 B），所以它引用的编号同样要能查到
    assert "- E1 · 用户输入 · 要做一个订单导出功能" in text
    assert "另有 1 条出处没有被本文引用" in text


def test_appendix_says_so_when_the_document_cites_nothing():
    text = export(make_document(), ledger_with_two_entries())

    assert "（本文没有引用任何出处）" in text
    assert "- E1 ·" not in text
    assert "- E2 ·" not in text


def test_appendix_of_an_empty_ledger_still_says_there_is_none():
    text = export(document_with_feature(), EvidenceLedger.empty(PROJECT))

    assert "（还没有任何出处）" in text
    assert "没有被本文引用" not in text


def test_feature_name_carries_its_evidence_into_the_heading():
    # 名称也是正文内容：界面在功能名旁边挂了出处胶囊（`FeatureBlock.tsx`），
    # 导出也得挂——否则附录里会出现一个正文根本看不见的编号
    # （P31 真跑对账抓到：L3 `p41 · s5/…name · 依据 E28`、L2 的 E170 都是这一条）
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-name",
                entry_id=EntryId("en-name"),
                op=WriteOp.ADD,
                target=Target.feature_field(
                           FeatureSlug("order-export"), "name",
                           home=STD.feature_home
                       ),
                text="订单导出",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="写名称",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )
    text = export(document, ledger_with_two_entries())

    assert "### 5.1 订单导出（order-export）〔E1〕" in text


def test_every_listed_evidence_is_visible_elsewhere_in_the_export():
    """附录 A 的准入判据（本文引用过）与"正文里挂没挂编号"必须一致——
    对不上就是导出把内容吞了（P31 真跑对账就是靠这条抓出名称那一处）。"""

    text = export(document_with_feature(), ledger_with_two_entries())
    head, rest = text.split("## 附录 A", 1)
    appendix_a, _, tail = rest.partition("## 附录")
    listed = re.findall(r"^- (\S+) · ", appendix_a, re.M)

    assert listed, "夹具得真的引用过编号"
    assert set(listed) <= visible_tokens(head + tail)


def test_appendix_skips_evidence_only_the_open_question_list_cites():
    """附录 A 是"交付物的出处"，不是"未决清单的记账"——
    只被第 11 章引用的系统记录（正文里那句话的回声）不进附录（M7 B）。"""

    document = with_open_question(document_with_feature())
    text = export(document, ledger_with_three_entries())
    appendix_a = appendix_a_of(text)

    assert "- E1 · 用户输入 · 要做一个订单导出功能" in appendix_a
    assert "- E2 ·" not in appendix_a
    assert "单次导出应在 60 秒内完成" not in appendix_a


def test_appendix_keeps_evidence_that_real_content_also_cites():
    """s11 与正文都引用了同一条 → 它在正文里有落点，照样进附录。"""

    document = with_open_question(document_with_feature(), refs=(EvidenceId("E2"),))
    # 正文里也引用 E2（同一编号既在未决清单里、又在功能字段里）
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-rules",
                entry_id=EntryId("en-rules"),
                op=WriteOp.ADD,
                target=Target.feature_field(
                           FeatureSlug("order-export"), "rules",
                           home=STD.feature_home
                       ),
                text="导出记录保留 30 天",
                refs=(EvidenceId("E2"),),
                kind=BlockKind.BULLET_LIST,
            )
        ],
        now=NOW,
        summary="补一条规则",
    )
    text = export(document, ledger_with_three_entries())
    appendix_a = appendix_a_of(text)

    assert "- E1 · 用户输入 · 要做一个订单导出功能" in appendix_a
    assert "- E2 · 席位判断 · 单次导出应在 60 秒内完成" in appendix_a
    assert "只被第 11 章未决清单引用" not in text


def test_appendix_reports_open_question_echoes_separately():
    """两类"没进附录"的要说清是两类：只被未决清单引用的、完全没被引用的。"""

    document = with_open_question(document_with_feature())
    text = export(document, ledger_with_three_entries())

    assert "另有 1 条出处只被第 11 章未决清单引用" in text
    assert "另有 1 条出处没有被本文引用" in text


def test_appendix_still_lists_everything_when_there_is_no_open_question():
    """没有未决清单时，行为与 P31 完全一致（B 只多一条排除口径）。"""

    text = export(document_with_feature(), ledger_with_three_entries())
    appendix_a = appendix_a_of(text)

    assert "- E1 · 用户输入 · 要做一个订单导出功能" in appendix_a
    assert "只被第 11 章未决清单引用" not in text
    assert "另有 2 条出处没有被本文引用" in text


def test_open_question_numbers_stay_visible_in_the_document():
    """B 只动附录：s11 条目照旧挂编号（读者在系统里按编号能查到那条）。"""

    document = with_open_question(document_with_feature())
    text = export(document, ledger_with_three_entries())
    body = text.split("## 附录 A")[0]

    assert "导出记录的保存期限是多少？" in body
    assert "〔E2〕" in body


def test_used_by_entries_still_finds_the_open_question():
    """界面那条通道（"这个编号被谁引用着"）**故意**与附录口径不同：它要能找到 s11 里的引用。"""

    from pmstudio.application.dto import used_by_entries
    from pmstudio.domain.shared.ids import EvidenceId as EID

    document = with_open_question(document_with_feature())

    assert used_by_entries(document.view(), EID("E2")) == ("en-open",)


def test_acceptance_entries_are_rendered_as_list_items():
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "acceptance", home=STD.feature_home),
                text="5 万条内 60 秒可下载",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.ACCEPTANCE,
            )
        ],
        now=NOW,
        summary="写验收",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )
    text = export(document, ledger_with_two_entries())

    assert "验收标准" in text
    assert "5 万条内 60 秒可下载〔E1〕" in text


def test_the_empty_section_note_does_not_talk_to_the_model():
    """空章那句"差什么"是给**读者**看的，不是给模型的操作提示。

    真跑现场（P30 五条车道）：第 6 章那行写的是
    「（本章为空：这类内容用 steps 块写；拿不准就出题问用户）」——后面半句是
    `readiness.KIND_PRESENCE` 的 detail（写给模型看的），漏进了交付物。
    空章提示只许拿**结构性**缺口（`section_min_entries`）的 detail；
    结构上没缺口时就是干净的「（本章为空）」。
    """

    document = document_with_feature()
    text = export(
        document,
        ledger_with_two_entries(),
        ReadinessChecker().check(document.view(), adapter=STD),
    )
    s6 = text.split("## 6 业务流程与交互", 1)[1].split("## 7 ", 1)[0]

    assert "（本章为空）" in s6
    assert "出题问用户" not in s6
    assert "steps 块写" not in s6


def test_rendering_is_deterministic():
    document, ledger = document_with_feature(), ledger_with_two_entries()
    assert export(document, ledger) == export(document, ledger)


def test_filename_carries_title_and_version():
    result = render_markdown(
        view=make_document().view(),
        readiness=(),
        versions=make_document().versions,
        ledger=ledger_with_two_entries(),
        exported_at=EXPORTED_AT,
        adapter=STD,
    )
    assert result.filename == "订单中台-v1.md"
    assert result.version_no == 1


def test_readiness_appendix_lists_unfinished_items():
    document = make_document()
    items = (
        ReadinessChecker().check(document.view(), adapter=STD)[0],
    )
    text = export(document, ledger_with_two_entries(), items)

    assert "未完成项" in text
    assert f"{items[0].section_id}" in text
    assert items[0].title in text
    assert items[0].severity is Severity.BLOCKER


# ── M11：模拟推演的诚实标注（同时补 M10 欠账） ───────────────────────────────


def _ledger_with_simulated() -> tuple[EvidenceLedger, EvidenceId]:  # type: ignore[no-untyped-def]
    ledger = ledger_with_two_entries()
    eid = ledger.record(
        kind=EvidenceKind.SIMULATED,
        text="点了导出没有反应；文档没写失败算不算正常",
        source=EvidenceSource(
            round_id=RoundId("r1"),
            external_ref="simulation:review:r1#persona.first_touch@order-export#F1",
        ),
        now=NOW,
    )
    return ledger, eid


def test_simulated_evidence_gets_its_own_label_and_disclaimer() -> None:
    from pmstudio.application.deliverable import SIMULATION_DISCLAIMER

    ledger, eid = _ledger_with_simulated()
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-sim",
                entry_id=EntryId("en-sim"),
                op=WriteOp.ADD,
                target=Target.body("s2"),
                text="导出失败要有反馈（模型推演，待验证）",
                refs=(eid,),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="引用一条模拟编号",
    )

    text = export(document, ledger)

    assert "模拟推演（未验证）" in text, "模拟编号在附录里不能裸奔成英文原值"
    assert SIMULATION_DISCLAIMER in text, "引用了模拟内容就必须带固定声明"


def test_clean_export_has_no_simulation_disclaimer() -> None:
    from pmstudio.application.deliverable import SIMULATION_DISCLAIMER

    text = export(document_with_feature(), ledger_with_two_entries())

    assert SIMULATION_DISCLAIMER not in text, "没引用模拟内容就不许吓人"
    assert "模拟推演" not in text


# —— 章号/功能号走模板顺序（`02` §11.0.69）：不再从 `sN` 剥前缀 ——


def _light_template():
    from pmstudio.domain.shared.template import (
        DocumentTemplate,
        TemplatedSection,
        TemplateRole,
    )

    def section(sid: str, title: str, role=TemplateRole.PLAIN) -> TemplatedSection:
        return TemplatedSection(
            section_id=SectionId(sid), title=title, purpose="摆设", role=role
        )

    return DocumentTemplate(
        template_id="light",
        title="轻量版",
        sections=(
            section("s1", "背景与问题", TemplateRole.BACKBONE),
            section("s2", "目标与非目标", TemplateRole.BACKBONE),
            section("s4", "范围与边界", TemplateRole.BACKBONE),
            section("s5", "功能需求", TemplateRole.FEATURE),
            section("s11", "风险与开放问题", TemplateRole.OPEN_QUESTIONS),
            section("s13", "版本历史", TemplateRole.VERSION_HISTORY),
        ),
    )


def _render(document, *, adapter, ledger=None):
    return render_markdown(
        view=document.view(),
        readiness=(),
        versions=document.versions,
        ledger=ledger if ledger is not None else EvidenceLedger(project_id=PROJECT),
        exported_at=NOW,
        adapter=adapter,
    ).markdown


def _ledger_with(text: str) -> tuple[EvidenceLedger, EvidenceId]:
    """账本 + 它分到的编号（条目要引真编号，编号是账本给的）。"""

    ledger = EvidenceLedger(project_id=PROJECT)
    eid = ledger.record(
        kind=EvidenceKind.USER_INPUT,
        text=text,
        source=EvidenceSource(round_id=RoundId("r1"), raw_span=RAW_SPAN),
        now=NOW,
    )
    return ledger, eid


def test_light_export_numbers_chapters_by_its_own_six():
    """light 的章号是 1..6（s4→3、s5→4、s11→5、s13→6），不是从 id 剥 `s`。"""

    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.template_adapter import adapt

    template = _light_template()
    document = Document.new(
        document_id=DocumentId("d1"), project_id=ProjectId("p1"), title="轻量", now=NOW,
        template=template,
    )

    text = _render(document, adapter=adapt(template))

    assert "## 1 背景与问题" in text
    assert "## 3 范围与边界" in text
    assert "## 4 功能需求" in text
    assert "## 5 风险与开放问题" in text
    assert "## 6 版本历史" in text
    assert "## 2 目标与非目标" in text


def test_light_export_numbers_features_under_their_own_chapter():
    """功能号用功能锚点章的序号：light 里功能章排第 4 → `### 4.1`（不是 `### 5.1`）。"""

    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.authoring.instruction import WriteInstruction
    from pmstudio.domain.shared.template_adapter import adapt

    template = _light_template()
    document = Document.new(
        document_id=DocumentId("d1"), project_id=ProjectId("p1"), title="轻量", now=NOW,
        template=template,
    )
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.feature_field(
                    "order-export", "name", home=adapt(template).feature_home
                ),
                text="订单导出",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="写一个功能",
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )

    text = _render(document, adapter=adapt(template))

    assert "### 4.1 订单导出（order-export）〔E1〕" in text


def test_version_chapter_renders_versions_by_role_not_by_id():
    """版本历史章由 role 指认（standard 的 s13 之外，自定义模板里可能是别的 id）。"""

    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.template import template_from_dict
    from pmstudio.domain.shared.template_adapter import adapt

    template = template_from_dict(
        {
            "template_id": "t",
            "title": "x",
            "sections": [
                {"id": "overview", "title": "概览", "purpose": "p"},
                {"id": "feat", "title": "功能", "purpose": "p", "role": "feature"},
                {"id": "open", "title": "未决", "purpose": "p", "role": "open_questions"},
                {
                    "id": "history",
                    "title": "版本",
                    "purpose": "p",
                    "role": "version_history",
                },
            ],
        }
    )
    document = Document.new(
        document_id=DocumentId("d1"), project_id=ProjectId("p1"), title="自建", now=NOW,
        template=template,
    )

    text = _render(document, adapter=adapt(template))

    assert "## 4 版本" in text
    assert "| 版本 | 时间 | 触发 | 摘要 |" in text


def test_open_questions_chapter_is_skipped_in_the_citation_appendix():
    """附录 A 跳过的"未决章"取模板锚点；那句"第 N 章"也用它自己的序号（light 的 s11 是第 5 章）。"""

    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.authoring.instruction import WriteInstruction
    from pmstudio.domain.shared.template_adapter import adapt

    template = _light_template()
    ledger, eid = _ledger_with("量级在 1 万级")
    document = Document.new(
        document_id=DocumentId("d1"), project_id=ProjectId("p1"), title="轻量", now=NOW,
        template=template,
    )
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body(adapt(template).open_home),
                text="〔未决·阻塞〕量级是多少（未决定，待决人：PM；跳过后果：按同步做）〔去处：s2〕",
                refs=(eid,),
                kind=BlockKind.QUESTION,
            )
        ],
        now=NOW,
        summary="留一条未决",
    )

    text = _render(document, adapter=adapt(template), ledger=ledger)

    # 它的口径是"不列入清单、但仍可按编号查"：编号不进正文，也不出现在附录清单里
    assert "只被第 5 章未决清单引用" in text
    assert f"- {eid} ·" not in text


def with_list_like_entry(document: Document) -> Document:
    """模型写的条目是多行 markdown（真跑现场：goal 字段里 "- 解决…\\n- 通过…\\n- 丰富…"）。"""

    document.apply(
        [
            WriteInstruction(
                instruction_id="i-list",
                entry_id=EntryId("en-list"),
                op=WriteOp.ADD,
                target=Target.body("s1"),
                text="- 解决老人信息触达难问题\n- 通过数字化流程提升效率\n- 丰富社区文化生活",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="落一条列表式正文",
    )
    return document


def test_a_list_like_entry_is_rendered_with_a_single_dash():
    """渲染器自己加 `- `，正文里的那个必须剥掉一个——否则导出里出现 `- - `。"""

    text = export(with_list_like_entry(make_document()), ledger_with_two_entries())

    assert "- 解决老人信息触达难问题" in text
    assert "- - " not in text


def test_continuation_lines_keep_their_indent():
    """换行续行不能顶格：否则第二行看着像另一条，还丢了编号归属。"""

    text = export(with_list_like_entry(make_document()), ledger_with_two_entries())

    assert "\n  通过数字化流程提升效率" in text
    assert "\n- 通过数字化" not in text
    assert "\n  - 通过数字化" not in text


def test_plain_text_is_rendered_exactly_as_before():
    """没列表符的正文逐字不变（老用例全过即证，这里钉住"只动有标记的行"）。"""

    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-plain",
                entry_id=EntryId("en-plain"),
                op=WriteOp.ADD,
                target=Target.body("s1"),
                text="社区食堂现在靠手工登记",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="落一条普通正文",
    )
    text = export(document, ledger_with_two_entries())

    assert "\n- 社区食堂现在靠手工登记〔E1〕" in text


def test_only_unordered_markers_are_stripped():
    """`-5`（负数）与 `2024. 年报`（有序编号）原样保留：剥错了就是改用户内容。"""

    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i-num",
                entry_id=EntryId("en-num"),
                op=WriteOp.ADD,
                target=Target.body("s1"),
                text="-5 个窗口\n2024. 年报已归档",
                refs=(EvidenceId("E1"),),
                kind=BlockKind.TEXT,
            )
        ],
        now=NOW,
        summary="落一条数字正文",
    )
    text = export(document, ledger_with_two_entries())

    assert "- -5 个窗口" in text  # 子弹 + "-5" 原文：解析回 "-5" 是忠实的
    assert "- - " not in text  # artifact 形态（marker + 空格）不在
    assert "\n  2024. 年报已归档" in text  # 有序编号不动，续行缩进
