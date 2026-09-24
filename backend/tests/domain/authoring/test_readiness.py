"""就绪度：把 `01-规格.md` §4 的合格标准跑成可判据的清单。

三条实现约定也在这里钉住：只有结构缺失算 blocker；同一 (章节, code) 只出一条；
s13 由版本生成，永不参与判定。
"""

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.readiness import ReadinessChecker, Severity
from pmstudio.domain.shared.ids import (
    BlockId,
    DocumentId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    ProjectId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, make_document

REF = EvidenceId("E1")
ALL_SECTIONS = ("s1", "s2", "s3", "s4", "s8", "s10", "s12")


def empty_doc() -> Document:
    return Document.new(
        document_id=DocumentId("d1"), project_id=ProjectId("p1"), title="订单中台", now=NOW
    )


def by_key(document: Document) -> dict[tuple[str, str], object]:
    items = ReadinessChecker().check(document.view())
    return {(item.section_id, item.code): item for item in items}


def write(document: Document, *, target: Target, text: str, kind: BlockKind | None = None) -> None:
    index = len(document.versions)
    document.apply(
        [
            WriteInstruction(
                instruction_id=f"i{index}",
                entry_id=EntryId(f"en{index}"),
                op=WriteOp.ADD,
                target=target,
                text=text,
                refs=(REF,),
                kind=kind,
            )
        ],
        now=NOW,
        summary="测试写入",
        authorized_new_features=frozenset(
            {FeatureSlug("order-export"), FeatureSlug("login-wechat")}
        ),
    )


def two_thin_features() -> Document:
    """两棵只写了 one_liner 的功能子树（必填字段缺一堆）。"""

    document = empty_doc()
    for slug in ("order-export", "login-wechat"):
        write(document, target=Target.feature_field(slug, "one_liner"), text=f"{slug} 的一句话")
    return document


def test_empty_document_reports_structural_blockers():
    items = by_key(empty_doc())

    for section in ALL_SECTIONS:
        assert items[(section, "section_min_entries")].severity is Severity.BLOCKER, section
    assert items[("s5", "feature_field_empty")].detail == "还没有任何功能"


def test_s8_item_lists_the_missing_slots():
    item = by_key(empty_doc())[("s8", "section_min_entries")]

    assert "6 项" in item.title
    assert "性能" in item.detail and "成本" in item.detail


def test_feature_gaps_are_one_item_with_per_feature_details():
    item = by_key(two_thin_features())[("s5", "feature_field_empty")]

    assert "order-export" in item.detail and "login-wechat" in item.detail
    assert "验收标准" in item.detail


def test_same_section_and_code_is_reported_once():
    keys = [
        (item.section_id, item.code)
        for item in ReadinessChecker().check(two_thin_features().view())
    ]
    assert len(keys) == len(set(keys))


def test_section_13_is_never_reported():
    assert all(
        item.section_id != "s13" for item in ReadinessChecker().check(empty_doc().view())
    )


def test_full_s8_slots_clear_that_item():
    document = empty_doc()
    for slot in (
        "performance",
        "availability",
        "security",
        "compatibility",
        "observability",
        "cost",
    ):
        write(document, target=Target.body("s8", field=slot), text=f"{slot} 有表态了")

    assert ("s8", "section_min_entries") not in by_key(document)


def test_plain_open_question_is_a_warning_and_blocking_one_is_a_blocker():
    document = empty_doc()
    write(document, target=Target.body("s11"), text="导出记录保留期未定（待决人：PM）")
    warning = by_key(document)[("s11", "open_blocking_question")]
    assert warning.severity is Severity.WARN
    assert "1 项" in warning.title

    document = empty_doc()
    write(
        document,
        target=Target.body("s11"),
        text="〔未决·阻塞〕单次导出最大行数未定（待决人：PM）",
    )
    blocker = by_key(document)[("s11", "open_blocking_question")]
    assert blocker.severity is Severity.BLOCKER
    assert "单次导出最大行数未定" in blocker.detail


def test_missing_main_flow_is_a_warning_when_features_exist():
    items = by_key(two_thin_features())
    assert items[("s6", "section_kind_present")].severity is Severity.WARN


def test_acceptance_entry_clears_the_s12_item():
    document = empty_doc()
    write(
        document,
        target=Target.body("s12"),
        text="5 万条内 60 秒可下载",
        kind=BlockKind.ACCEPTANCE,
    )
    write(document, target=Target.body("s12"), text="无权限账号不出现导出按钮")

    assert ("s12", "section_min_entries") not in by_key(document)


def test_seeded_document_view_carries_feature_gaps():
    """就绪度读的是 `DocumentView.feature_gaps`（`Document.gaps()` 的投影），不另写一套规则。"""

    view = two_thin_features().view()
    assert {str(gap.feature_slug) for gap in view.feature_gaps} == {"order-export", "login-wechat"}
    assert make_document().view().feature_gaps == ()


def test_slot_block_is_not_mistaken_for_a_feature_field():
    """槽块的 field 名不是功能字段：就绪度不能因为它去查功能字段表而炸。"""

    document = empty_doc()
    write(document, target=Target.body("s8", field="performance"), text="60 秒")

    block = document.view().sections[7].blocks[0]
    assert block.field == "performance"
    assert block.field_title == "性能"
    assert block.block_id == BlockId("s8.performance")
