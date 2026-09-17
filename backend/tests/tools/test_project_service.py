"""建项目（应用函数，不是能力工具）：按模板实例化 9 个字段空骨架，一次事务完成。"""

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.contracts.interfaces.tools import AppServicesPort
from pmstudio.contracts.models.registry import TemplateBody
from pmstudio.contracts.skeleton.events import Event
from pmstudio.registry.entries import InMemoryRegistry
from pmstudio.registry.seeds import load_entries, seed
from pmstudio.storage.db import Database
from pmstudio.storage.ledger import Ledger
from pmstudio.tools.services.project_service import (
    DEFAULT_OUTPUT_RESERVE_TOKENS,
    DEFAULT_SYSTEM_OVERHEAD_TOKENS,
    ProjectService,
)

SEEDS = Path(__file__).resolve().parents[2] / "seeds"
AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return AT


class _Bus:
    """灌种子不发事件（只有 `update_status` 发）——形状对就行。"""

    async def publish(self, event: Event, *, producer: object) -> None:
        raise AssertionError("建项目不该发事件")


class CountingIds:
    """真 id 生成器 + 在第 N 次调用上炸一次，用来证明"写到一半失败，一行都不留"。"""

    def __init__(self, fail_at: int | None = None) -> None:
        self._fail_at = fail_at
        self._calls = 0
        self._inner = TimestampIdGenerator()

    def new_id(self, prefix: str) -> str:
        self._calls += 1
        if self._fail_at is not None and self._calls == self._fail_at:
            raise RuntimeError("注入的失败：写到一半炸了")
        return self._inner.new_id(prefix)


@pytest.fixture
def db(tmp_path: Path):
    database = Database.open(tmp_path / "pmstudio.sqlite3")
    yield database
    database.close()


def _template_id() -> str:
    """模板 id 从种子文件读——不在代码里再写一遍（第二个出处就是第二个真相）。"""
    return load_entries(SEEDS)[0].id


def _service(db: Database, ids: CountingIds | None = None) -> ProjectService:
    registry = InMemoryRegistry(_Bus(), FixedClock())  # type: ignore[arg-type]
    asyncio.run(seed(registry, SEEDS))
    generator = ids or CountingIds()
    return ProjectService(
        registry=registry,
        ledger=Ledger(db, generator),
        db=db,
        ids=generator,
        clock=FixedClock(),
    )


def test_create_project_signature_matches_the_port() -> None:
    """签名对齐 §5.3 的 `createProject`。R1.1 只实现这一个方法，不假装自己是整个 `AppServicesPort`
    （另一半 `writeDocument` 是 M20，R1.4）。"""
    assert list(inspect.signature(AppServicesPort.create_project).parameters) == list(
        inspect.signature(ProjectService.create_project).parameters
    )


def test_create_project_writes_project_document_and_nine_blocks(db: Database) -> None:
    result = asyncio.run(_service(db).create_project(_template_id(), "PM Studio"))

    ledger = Ledger(db)
    project = ledger.read_project(result.project_id)
    document = ledger.read_document(result.doc_id)
    blocks = ledger.read_blocks(result.doc_id)
    assert project is not None and project.name == "PM Studio"
    assert project.template_id == _template_id()
    assert document is not None and document.project_id == result.project_id
    assert len(blocks) == 9
    assert all(block.version == 1 for block in blocks)
    assert all(block.source_card_id is None for block in blocks)
    assert all(block.content == "" for block in blocks)


def test_blocks_follow_the_template_order(db: Database) -> None:
    """`read_blocks` 按 `position` 返回——所以这条断言同时钉住顺序与落位。"""
    result = asyncio.run(_service(db).create_project(_template_id(), "PM Studio"))
    body = TemplateBody.model_validate(load_entries(SEEDS)[0].content)

    blocks = Ledger(db).read_blocks(result.doc_id)

    assert [block.schema_label for block in blocks] == [field.label for field in body.fields]


def test_create_project_produces_no_document_version(db: Database) -> None:
    """C3 的 `trigger` 只有 manual / submit / rollback——**建项目不是一版**。"""
    result = asyncio.run(_service(db).create_project(_template_id(), "PM Studio"))

    assert Ledger(db).list_versions(result.doc_id) == []


def test_project_gets_the_default_budget_until_the_model_table_exists(db: Database) -> None:
    """预算数字的正经出处是模型元数据表（directory.md §11 #6）。在那之前用默认值，记账。"""
    result = asyncio.run(_service(db).create_project(_template_id(), "PM Studio"))

    project = Ledger(db).read_project(result.project_id)
    assert project is not None
    assert project.config.output_reserve_tokens == DEFAULT_OUTPUT_RESERVE_TOKENS
    assert project.config.system_overhead_tokens == DEFAULT_SYSTEM_OVERHEAD_TOKENS


def test_create_project_is_one_transaction(db: Database) -> None:
    """第 11 次生成 id（最后一个块）时炸 → 项目、文档、块一行都不留。"""
    service = _service(db, CountingIds(fail_at=11))

    with pytest.raises(RuntimeError):
        asyncio.run(service.create_project(_template_id(), "PM Studio"))

    rows = db._connection.execute(  # noqa: SLF001 - 直接看表，证明"一行都没写"
        "SELECT (SELECT COUNT(*) FROM projects) AS p,"
        " (SELECT COUNT(*) FROM documents) AS d,"
        " (SELECT COUNT(*) FROM blocks) AS b"
    ).fetchone()
    assert (rows["p"], rows["d"], rows["b"]) == (0, 0, 0)


def test_unknown_template_is_refused(db: Database) -> None:
    with pytest.raises(ContractViolation):
        asyncio.run(_service(db).create_project("reg_tpl_nope", "PM Studio"))

    assert db._connection.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"] == 0  # noqa: SLF001


def test_project_survives_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    first = Database.open(path)
    try:
        result = asyncio.run(_service(first).create_project(_template_id(), "PM Studio"))
    finally:
        first.close()

    second = Database.open(path)
    try:
        ledger = Ledger(second)
        assert ledger.read_project(result.project_id) is not None
        assert len(ledger.read_blocks(result.doc_id)) == 9
    finally:
        second.close()
