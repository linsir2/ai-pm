"""M13 上下文组装器：说"这一轮该带什么"，**一个 token 都不裁**（裁多少是 M24 的事）。"""

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.contracts.enums import (
    BlockOpKind,
    ContextBlockSource,
    RoundEntry,
    RoundPhase,
    VersionTrigger,
)
from pmstudio.contracts.interfaces.memory import MemoryPort
from pmstudio.contracts.models.document import Block, BlockOp, Document
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.memory.context_assembler import (
    PRIORITY_CURRENT_INPUT,
    PRIORITY_DOCUMENT,
    ContextAssembler,
)
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


def _seed(db: Database, *, with_document: bool = True, nested: bool = False) -> None:
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
    if not with_document:
        return
    blocks = [
        Block(block_id=f"blk_{index}", schema_label=label)
        for index, label in enumerate(FIELDS)
    ]
    if nested:
        blocks.append(Block(block_id="blk_nested", parent_id="blk_0", schema_label="子分区"))
    ledger.create_document(
        Document(doc_id="doc_1", project_id="prj_1", template_id=TEMPLATE), blocks
    )


def _round(user_input: str = "细化一下功能清单") -> RoundRegion:
    return RoundRegion(
        round_id="rnd_1",
        project_id="prj_1",
        entry=RoundEntry.MAIN,
        user_input=user_input,
        scope=Scope(selected_fields=("功能清单",)),
        phase=RoundPhase.ASSEMBLING,
    )


def _assemble(db: Database, user_input: str = "细化一下功能清单"):
    return asyncio.run(ContextAssembler(Ledger(db)).assemble_context(_round(user_input)))


def _documents(result) -> list:
    return [block for block in result.blocks if block.source is ContextBlockSource.DOCUMENT]


def test_signature_matches_the_port() -> None:
    """与 §5.3 的 `assembleContext` 对齐——入参就是整个 round 区块。"""
    assert list(inspect.signature(MemoryPort.assemble_context).parameters) == list(
        inspect.signature(ContextAssembler.assemble_context).parameters
    )


def test_the_document_is_read_in_full(db: Database) -> None:
    """读全文：每个顶层块一个上下文块，`ref` 指回 `block_id`。"""
    _seed(db)

    result = _assemble(db)

    documents = _documents(result)
    assert [block.ref for block in documents] == ["blk_0", "blk_1", "blk_2"]
    assert [block.content for block in documents] == ["", "", ""]
    assert {block.priority for block in documents} == {PRIORITY_DOCUMENT}
    # `credibility` 只有材料块有——C12 的校验器会拒掉别的来源带的 credibility
    assert all(block.credibility is None for block in documents)


def test_the_current_input_is_a_block_with_the_highest_priority(db: Database) -> None:
    _seed(db)

    result = _assemble(db, "把功能清单细化一下")

    inputs = [block for block in result.blocks if block.source is ContextBlockSource.USER_INPUT]
    assert [block.ref for block in inputs] == ["rnd_1"]
    assert inputs[0].content == "把功能清单细化一下"
    assert inputs[0].priority == PRIORITY_CURRENT_INPUT
    assert inputs[0].priority > PRIORITY_DOCUMENT


def test_base_versions_snapshot_covers_every_block(db: Database) -> None:
    """快照就是 I10 的眼睛：块被写过后，下一轮组装必须看到新版本。"""
    _seed(db)
    assembler = ContextAssembler(Ledger(db))

    before = asyncio.run(assembler.assemble_context(_round()))
    assert before.base_versions == {"blk_0": 1, "blk_1": 1, "blk_2": 1}

    Ledger(db).write_blocks(
        "doc_1",
        [
            BlockOp(
                block_id="blk_1",
                op=BlockOpKind.REPLACE,
                content="做成什么样算成功",
                expected_version=1,
            )
        ],
        VersionTrigger.MANUAL,
    )

    after = asyncio.run(assembler.assemble_context(_round()))
    assert after.base_versions == {"blk_0": 1, "blk_1": 2, "blk_2": 1}
    assert {block.ref: block.content for block in _documents(after)}["blk_1"] == "做成什么样算成功"


def test_empty_blocks_are_kept_so_the_snapshot_stays_complete(db: Database) -> None:
    """空块照样带上：漏了它，用户手改一个当前为空的块就查不出来（I10）。"""
    _seed(db)
    Ledger(db).write_blocks(
        "doc_1",
        [BlockOp(block_id="blk_0", op=BlockOpKind.REPLACE, content="先说清是什么", expected_version=1)],
        VersionTrigger.MANUAL,
    )

    result = _assemble(db)

    assert len(_documents(result)) == 3
    assert set(result.base_versions) == {"blk_0", "blk_1", "blk_2"}


def test_nested_blocks_are_not_sources(db: Database) -> None:
    """定位只发生在顶层（与 I22 同一口径）：子块不进上下文，也不进快照。"""
    _seed(db, nested=True)

    result = _assemble(db)

    assert "blk_nested" not in [block.ref for block in _documents(result)]
    assert "blk_nested" not in result.base_versions


def test_a_project_without_a_document_is_refused(db: Database) -> None:
    """装配没配好就该炸——静默给一个空上下文，复述只能靠猜（那正是 I14 要防的）。"""
    _seed(db, with_document=False)

    with pytest.raises(ContractViolation):
        _assemble(db)
