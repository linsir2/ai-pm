"""事务可以嵌套，且嵌套的边界就是子事务的边界。

为什么需要这个能力：C12 把"写工作台与写账本能在同一个事务里"列为黑板持久化的三个理由之一，
而 `BoardStore` 与 `Ledger` 各自开事务——不能嵌套，这条要求就落不了地。

嵌套的语义是 SAVEPOINT：内层失败只回滚到自己的保存点，外层可以选择吞掉它继续提交；
最外层失败才是整批回滚。
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.contracts.models.document import Block, Document
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.storage.db import Database
from pmstudio.storage.ledger import Ledger

CREATED_AT = datetime(2026, 9, 16, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path):
    database = Database.open(tmp_path / "pmstudio.sqlite3")
    yield database
    database.close()


@pytest.fixture
def ledger(db: Database) -> Ledger:
    return Ledger(db)


def _project() -> Project:
    return Project(
        project_id="prj_1",
        name="PM Studio",
        template_id="reg_tpl_initial",
        config=ProjectConfig(output_reserve_tokens=4096, system_overhead_tokens=512),
        created_at=CREATED_AT,
    )


def _document() -> Document:
    return Document(doc_id="doc_1", project_id="prj_1", template_id="reg_tpl_initial")


def _blocks() -> list[Block]:
    return [
        Block(block_id="blk_goal", schema_label="目标"),
        Block(block_id="blk_features", schema_label="功能清单"),
    ]


def test_nested_transactions_commit_together(db: Database, ledger: Ledger) -> None:
    """外层开事务、内层调 Ledger（它也开事务）——一次提交，两样都在。"""
    with db.transaction():
        ledger.create_project(_project())
        ledger.create_document(_document(), _blocks())
        # 同一条连接里，未提交的写入自己看得见——这正是"先提交后广播"那条规矩的由来
        assert ledger.read_project("prj_1") is not None

    assert ledger.read_project("prj_1") is not None
    assert [block.block_id for block in ledger.read_blocks("doc_1")] == ["blk_goal", "blk_features"]


def test_inner_failure_rolls_back_the_whole_outer(db: Database, ledger: Ledger) -> None:
    """内层炸了且没人接住 → 整批回滚，不留半个项目。"""
    with pytest.raises(RuntimeError), db.transaction():
        ledger.create_project(_project())
        with db.transaction():
            raise RuntimeError("写到一半炸了")

    assert ledger.read_project("prj_1") is None


def test_outer_can_catch_inner_failure_and_still_commit(db: Database, ledger: Ledger) -> None:
    """内层失败被外层吞掉 → 只回滚到内层的保存点，外层其余的写入照常提交。"""
    with db.transaction():
        ledger.create_project(_project())
        with pytest.raises(RuntimeError), db.transaction():
            raise RuntimeError("这一步没成，但整轮还能继续")

    assert ledger.read_project("prj_1") is not None


def test_depth_resets_after_a_failed_transaction(db: Database, ledger: Ledger) -> None:
    """失败之后深度必须复位——否则下一次嵌套会把内层当成顶层。"""
    with pytest.raises(RuntimeError), db.transaction(), db.transaction():
        raise RuntimeError("内层炸")

    assert db._depth == 0

    with db.transaction():
        ledger.create_project(_project())
        with pytest.raises(RuntimeError), db.transaction():
            raise RuntimeError("再次内层炸")

    assert ledger.read_project("prj_1") is not None
