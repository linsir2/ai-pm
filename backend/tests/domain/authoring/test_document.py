from dataclasses import FrozenInstanceError, replace
from datetime import datetime

import pytest

from pmstudio.domain.authoring.document import Document
from pmstudio.domain.authoring.instruction import WriteInstruction
from pmstudio.domain.authoring.version import VersionTrigger
from pmstudio.domain.shared.errors import (
    BlockManuallyEdited,
    EmptyApply,
    FeatureRequiresUserNaming,
    SectionOrderViolation,
    SectionSetViolation,
    UnknownBlock,
)
from pmstudio.domain.shared.ids import (
    BlockId,
    DocumentId,
    EntryId,
    EvidenceId,
    FeatureSlug,
    ProjectId,
)
from pmstudio.domain.shared.invariants import DInvariant
from pmstudio.domain.shared.target import Target
from pmstudio.domain.shared.writing import BlockKind, WriteOp

NOW = datetime(2026, 9, 22, 10, 0, 0)
REF = EvidenceId("E1")


def new_document() -> Document:
    return Document.new(
        document_id=DocumentId("doc1"),
        project_id=ProjectId("p1"),
        title="测试文档",
        now=NOW,
    )


def test_new_document_creates_six_empty_nfr_slots():
    """s8 的六个章节槽是骨架：空块不算内容，但必须存在（判据按槽名找它们）。"""

    document = new_document()

    slots = [block for block in document.blocks if str(block.block_id).startswith("s8.")]
    assert sorted(str(block.block_id) for block in slots) == [
        "s8.availability",
        "s8.compatibility",
        "s8.cost",
        "s8.observability",
        "s8.performance",
        "s8.security",
    ]
    assert all(block.kind is BlockKind.NFR for block in slots)
    assert all(block.entries == () for block in slots)


def instruction(
    *,
    target: Target,
    entry_id: str = "en1",
    text: str = "一条内容",
    op: WriteOp = WriteOp.ADD,
    kind: BlockKind | None = None,
    refs: tuple[EvidenceId, ...] = (REF,),
) -> WriteInstruction:
    return WriteInstruction(
        instruction_id=f"i-{entry_id}",
        entry_id=EntryId(entry_id),
        op=op,
        target=target,
        text=text,
        refs=refs,
        kind=kind,
    )


def apply(document: Document, *instructions: WriteInstruction, authorized: tuple[str, ...] = ()):
    return document.apply(
        instructions,
        now=NOW,
        summary="测试写入",
        authorized_new_features=frozenset(FeatureSlug(slug) for slug in authorized),
    )


def test_D1_ok_document_new_has_thirteen_sections():
    document = new_document()
    view = document.view()
    assert [section.section_id for section in view.sections] == [f"s{i}" for i in range(1, 14)]
    assert view.version_no == 1
    assert document.current_version.trigger is VersionTrigger.MANUAL


def test_D1_bad_missing_section():
    template = new_document()
    blocks = tuple(block for block in template.blocks if block.block_id != BlockId("s7"))
    with pytest.raises(SectionSetViolation) as exc:
        Document(
            document_id=DocumentId("doc1"),
            project_id=ProjectId("p1"),
            title="t",
            blocks=blocks,
            versions=template.versions,
            created_at=NOW,
        )
    assert exc.value.invariant == DInvariant.D1


def test_D1_bad_section_order():
    template = new_document()
    blocks = list(template.blocks)
    blocks[2], blocks[3] = blocks[3], blocks[2]
    with pytest.raises(SectionOrderViolation) as exc:
        Document(
            document_id=DocumentId("doc1"),
            project_id=ProjectId("p1"),
            title="t",
            blocks=tuple(blocks),
            versions=template.versions,
            created_at=NOW,
        )
    assert exc.value.invariant == DInvariant.D1


def test_D2_ok_entry_replace_keeps_block_id():
    document = new_document()
    first = apply(document, instruction(target=Target.body("s1"), text="现状：人工导表"))
    block_id = first.changed_block_ids[0]
    second = apply(
        document,
        instruction(
            target=Target.body("s1"),
            entry_id="en1",
            text="现状：人工导表，每天一次",
            op=WriteOp.REPLACE,
        ),
    )
    assert second.changed_block_ids == (block_id,)
    assert document.block(block_id).entries[0].text == "现状：人工导表，每天一次"
    assert document.version(1).snapshot.blocks != document.blocks


def test_D2_bad_removed_block_id_is_not_reused():
    document = new_document()
    apply(document, instruction(target=Target.body("s1"), text="第一条"))
    removed = BlockId("s1.b1")
    apply(
        document,
        instruction(
            target=Target.body("s1"),
            entry_id="s1.b1",
            text="删掉这条",
            op=WriteOp.REMOVE,
        ),
    )
    assert [block.block_id for block in document.tombstones] == [removed]
    with pytest.raises(UnknownBlock):
        document.block(removed)
    version = apply(document, instruction(target=Target.body("s1"), entry_id="en2", text="第二条"))
    assert version.changed_block_ids == (BlockId("s1.b2"),)


