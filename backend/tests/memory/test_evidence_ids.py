"""TDD-1：M13 组装上下文时给证据编号（引用归因的前提）。

设计（docs/redesign 05-evidence）：
- 每个可引用块在组装时获得连续唯一的 `evidence_id`（E1, E2, ...），按产出顺序编号；
- 文档块额外带 `ref_version`（BLOCK 引用必须带 target_version，C15）；
- 编号在裁剪后跟随块走（evidence_id 是块的属性，不可变对象天然跟随）。
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.contracts.enums import ContextBlockSource, RoundEntry, RoundPhase
from pmstudio.contracts.models.document import Block, Document
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.memory.context_assembler import ContextAssembler
from pmstudio.storage.db import Database
from pmstudio.storage.ledger import Ledger

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
TEMPLATE = "reg_tpl_initial"
FIELDS = ("项目是什么", "目标", "功能清单")


@pytest.fixture
def db(tmp_path: Path):
    database = Database.open(tmp_path / "pmstudio.sqlite3")
    yield database
    database.close()


def _seed(db: Database) -> None:
    ledger = Ledger(db, TimestampIdGenerator())
    ledger.create_project(
        Project(
            project_id="prj_1",
            name="PM Studio",
            template_id=TEMPLATE,
            config=ProjectConfig(output_reserve_tokens=4096, system_overhead_tokens=512),
            created_at=AT,
        )
    )
    ledger.create_document(
        Document(doc_id="doc_1", project_id="prj_1", template_id=TEMPLATE),
        [Block(block_id=f"blk_{index}", schema_label=label) for index, label in enumerate(FIELDS)],
    )


def _round() -> RoundRegion:
    return RoundRegion(
        round_id="rnd_1",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input="细化一下功能清单",
        scope=Scope(selected_fields=("功能清单",)),
        phase=RoundPhase.ASSEMBLING,
    )


def _assemble(db: Database):
    return asyncio.run(ContextAssembler(Ledger(db)).assemble_context(_round()))


def test_every_block_gets_a_continuous_unique_evidence_id(db: Database) -> None:
    """每个块都有 evidence_id，且按产出顺序连续唯一（E1..En）。"""
    _seed(db)

    result = _assemble(db)

    ids = [block.evidence_id for block in result.blocks]
    assert len(ids) == 4  # 3 个文档块 + 1 条用户输入
    assert all(block.evidence_id is not None for block in result.blocks)
    assert len(set(ids)) == len(ids)  # 唯一
    assert ids == ["E1", "E2", "E3", "E4"]  # 连续


def test_document_blocks_carry_ref_version(db: Database) -> None:
    """文档块带 ref_version——C15 的 BLOCK 引用必须带 target_version（I6 失效判定靠它）。"""
    _seed(db)

    result = _assemble(db)

    documents = [b for b in result.blocks if b.source is ContextBlockSource.DOCUMENT]
    assert all(b.ref_version == 1 for b in documents)  # 初始版本都是 1
    assert all(b.evidence_id for b in documents)


def test_user_input_block_has_no_ref_version(db: Database) -> None:
    """用户输入块不是 BLOCK 引用——ref_version 必须为空（C15 只有 block 有版本）。"""
    _seed(db)

    result = _assemble(db)

    inputs = [b for b in result.blocks if b.source is ContextBlockSource.USER_INPUT]
    assert len(inputs) == 1
    assert inputs[0].evidence_id == "E4"
    assert inputs[0].ref_version is None


def test_ref_version_tracks_block_after_write(db: Database) -> None:
    """块被写过 → 下一轮组装的 ref_version 是当前版本（引用的是最新版本，I6 对齐）。"""
    _seed(db)
    from pmstudio.contracts.enums import BlockOpKind, VersionTrigger
    from pmstudio.contracts.models.document import BlockOp

    Ledger(db).write_blocks(
        "doc_1",
        [BlockOp(block_id="blk_1", op=BlockOpKind.REPLACE, content="写过了", expected_version=1)],
        VersionTrigger.MANUAL,
    )

    result = _assemble(db)

    documents = {b.ref: b for b in result.blocks if b.source is ContextBlockSource.DOCUMENT}
    assert documents["blk_1"].ref_version == 2
    assert documents["blk_0"].ref_version == 1
