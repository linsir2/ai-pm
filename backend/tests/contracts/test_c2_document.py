"""C2 Block / Document：文档树与写操作的入参。"""

import pytest
from pydantic import ValidationError

from pmstudio.contracts.enums import BlockOpKind
from pmstudio.contracts.models.document import Block, BlockOp, Document, DocumentSnapshot


def test_block_defaults() -> None:
    block = Block(block_id="blk_1", schema_label="目标")
    assert block.parent_id is None
    assert block.content == ""
    assert block.version == 1
    assert block.source_card_id is None
    assert block.is_ai_written is False


def test_block_rejects_blank_ids_and_labels() -> None:
    with pytest.raises(ValidationError):
        Block(block_id="", schema_label="目标")
    with pytest.raises(ValidationError):
        Block(block_id="blk_1", schema_label="")


def test_block_cannot_be_its_own_parent() -> None:
    with pytest.raises(ValidationError):
        Block(block_id="blk_1", parent_id="blk_1", schema_label="目标")


def test_source_card_id_marks_ai_written_block() -> None:
    block = Block(block_id="blk_1", schema_label="目标", source_card_id="crd_1")
    assert block.is_ai_written is True


def test_document_requires_all_three_ids() -> None:
    with pytest.raises(ValidationError):
        Document(doc_id="", project_id="prj_1", template_id="reg_tpl_initial")
    with pytest.raises(ValidationError):
        Document(doc_id="doc_1", project_id="", template_id="reg_tpl_initial")
    with pytest.raises(ValidationError):
        Document(doc_id="doc_1", project_id="prj_1", template_id="")


def test_snapshot_rejects_duplicate_block_ids() -> None:
    with pytest.raises(ValidationError):
        DocumentSnapshot(
            blocks=(
                Block(block_id="blk_1", schema_label="目标"),
                Block(block_id="blk_1", schema_label="功能清单"),
            )
        )


def test_snapshot_rejects_duplicate_top_level_labels() -> None:
    """M20 靠 `schema_label` 定位块（I22）：两个同名的顶层块，让"写到哪一块"没有唯一答案。"""
    with pytest.raises(ValidationError):
        DocumentSnapshot(
            blocks=(
                Block(block_id="blk_1", schema_label="功能清单"),
                Block(block_id="blk_2", schema_label="功能清单"),
            )
        )


def test_nested_duplicate_labels_are_allowed() -> None:
    """定的是**顶层**唯一：定位只发生在顶层，嵌套块的重名等 R2 引入加块时再谈。"""
    snapshot = DocumentSnapshot(
        blocks=(
            Block(block_id="blk_features", schema_label="功能清单"),
            Block(block_id="blk_sub", parent_id="blk_features", schema_label="功能清单"),
        )
    )
    assert [block.block_id for block in snapshot.blocks] == ["blk_features", "blk_sub"]


def test_block_op_uses_the_contract_field_name() -> None:
    """§5.3 payload 项写的是 `op`，不是 `kind`。"""
    op = BlockOp(block_id="blk_1", op=BlockOpKind.APPEND, content="加一条", expected_version=2)
    assert op.op is BlockOpKind.APPEND


def test_block_op_rejects_the_old_field_name() -> None:
    with pytest.raises(ValidationError):
        BlockOp(block_id="blk_1", kind=BlockOpKind.APPEND, content="x", expected_version=1)


def test_block_op_expected_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        BlockOp(block_id="blk_1", op=BlockOpKind.REPLACE, content="x", expected_version=0)
