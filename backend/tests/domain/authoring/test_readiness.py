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


def test_a_settled_record_in_s11_is_not_an_open_question():
    """`〔已定〕…` 是"这条未决已经定了"的记录，**不算未决**（P28 T5）。

    为什么要有这个标记：s11 目标题的答案以前直接蒸发（9 条真案例，见
    `.codex/runs/2026-10-02/P28-T0测量.md`）——现在落进 s11，但它不是"还开着的问题"，
    不该被算成"有 N 项未决"，更不该挡交付。
    """

    document = empty_doc()
    write(
        document,
        target=Target.body("s11"),
        text="〔已定〕以 OMS 为唯一库存主数据源，平台侧只读。",
    )

    # 只看 s11 那一条：空文档本来就有一堆别的缺口（s1/s2/… 的必填）
    assert ("s11", "open_blocking_question") not in by_key(document)


def test_settled_records_do_not_hide_real_open_questions():
    """已定记录不算未决，但**不能把真未决也一起藏掉**。"""

    document = empty_doc()
    write(document, target=Target.body("s11"), text="〔已定〕以 OMS 为唯一库存主数据源。")
    write(document, target=Target.body("s11"), text="导出记录保留期未定（待决人：PM）")

    warning = by_key(document)[("s11", "open_blocking_question")]

    assert warning.severity is Severity.WARN
    assert "1 项" in warning.title


def test_missing_main_flow_is_a_warning_when_features_exist():
    items = by_key(two_thin_features())
    assert items[("s6", "section_kind_present")].severity is Severity.WARN


def test_missing_main_flow_is_still_not_a_blocker():
    """**护栏**：`main_flow` 缺了不挡交付——P37 的决定，P38 也没动它。

    P38 只往字段表与归位表加"每个功能都要写主流程"的**提示词压力**；
    判据一个字没改。这条钉住"日后别顺手把它升成必填"
    （升上去 = 每个缺主流程的功能加一条阻塞，P30 里到 0 阻塞的车道会立刻破）。
    """

    items = ReadinessChecker().check(two_thin_features().view())

    assert not any(
        item.severity is Severity.BLOCKER and "主流程" in item.title for item in items
    )


def test_a_section_level_steps_entry_in_s6_clears_that_warning():
    """第 6 章**章节级**的 `steps` 条目是这条 warn 的解——落得进去，也认得出来。

    这是**护栏**用例（能力本来就在：`document._apply_add` 对章节级目标走
    `Block.section_content(kind=…)`），不是红用例；钉它是因为 P36 起归位表会
    真的往这儿派活（真跑 449 条提案里落 s6 的只有 1 条、而且 kind 是 text）。
    """

    document = two_thin_features()
    assert ("s6", "section_kind_present") in by_key(document)  # 先确认在告警状态

    write(
        document,
        target=Target.body("s6"),
        text="1. 点检员按班次点检；2. 发现异常点报修；3. 主管派单、维修工抢单到场。",
        kind=BlockKind.STEPS,
    )

    assert ("s6", "section_kind_present") not in by_key(document)


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
