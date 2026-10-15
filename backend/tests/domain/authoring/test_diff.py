"""版本差异：按 entry_id 比对。entry_id 稳定（原地替换 + 快照留旧值），
所以不需要文本相似度——这是当初"永久稳定 id"决定现在的红利。"""

from pmstudio.domain.authoring.diff import diff_snapshots
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.shared.ids import EntryId, EvidenceId, SectionId
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import WriteOp
from tests.support.builders import NOW, STD, document_with_feature


def instruction(
    *,
    target: Target,
    entry_id: str,
    text: str,
    op: WriteOp = WriteOp.ADD,
    refs: tuple[EvidenceId, ...] = (EvidenceId("E1"),),
) -> WriteInstruction:
    """本文件自己用的写入指令（形状与 test_document.py 的同名助手一致）。"""

    return WriteInstruction(
        instruction_id=f"i-{entry_id}",
        entry_id=EntryId(entry_id),
        op=op,
        target=target,
        text=text,
        refs=refs,
    )


def entry_id_of(document, field: str) -> str:
    """按字段名找它的第一条条目 id（diff 的比对单位是 entry_id）。"""

    for block in document.blocks:
        if block.field == field and block.entries:
            return str(block.entries[0].entry_id)
    raise AssertionError(f"没有 {field} 条目")


def test_changed_entry_is_reported_with_before_and_after():
    document = document_with_feature(one_liner="把订单导出成表格")
    before = document.current_version.snapshot
    document.apply(
        [
            instruction(
                target=Target.feature_field("order-export", "one_liner", home=STD.feature_home),
                entry_id=entry_id_of(document, "one_liner"),
                text="按筛选条件把订单导成表格",
                op=WriteOp.REPLACE,
                refs=(EvidenceId("E1"), EvidenceId("E9")),
            )
        ],
        now=NOW,
        summary="改一句话描述",
    )

    groups = diff_snapshots(before, document.current_version.snapshot, adapter=STD)

    assert [group.target_label for group in groups] == ["5. 功能需求 · order-export · 一句话描述"]
    assert groups[0].added == ()
    assert groups[0].removed == ()
    assert [item.before.text for item in groups[0].changed] == ["把订单导出成表格"]
    assert [item.after.text for item in groups[0].changed] == ["按筛选条件把订单导成表格"]
    assert groups[0].changed[0].after.refs == (EvidenceId("E1"), EvidenceId("E9"))


def test_added_entry_is_grouped_under_its_field():
    document = document_with_feature()
    before = document.current_version.snapshot
    document.apply(
        [
            instruction(
                target=Target.feature_field("order-export", "acceptance", home=STD.feature_home),
                entry_id="en-ac1",
                text="5 万条以内 60 秒可下载",
            )
        ],
        now=NOW,
        summary="加验收标准",
    )

    groups = diff_snapshots(before, document.current_version.snapshot, adapter=STD)

    assert [(group.target_label, [item.text for item in group.added]) for group in groups] == [
        ("5. 功能需求 · order-export · 验收标准", ["5 万条以内 60 秒可下载"])
    ]


def test_removed_entry_shows_up_in_the_reverse_direction():
    """删一条：正向看是"删除"，反向看就是"新增"（两个方向都要能算）。"""

    document = document_with_feature()
    entry_id = entry_id_of(document, "one_liner")
    document.apply(
        [
            instruction(
                target=Target.feature_field("order-export", "one_liner", home=STD.feature_home),
                entry_id=entry_id,
                text="删掉一句话描述",
                op=WriteOp.REMOVE,
            )
        ],
        now=NOW,
        summary="删一条",
    )

    groups = diff_snapshots(
        document.version(2).snapshot, document.current_version.snapshot, adapter=STD
    )
    backward = diff_snapshots(
        document.current_version.snapshot, document.version(2).snapshot, adapter=STD
    )

    assert [group.target_label for group in groups] == [
        "5. 功能需求 · order-export · 一句话描述"
    ]
    assert [item.entry_id for item in groups[0].removed] == [EntryId(entry_id)]
    assert [item.entry_id for item in backward[0].added] == [EntryId(entry_id)]


def test_unchanged_snapshots_produce_no_groups():
    document = document_with_feature()
    same = document.current_version.snapshot
    assert diff_snapshots(same, same, adapter=STD) == ()


