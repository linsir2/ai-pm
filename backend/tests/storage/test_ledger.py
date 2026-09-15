"""M0.1 存储底座的验收用例。

对应 directory.md §9.1 M0.1 的退出条件：
能建项目、读文档、写块；版本冲突被拒；回退产生新版本；重启后数据还在。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import VersionConflict
from pmstudio.contracts.enums import BlockOpKind, VersionTrigger
from pmstudio.contracts.models.document import Block, BlockOp, Document
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.storage.db import Database
from pmstudio.storage.ledger import Ledger

CREATED_AT = datetime(2026, 9, 14, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path):
    database = Database.open(tmp_path / "pmstudio.sqlite3")
    yield database
    database.close()


@pytest.fixture
def ledger(db: Database) -> Ledger:
    return Ledger(db)


def _project(project_id: str = "prj_1") -> Project:
    return Project(
        project_id=project_id,
        name="PM Studio",
        template_id="reg_tpl_initial",
        config=ProjectConfig(output_reserve_tokens=4096, system_overhead_tokens=512),
        created_at=CREATED_AT,
    )


def _block(block_id: str, label: str, content: str = "", version: int = 1) -> Block:
    return Block(
        block_id=block_id,
        parent_id=None,
        schema_label=label,
        content=content,
        version=version,
        source_card_id=None,
    )


def _seed(ledger: Ledger, project_id: str = "prj_1", doc_id: str = "doc_1") -> None:
    ledger.create_project(_project(project_id))
    ledger.create_document(
        Document(doc_id=doc_id, project_id=project_id, template_id="reg_tpl_initial"),
        [_block("blk_goal", "目标", ""), _block("blk_features", "功能清单", "")],
    )


def test_create_project_and_read_back(ledger: Ledger) -> None:
    _seed(ledger)
    project = ledger.read_project("prj_1")
    assert project is not None
    assert project.name == "PM Studio"
    assert project.template_id == "reg_tpl_initial"
    assert ledger.read_project("prj_missing") is None


def test_create_document_with_blocks(ledger: Ledger) -> None:
    _seed(ledger)
    document = ledger.read_document("doc_1")
    assert document is not None
    assert document.project_id == "prj_1"
    labels = [b.schema_label for b in ledger.read_blocks("doc_1")]
    assert labels == ["目标", "功能清单"]


def test_write_blocks_bumps_version_and_records_source_card(ledger: Ledger) -> None:
    _seed(ledger)
    version = ledger.write_blocks(
        "doc_1",
        ops=[
            BlockOp(
                block_id="blk_goal",
                kind=BlockOpKind.REPLACE,
                content="给产品经理和工程师做需求对齐",
                expected_version=1,
            )
        ],
        trigger=VersionTrigger.SUBMIT,
        group_id="grp_1",
    )
    blocks = {b.block_id: b for b in ledger.read_blocks("doc_1")}
    assert blocks["blk_goal"].content == "给产品经理和工程师做需求对齐"
    assert blocks["blk_goal"].version == 2
    assert version.trigger is VersionTrigger.SUBMIT
    assert version.group_id == "grp_1"
    assert version.seq == 1


def test_append_keeps_existing_content(ledger: Ledger) -> None:
    _seed(ledger)
    ledger.write_blocks(
        "doc_1",
        ops=[
            BlockOp(
                block_id="blk_features",
                kind=BlockOpKind.APPEND,
                content="第 1 条：支持把讨论候选带进主闭环",
                expected_version=1,
            )
        ],
        trigger=VersionTrigger.SUBMIT,
        group_id="grp_1",
    )
    blocks = {b.block_id: b for b in ledger.read_blocks("doc_1")}
    assert "第 1 条" in blocks["blk_features"].content


def test_stale_expected_version_is_rejected(ledger: Ledger) -> None:
    _seed(ledger)
    with pytest.raises(VersionConflict):
        ledger.write_blocks(
            "doc_1",
            ops=[
                BlockOp(
                    block_id="blk_goal",
                    kind=BlockOpKind.REPLACE,
                    content="x",
                    expected_version=99,
                )
            ],
            trigger=VersionTrigger.MANUAL,
        )


def test_write_is_atomic_all_or_nothing(ledger: Ledger) -> None:
    """一条 op 的版本对不上，整批都不能生效（I9）。"""
    _seed(ledger)
    with pytest.raises(VersionConflict):
        ledger.write_blocks(
            "doc_1",
            ops=[
                BlockOp(
                    block_id="blk_goal",
                    kind=BlockOpKind.REPLACE,
                    content="这条不该被写进去",
                    expected_version=1,
                ),
                BlockOp(
                    block_id="blk_features",
                    kind=BlockOpKind.REPLACE,
                    content="这条版本对不上",
                    expected_version=99,
                ),
            ],
            trigger=VersionTrigger.MANUAL,
        )
    blocks = {b.block_id: b for b in ledger.read_blocks("doc_1")}
    assert blocks["blk_goal"].content == ""
    assert blocks["blk_goal"].version == 1
    assert ledger.list_versions("doc_1") == []


def test_version_seq_is_monotonic(ledger: Ledger) -> None:
    _seed(ledger)
    for expected in (1, 2, 3):
        ledger.write_blocks(
            "doc_1",
            ops=[
                BlockOp(
                    block_id="blk_goal",
                    kind=BlockOpKind.REPLACE,
                    content=f"v{expected}",
                    expected_version=expected,
                )
            ],
            trigger=VersionTrigger.MANUAL,
        )
    assert [v.seq for v in ledger.list_versions("doc_1")] == [1, 2, 3]


def test_manual_edit_has_no_source_card_id(ledger: Ledger) -> None:
    """手写的块没有 source_card_id（I3）。"""
    _seed(ledger)
    ledger.write_blocks(
        "doc_1",
        ops=[
            BlockOp(
                block_id="blk_goal",
                kind=BlockOpKind.REPLACE,
                content="手改的",
                expected_version=1,
            )
        ],
        trigger=VersionTrigger.MANUAL,
    )
    blocks = {b.block_id: b for b in ledger.read_blocks("doc_1")}
    assert blocks["blk_goal"].is_ai_written is False


def test_rollback_creates_new_version_and_keeps_history(ledger: Ledger) -> None:
    """回退产生新版本，不删不改历史（I8 / AC8）。"""
    _seed(ledger)
    first = ledger.write_blocks(
        "doc_1",
        ops=[
            BlockOp(
                block_id="blk_goal",
                kind=BlockOpKind.REPLACE,
                content="第一版",
                expected_version=1,
            )
        ],
        trigger=VersionTrigger.MANUAL,
    )
    ledger.write_blocks(
        "doc_1",
        ops=[
            BlockOp(
                block_id="blk_goal",
                kind=BlockOpKind.REPLACE,
                content="第二版",
                expected_version=2,
            )
        ],
        trigger=VersionTrigger.MANUAL,
    )

    rolled = ledger.rollback("doc_1", target_version_id=first.version_id)

    assert rolled.trigger is VersionTrigger.ROLLBACK
    assert rolled.target_version_id == first.version_id
    assert [v.seq for v in ledger.list_versions("doc_1")] == [1, 2, 3]
    blocks = {b.block_id: b for b in ledger.read_blocks("doc_1")}
    assert blocks["blk_goal"].content == "第一版"
    assert blocks["blk_goal"].version > 2


def test_persistence_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    first = Database.open(path)
    Ledger(first).create_project(_project())
    first.close()

    second = Database.open(path)
    try:
        assert Ledger(second).read_project("prj_1") is not None
    finally:
        second.close()


def test_schema_version_is_recorded(db: Database) -> None:
    assert db.schema_version() >= 1
