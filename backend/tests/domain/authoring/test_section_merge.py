"""F8 残留：章节级追加每次都新开一段 → 改为并入同章节同类型已有块。

规则：
1. 同章节、同 kind、无 feature、无手改 → append 到最后一个已有块
2. 不同类型各开一块（text 并 text，steps 并 steps）
3. 手改过的块不 append，新建
4. 无现有块可并 → 新建（老行为）
5. 章节槽（有 field 的）不受影响
"""

from dataclasses import replace

from pmstudio.domain.authoring.blocks import BlockKind
from pmstudio.domain.authoring.document import Document
from pmstudio.domain.shared.ids import BlockId, EvidenceId
from pmstudio.domain.shared.target import Target
from tests.domain.authoring.test_document import NOW, apply, instruction, new_document

REF = EvidenceId("E1")


def test_section_append_merges_into_one_block():
    """连续两次章节级追加 → 并入同一块。"""
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s1"), entry_id="en1", text="用户需求很重要"),
    )
    apply(
        document,
        instruction(target=Target.body("s1"), entry_id="en2", text="尤其是新手"),
    )

    block = document.block(BlockId("s1.b1"))
    assert [e.text for e in block.entries] == ["用户需求很重要", "尤其是新手"]
    assert not _has_block(document, "s1.b2")


def test_different_kind_separate_blocks():
    """不同类型各开一块。"""
    document = new_document()
    apply(
        document,
        instruction(
            target=Target.body("s6"), entry_id="en1", text="步骤一", kind=BlockKind.STEPS
        ),
    )
    apply(
        document,
        instruction(
            target=Target.body("s6"), entry_id="en2", text="步骤二", kind=BlockKind.STEPS
        ),
    )
    apply(
        document,
        instruction(
            target=Target.body("s6"), entry_id="en3", text="叙述正文", kind=BlockKind.TEXT
        ),
    )

    assert document.block(BlockId("s6.b1")).kind == BlockKind.STEPS
    assert len(document.block(BlockId("s6.b1")).entries) == 2
    assert document.block(BlockId("s6.b2")).kind == BlockKind.TEXT


def test_manually_edited_block_not_appended():
    """手改块不 append，新建。"""
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s1"), entry_id="en1", text="AI 写的"),
    )
    # 模拟手改：重建文档，标记 s1.b1 为 manually_edited
    blocks = tuple(
        replace(block, manually_edited=True) if block.block_id == BlockId("s1.b1") else block
        for block in document.blocks
    )
    document = Document(
        document_id=document.document_id,
        project_id=document.project_id,
        title=document.title,
        blocks=blocks,
        versions=document.versions,
        created_at=NOW,
    )

    apply(
        document,
        instruction(target=Target.body("s1"), entry_id="en2", text="AI 追加"),
    )

    assert len(document.block(BlockId("s1.b1")).entries) == 1  # 不变
    assert document.block(BlockId("s1.b2")).entries[-1].text == "AI 追加"  # 新建


def test_no_existing_block_creates_new():
    """无现有块可并 → 新建（老行为）。"""
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s3"), entry_id="en1", text="第一条"),
    )

    assert _has_block(document, "s3.b1")
    assert document.block(BlockId("s3.b1")).entries[-1].text == "第一条"


def test_section_slot_unaffected():
    """章节槽（有 field 的）不受影响。"""
    document = new_document()
    apply(
        document,
        instruction(
            target=Target.body("s8", field="performance"), entry_id="en1", text="性能目标"
        ),
    )
    apply(
        document,
        instruction(
            target=Target.body("s8", field="performance"),
            entry_id="en2",
            text="补充说明",
        ),
    )

    assert [e.text for e in document.block(BlockId("s8.performance")).entries] == [
        "性能目标",
        "补充说明",
    ]


def test_different_sections_independent():
    """多章节互不干扰。"""
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s2"), entry_id="en1", text="s2 内容"),
    )
    apply(
        document,
        instruction(target=Target.body("s3"), entry_id="en2", text="s3 内容"),
    )
    apply(
        document,
        instruction(target=Target.body("s2"), entry_id="en3", text="s2 追加"),
    )

    assert len(document.block(BlockId("s2.b1")).entries) == 2
    assert len(document.block(BlockId("s3.b1")).entries) == 1


def test_snapshot_after_merge():
    """多轮追加后版本追踪正常。"""
    document = new_document()
    apply(
        document,
        instruction(target=Target.body("s2"), entry_id="en1", text="A"),
    )
    v2 = apply(
        document,
        instruction(target=Target.body("s2"), entry_id="en2", text="B"),
    )

    assert v2.changed_block_ids == (BlockId("s2.b1"),)
    assert [e.text for e in document.block(BlockId("s2.b1")).entries] == ["A", "B"]


def _has_block(document: Document, block_id: str) -> bool:
    try:
        document.block(BlockId(block_id))
        return True
    except Exception:
        return False
