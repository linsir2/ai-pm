"""交付排版的消费者测试：真实聚合 → Markdown → HTML，检查阅读结构与引用保真。"""

from dataclasses import replace

import pytest
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt

from pmstudio.application.deliverable import render_markdown
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessChecker
from pmstudio.domain.provenance.evidence import EvidenceKind, EvidenceSource
from pmstudio.domain.provenance.ledger import EvidenceLedger
from pmstudio.domain.shared.ids import EntryId, EvidenceId, FeatureSlug, RoundId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, STD, make_document


def put(
    document: Document, text: str, *, target: Target | None = None,
    kind: BlockKind = BlockKind.TEXT, refs: tuple[EvidenceId, ...] = (EvidenceId("E1"),),
) -> None:
    key = f"entry-{len(document.versions)}"
    document.apply(
        [WriteInstruction(
            instruction_id=key, entry_id=EntryId(key), op=WriteOp.ADD,
            target=target or Target.body("s1"), text=text, refs=refs, kind=kind,
        )],
        now=NOW, summary=key,
        authorized_new_features=frozenset({FeatureSlug("order-export")}),
    )


def ledger_of(*texts: str) -> EvidenceLedger:
    ledger = EvidenceLedger.empty(PROJECT)
    for text in texts:
        ledger.record(
            kind=EvidenceKind.USER_INPUT, text=text,
            source=EvidenceSource(round_id=RoundId("r1"), raw_span=text), now=NOW,
        )
    return ledger


def exported(document: Document, ledger: EvidenceLedger | None = None) -> str:
    return render_markdown(
        view=document.view(), ledger=ledger or ledger_of("原始依据"),
        readiness=ReadinessChecker().check(document.view(), adapter=STD),
        versions=document.versions, exported_at=NOW, adapter=STD,
    ).markdown


def html_of(text: str) -> BeautifulSoup:
    return BeautifulSoup(MarkdownIt("commonmark").enable("table").render(text), "html.parser")


def test_feature_name_is_the_heading_and_identity_remains_in_metadata():
    document = make_document()
    put(
        document, "订单导出",
        target=Target.feature_field("order-export", "name", home=STD.feature_home),
    )
    put(
        document, "下载订单",
        target=Target.feature_field("order-export", "one_liner", home=STD.feature_home),
    )
    html = html_of(exported(document))

    assert html.find("h3").get_text() == "5.1 订单导出"
    assert "标识：order-export" in html.get_text()
    assert "〔E1〕" in html.get_text()
    assert "一句话描述" in [node.get_text() for node in html.find_all("h4")]


def test_semicolon_steps_become_three_ordered_steps_without_changing_numbers():
    document = make_document()
    put(
        document, "1. 筛选最近30天订单；2. 校验运营权限；3. 在1.5秒内生成文件",
        target=Target.feature_field("order-export", "main_flow", home=STD.feature_home),
        kind=BlockKind.STEPS,
    )
    html = html_of(exported(document))
    assert [node.get_text().split("〔")[0].rstrip("；;") for node in html.select("ol > li")] == [
        "筛选最近30天订单", "校验运营权限", "在1.5秒内生成文件",
    ]


def test_unstructured_steps_do_not_split_a_semicolon_in_the_content():
    document = make_document()
    put(
        document, "校验选项A；选项B保持原样",
        target=Target.feature_field("order-export", "main_flow", home=STD.feature_home),
        kind=BlockKind.STEPS,
    )
    html = html_of(exported(document))
    assert [node.get_text() for node in html.select("ol > li")] == [
        "校验选项A；选项B保持原样〔E1〕",
    ]


def test_multiline_acceptance_is_three_readable_items_not_one_soft_wrapped_line():
    document = make_document()
    put(
        document, "运营角色可以导出\n非运营角色被拒绝\n文件包含全部筛选结果",
        target=Target.feature_field("order-export", "acceptance", home=STD.feature_home),
        kind=BlockKind.ACCEPTANCE,
    )
    html = html_of(exported(document))
    text = [node.get_text() for node in html.find_all("li")]
    assert "运营角色可以导出" in text
    assert "非运营角色被拒绝" in text
    assert "文件包含全部筛选结果〔E1〕" in text


def test_nonempty_chapter_keeps_its_remaining_gap_locally_without_appendix_c():
    document = make_document()
    put(document, "提升导出效率", target=Target.body("s2"))
    text = exported(document)
    section = text.split("## 2 目标与非目标", 1)[1].split("## 3 ", 1)[0]
    assert "目标与非目标还缺 1 条内容" in section
    assert "目标（可验证）与非目标各一条" in section
    assert text.count("目标与非目标还缺 1 条内容") == 1
    assert "附录 C" not in text


def test_kind_presence_warning_keeps_human_title_without_model_instructions():
    document = make_document()
    put(document, "已有一条背景")
    text = exported(document)
    section = text.split("## 9 技术方案与约束", 1)[1].split("## 10 ", 1)[0]
    assert "还没有技术决策" in section
    assert "出题问用户" not in text
    assert "decision 块" not in text


