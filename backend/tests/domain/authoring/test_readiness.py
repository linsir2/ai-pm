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
    SectionId,
)
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.template import (
    DocumentTemplate,
    TemplatedSection,
    TemplateRole,
    standard_template,
    template_from_dict,
)
from pmstudio.domain.shared.template_adapter import adapt
from pmstudio.domain.shared.writing import BlockKind, WriteOp
from tests.support.builders import NOW, STD, make_document

REF = EvidenceId("E1")
ALL_SECTIONS = ("s1", "s2", "s3", "s4", "s8", "s10", "s12")


def empty_doc() -> Document:
    return Document.new(
        document_id=DocumentId("d1"), project_id=ProjectId("p1"), title="订单中台", now=NOW
    )


def by_key(document: Document) -> dict[tuple[str, str], object]:
    items = ReadinessChecker().check(document.view(), adapter=STD)
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
        write(document, target=Target.feature_field(
                                   slug, "one_liner",
                                   home=STD.feature_home
                               ), text=f"{slug} 的一句话")
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
        for item in ReadinessChecker().check(two_thin_features().view(), adapter=STD)
    ]
    assert len(keys) == len(set(keys))


def test_section_13_is_never_reported():
    assert all(
        item.section_id != "s13"
        for item in ReadinessChecker().check(empty_doc().view(), adapter=STD)
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

    items = ReadinessChecker().check(two_thin_features().view(), adapter=STD)

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


# —— 模板驱动（`02` §11.0.69）：判据读文档自己的模板，不再读全局 id 表 ——


def _light_template():
    """6 章轻量模板（与 `light.toml` 同形；domain 测试不碰 infrastructure）。"""

    def section(
        section_id: str, title: str, role: TemplateRole = TemplateRole.PLAIN
    ) -> TemplatedSection:
        return TemplatedSection(
            section_id=SectionId(section_id), title=title, purpose="摆设", role=role
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


def _custom_template():
    """自由 id 的 5 章模板：验证"章节 id 不是 sN"时判据照跑。"""

    return template_from_dict(
        {
            "template_id": "t",
            "title": "自建",
            "sections": [
                {
                    "id": "overview",
                    "title": "概览",
                    "purpose": "为什么做这件事",
                    "min_entries": 2,
                },
                {"id": "facts", "title": "事实", "purpose": "摆事实"},
                {
                    "id": "quality",
                    "title": "质量",
                    "purpose": "质量怎么保证",
                    "slots": [
                        {"name": "speed", "title": "速度", "kind": "nfr"},
                        {"name": "safety", "title": "安全", "kind": "nfr"},
                    ],
                },
                {"id": "feat", "title": "功能", "purpose": "做什么", "role": "feature"},
                {
                    "id": "open",
                    "title": "未决",
                    "purpose": "还没定的",
                    "role": "open_questions",
                },
                {
                    "id": "history",
                    "title": "版本",
                    "purpose": "不用手写",
                    "role": "version_history",
                },
            ],
        }
    )


def _doc_with(template) -> Document:  # type: ignore[no-untyped-def]
    return Document.new(
        document_id=DocumentId("d1"),
        project_id=ProjectId("p1"),
        title="文档",
        now=NOW,
        template=template,
    )


def test_light_document_is_only_judged_on_its_own_chapters():
    """light 只有 6 章：模板外的章（s3/s6/s7/s8/s10/s12）一条判据都不该出。"""

    items = ReadinessChecker().check(
        _doc_with(_light_template()).view(), adapter=adapt(_light_template())
    )

    assert {item.section_id for item in items} == {"s1", "s2", "s4", "s5"}
    assert all(item.severity is Severity.BLOCKER for item in items)


def test_custom_chapter_ids_are_judged_by_declaration_not_by_s_n():
    """自由 id 的章照判：`overview` 声明了"至少 2 条"，`facts` 没声明就不判。"""

    template = _custom_template()
    document = _doc_with(template)
    write(document, target=Target.body("overview"), text="第一条")

    items = ReadinessChecker().check(document.view(), adapter=adapt(template))
    overview = [item for item in items if item.section_id == "overview"]

    assert [item.title for item in overview] == ["概览还缺 1 条内容"]
    assert not [item for item in items if item.section_id == "facts"]


def test_custom_chapter_detail_falls_back_to_the_template_purpose():
    template = _custom_template()

    items = ReadinessChecker().check(_doc_with(template).view(), adapter=adapt(template))
    overview = next(item for item in items if item.section_id == "overview")

    assert overview.detail == "至少 2 条内容：为什么做这件事"


def test_slotted_chapter_is_judged_slot_by_slot():
    """有槽的章逐槽判（standard 的 s8 六项表态就是它）；detail 列缺哪几项。"""

    template = _custom_template()
    document = _doc_with(template)

    items = ReadinessChecker().check(document.view(), adapter=adapt(template))
    quality = next(item for item in items if item.section_id == "quality")

    assert quality.title == "质量还缺 2 项表态"
    assert quality.detail == "速度、安全"


def test_cross_section_link_hint_follows_the_data_role():
    """跨章链接启发式只在模板标了 `role = "data"` 的章上跑（standard 的 s7）。"""

    standard = standard_template()
    with_dependencies = two_thin_features()
    write(
        with_dependencies,
        target=Target.feature_field(
            "order-export", "dependencies", home=STD.feature_home
        ),
        text="订单服务",
    )

    items = ReadinessChecker().check(with_dependencies.view(), adapter=adapt(standard))
    assert [item.section_id for item in items if item.code == "cross_section_link"] == ["s7"]

    # light 没有数据与接口这类章 → 整条启发式不跑（不是跑到别的章上）
    light = _light_template()
    items_light = ReadinessChecker().check(with_dependencies.view(), adapter=adapt(light))
    assert not [item for item in items_light if item.code == "cross_section_link"]
