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
    EntryId,
    EvidenceId,
    FeatureSlug,
    RoundId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, PROJECT, document_with_feature, make_document

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


def export(document: Document, ledger: EvidenceLedger, readiness=()) -> str:  # type: ignore[no-untyped-def]
    return render_markdown(
        view=document.view(),
        readiness=readiness,
        versions=document.versions,
        ledger=ledger,
        exported_at=EXPORTED_AT,
    ).markdown


def visible_tokens(text: str) -> set[str]:
    """正文里出现的编号。〔…〕里还有「未决·阻塞」这类标记与「去处：…」这类地址，只认 E<数字>。"""

    found: set[str] = set()
    for block in re.findall(r"〔([^〕]*)〕", text):
        found.update(part for part in block.split() if re.fullmatch(r"E\d+", part))
    return found


def test_header_carries_title_version_time_and_readiness_stamp():
    document = make_document()
    text = export(
        document,
        ledger_with_two_entries(),
        ReadinessChecker().check(document.view()),
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
        ReadinessChecker().check(make_document().view()),
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
                target=Target.feature_field(FeatureSlug("order-export"), "name"),
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


def test_acceptance_entries_are_rendered_as_list_items():
    document = make_document()
    document.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.feature_field("order-export", "acceptance"),
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
        ReadinessChecker().check(document.view()),
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
    )
    assert result.filename == "订单中台-v1.md"
    assert result.version_no == 1


def test_readiness_appendix_lists_unfinished_items():
    document = make_document()
    items = (
        ReadinessChecker().check(document.view())[0],
    )
    text = export(document, ledger_with_two_entries(), items)

    assert "未完成项" in text
    assert f"{items[0].section_id}" in text
    assert items[0].title in text
    assert items[0].severity is Severity.BLOCKER