def test_citation_stays_outside_a_code_fence_and_later_headings_are_not_swallowed():
    document = make_document()
    put(document, "```python\nvalue = 1.5\n```")
    html = html_of(exported(document))
    assert html.select_one("pre > code").get_text() == "value = 1.5\n"
    assert html.find("h2", string="2 目标与非目标") is not None
    assert "〔E1〕" in html.get_text()


def test_preference_code_fence_keeps_its_content_and_citation_outside_the_code():
    document = make_document()
    put(document, "```python\nmode = 'compact'\n```", target=Target.preference())
    html = html_of(exported(document))
    assert html.select_one("pre > code").get_text() == "mode = 'compact'\n"
    assert "〔E1〕" in html.get_text()


@pytest.mark.parametrize("kind", [BlockKind.TABLE, BlockKind.TEXT])
def test_a_user_table_keeps_its_cell_values_and_citation_is_outside_the_table(kind):
    document = make_document()
    put(document, "| 用户 | 权限 |\n|---|---|\n| Alice | reader |", kind=kind)
    html = html_of(exported(document))
    table = html.find("table")
    assert [cell.get_text() for cell in table.select("tbody td")] == ["Alice", "reader"]
    assert "E1" not in table.get_text()
    assert "〔E1〕" in html.get_text()


def test_source_index_merges_only_actual_consecutive_ids_and_does_not_repeat_summaries():
    document = make_document()
    ledger = ledger_of("依据一", "依据二", "依据三", "未引用依据四", "独有的证据摘要长文本")
    put(document, "已确认方案", refs=tuple(EvidenceId(eid) for eid in ("E1", "E2", "E3", "E5")))
    appendix = exported(document, ledger).split("## 附录 A", 1)[1]
    html = html_of(appendix)
    rows = html.select("tbody tr")
    assert len(rows) == 1
    assert [cell.get_text() for cell in rows[0].find_all("td")] == [
        "用户输入", "—", "E1–E3、E5",
    ]
    assert "独有的证据摘要长文本" not in appendix
    assert "E4" not in appendix
    assert "另有 1 条出处没有被本文引用" in appendix


def test_source_types_are_not_merged_when_they_reference_the_same_material():
    document = make_document()
    ledger = EvidenceLedger.empty(PROJECT)
    eids = tuple(ledger.record(
        kind=kind, text="材料内容", source=EvidenceSource(external_ref="attachment:需求.md#c00"),
        now=NOW,
    ) for kind in (EvidenceKind.EXTERNAL, EvidenceKind.SIMULATED))
    put(document, "确认内容", refs=eids)
    html = html_of(exported(document, ledger).split("## 附录 A", 1)[1])
    assert [row.find("td").get_text() for row in html.select("tbody tr")] == [
        "外部材料", "模拟推演（未验证）",
    ]


def test_same_attachment_chunks_share_a_source_but_urls_keep_their_fragments():
    document = make_document()
    ledger = EvidenceLedger.empty(PROJECT)
    sources = ("attachment:需求.md#c00", "attachment:需求.md#c01",
               "https://example.com/a#c00", "https://example.com/a#c01")
    eids = tuple(ledger.record(
        kind=EvidenceKind.EXTERNAL, text="材料内容",
        source=EvidenceSource(external_ref=source), now=NOW,
    ) for source in sources)
    put(document, "确认内容", refs=eids)
    html = html_of(exported(document, ledger).split("## 附录 A", 1)[1])
    rows = [[cell.get_text() for cell in row.find_all("td")] for row in html.select("tbody tr")]
    assert rows == [
        ["外部材料", "attachment:需求.md", "E1–E2"],
        ["外部材料", "https://example.com/a#c00", "E3"],
        ["外部材料", "https://example.com/a#c01", "E4"],
    ]


def test_export_shows_five_recent_versions_without_removing_stored_versions():
    document = make_document()
    for index in range(6):
        put(document, f"第{index + 1}条内容")
    before = document.view()
    html = html_of(exported(document))
    table = html.find("h2", string="13 版本历史").find_next("table")
    assert [row.find("td").get_text() for row in table.select("tbody tr")] == [
        "v7", "v6", "v5", "v4", "v3",
    ]
    assert "共 7 版" in html.get_text()
    assert len(document.versions) == 7
    assert document.view() == before


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_version_summary_with_pipe_and_newline_does_not_change_table_columns(newline):
    document = make_document()
    versions = (replace(document.versions[0], summary=f"确认 A|B{newline}保留后续内容"),)
    text = render_markdown(
        view=document.view(), ledger=ledger_of("原始依据"), readiness=(),
        versions=versions, exported_at=NOW, adapter=STD,
    ).markdown
    html = html_of(text)
    cells = html.find("table").select("tbody td")
    assert len(cells) == 4
    assert "A|B" in cells[-1].get_text()
    assert "保留后续内容" in cells[-1].get_text()