def test_labels_cover_sections_slots_features_and_preference():
    """四种去处都有中文标签，界面不再自己抄一份名字。"""

    document = document_with_feature()
    document.apply(
        [
            instruction(target=Target.body("s1"), entry_id="en-s1", text="运营每天手工导表"),
            instruction(
                target=Target.body("s8", field="performance"),
                entry_id="en-perf",
                text="5 万条 60 秒",
            ),
            instruction(target=Target.preference(), entry_id="en-pref", text="登录页太丑"),
            instruction(
                target=Target.feature_field("order-export", "name", home=STD.feature_home),
                entry_id="en-name",
                text="订单导出",
            ),
        ],
        now=NOW,
        summary="铺四个去处",
    )

    groups = diff_snapshots(
        document.version(2).snapshot, document.current_version.snapshot, adapter=STD
    )

    assert [group.target_label for group in groups] == [
        "1. 背景与问题",
        "5. 功能需求 · 订单导出 · 名称",
        "8. 非功能需求 · 性能",
        "偏好区（不进交付物）",
    ]


def test_diff_is_deterministic():
    """同一对快照，调多少次都是同一个结果（"权威"的前提是确定性）。"""

    document = document_with_feature()
    before = document.version(1).snapshot
    after = document.current_version.snapshot

    forward = diff_snapshots(before, after, adapter=STD)
    backward = diff_snapshots(after, before, adapter=STD)

    assert [item.text for group in forward for item in group.added] == [
        item.text for group in backward for item in group.removed
    ]
    assert diff_snapshots(before, after, adapter=STD) == forward


# —— 分组标签走模板（`02` §11.0.69）：序号与标题都从模板来，不剥 `sN` ——


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


def _light_document():
    from pmstudio.domain.authoring.document import Document
    from pmstudio.domain.shared.ids import DocumentId, ProjectId

    return Document.new(
        document_id=DocumentId("d1"),
        project_id=ProjectId("p1"),
        title="轻量",
        now=NOW,
        template=_light_template(),
    )


def test_diff_labels_use_the_templates_chapter_numbers_and_titles():
    """light 的 `s4` 是第 3 章（不是 `4.`）：标签里的序号来自模板顺序。"""

    from pmstudio.domain.shared.template_adapter import adapt

    before = _light_document()
    after = _light_document()
    after.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body("s4"),
                text="本期只做导出",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="补一条",
    )

    groups = diff_snapshots(
        before.versions[0].snapshot, after.versions[1].snapshot, adapter=adapt(_light_template())
    )

    assert [group.target_label for group in groups] == ["3. 范围与边界"]


def test_diff_orders_groups_by_the_templates_own_chapter_order():
    """两章同时改：按模板顺序排（light 里 s2 在 s4 前，序号 2 < 3）。"""

    from pmstudio.domain.shared.template_adapter import adapt

    before = _light_document()
    after = _light_document()
    after.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body("s4"),
                text="范围",
                refs=(EvidenceId("E1"),),
            ),
            WriteInstruction(
                instruction_id="i2",
                entry_id=EntryId("en2"),
                op=WriteOp.ADD,
                target=Target.body("s2"),
                text="目标",
                refs=(EvidenceId("E1"),),
            ),
        ],
        now=NOW,
        summary="补两条",
    )

    groups = diff_snapshots(
        before.versions[0].snapshot, after.versions[1].snapshot, adapter=adapt(_light_template())
    )

    assert [group.target_label for group in groups] == ["2. 目标与非目标", "3. 范围与边界"]


def test_diff_of_a_chapter_the_template_lacks_keeps_the_raw_id():
    """模板里没有的章不编造标题：原样显示 id（说不知道，不猜）。"""

    from pmstudio.domain.shared.template_adapter import adapt

    before = _light_document()
    after = _light_document()
    after.apply(
        [
            WriteInstruction(
                instruction_id="i1",
                entry_id=EntryId("en1"),
                op=WriteOp.ADD,
                target=Target.body("s4"),
                text="范围",
                refs=(EvidenceId("E1"),),
            )
        ],
        now=NOW,
        summary="补一条",
    )
    # 用**只有三章**的模板去看 light 的差异：s4 不在里面
    from pmstudio.domain.shared.template import template_from_dict

    other = template_from_dict(
        {
            "template_id": "t",
            "title": "x",
            "sections": [
                {"id": "s5", "title": "功能", "purpose": "p", "role": "feature"},
                {"id": "s11", "title": "未决", "purpose": "p", "role": "open_questions"},
                {"id": "s13", "title": "版本", "purpose": "p", "role": "version_history"},
            ],
        }
    )

    groups = diff_snapshots(
        before.versions[0].snapshot, after.versions[1].snapshot, adapter=adapt(other)
    )

    assert [group.target_label for group in groups] == ["s4"]