def test_D3_ok_feature_with_authorization():
    document = new_document()
    target = Target.feature_field("order-export", "one_liner")
    version = apply(
        document,
        instruction(target=target, text="运营按时间导出订单"),
        authorized=("order-export",),
    )
    root = document.feature(FeatureSlug("order-export"))
    assert root is not None
    assert root.block_id == BlockId("feat-order-export")
    assert version.changed_block_ids == (
        BlockId("feat-order-export"),
        BlockId("feat-order-export.one_liner"),
    )


def test_D3_bad_feature_without_authorization():
    document = new_document()
    with pytest.raises(FeatureRequiresUserNaming) as exc:
        apply(document, instruction(target=Target.feature_field("order-export", "one_liner")))
    assert exc.value.invariant == DInvariant.D3
    assert document.features() == ()


def test_D5_ok_add_to_untouched_single_value_field():
    document = new_document()
    target = Target.feature_field("order-export", "one_liner")
    apply(
        document,
        instruction(target=target, entry_id="en1", text="第一版"),
        authorized=("order-export",),
    )
    apply(
        document,
        instruction(target=target, entry_id="en2", text="第二版"),
        authorized=("order-export",),
    )
    block = document.field_block(FeatureSlug("order-export"), "one_liner")
    assert block is not None
    assert [entry.text for entry in block.entries] == ["第二版"]


def test_D5_bad_replace_manually_edited_block():
    document = new_document()
    apply(document, instruction(target=Target.body("s1"), entry_id="en1", text="用户写的内容"))
    blocks = tuple(
        replace(block, manually_edited=True) if block.block_id == BlockId("s1.b1") else block
        for block in document.blocks
    )
    manually_edited = Document(
        document_id=document.document_id,
        project_id=document.project_id,
        title=document.title,
        blocks=blocks,
        versions=document.versions,
        created_at=NOW,
    )
    with pytest.raises(BlockManuallyEdited) as exc:
        apply(
            manually_edited,
            instruction(
                target=Target.body("s1"),
                entry_id="en1",
                text="AI 想覆盖",
                op=WriteOp.REPLACE,
            ),
        )
    assert exc.value.invariant == DInvariant.D5
    assert manually_edited.block(BlockId("s1.b1")).entries[0].text == "用户写的内容"


def test_D6_ok_apply_creates_one_version():
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s1"), entry_id="en1", text="现状"),
        instruction(target=Target.body("s2"), entry_id="en2", text="目标"),
    )
    assert [version.version_no for version in document.versions] == [1, 2]
    assert document.version(1).snapshot.blocks != document.blocks
    assert len(document.current_version.changed_block_ids) == 2


def test_D6_ok_rollback_creates_new_version():
    document = new_document()
    apply(document, instruction(target=Target.body("s1"), text="现状"))
    version = document.rollback_to(1, now=NOW, summary="回退到 v1")
    assert version.version_no == 3
    assert version.trigger is VersionTrigger.ROLLBACK
    assert document.view().sections[0].blocks == ()
    assert document.versions[-1] is version


def test_D6_bad_empty_apply():
    document = new_document()
    with pytest.raises(EmptyApply) as exc:
        apply(document)
    assert exc.value.invariant == DInvariant.D6


def test_D6_bad_versions_are_immutable():
    document = new_document()
    with pytest.raises(AttributeError):
        document.versions.append(object())  # type: ignore[attr-defined]
    with pytest.raises(FrozenInstanceError):
        document.current_version.summary = "改历史"  # type: ignore[misc]


def test_view_builds_tree_and_preference_area():
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s5"), entry_id="en1", text="本期不做定时导出"),
        instruction(target=Target.preference(), entry_id="en2", text="登录页太丑"),
        instruction(
            target=Target.feature_field("order-export", "acceptance"),
            entry_id="en3",
            text="5 万条内 60 秒",
        ),
        authorized=("order-export",),
    )
    view = document.view()
    s5 = next(section for section in view.sections if section.section_id == "s5")
    assert [block.kind for block in s5.blocks] == [BlockKind.TEXT, BlockKind.FEATURE]
    feature = s5.blocks[1]
    assert [child.kind for child in feature.children] == [BlockKind.ACCEPTANCE]
    assert view.preference_entries[0].text == "登录页太丑"


def test_gaps_lists_missing_required_fields():
    document = new_document()
    apply(
        document,
        instruction(
            target=Target.feature_field("order-export", "name"),
            entry_id="en1",
            text="订单导出",
        ),
        authorized=("order-export",),
    )
    gaps = document.gaps()
    assert [gap.field for gap in gaps] == [
        "one_liner",
        "goal",
        "audience",
        "exceptions",
        "acceptance",
    ]


def test_apply_refuses_a_candidate_state_that_would_not_load():
    """写之前先验一次：不合格的候选状态不许落盘。

    这条是"写得进去、读不出来"逼出来的——校验原本只在 Document 构造时跑，
    于是错误状态能存进库，要等下一个人读它才炸（手改文档踩过一次）。
    """

    document = new_document()
    with pytest.raises(SectionSetViolation):
        # 把章节根块当成普通内容块的 kind 抄下来 → 同一章出现两个 section 根
        apply(
            document,
            instruction(
                target=Target.body("s2"),
                entry_id="en1",
                text="一段话",
                kind=BlockKind.SECTION,
            ),
        )

    assert document.current_version.version_no == 1  # 一个字节没变
