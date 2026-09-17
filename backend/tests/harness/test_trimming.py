"""M24 预算与裁剪：只管"能带多少"，只按 `priority` 裁，不做语义判断。"""

import asyncio
import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.common.errors import ContractViolation
from pmstudio.common.ids import TimestampIdGenerator
from pmstudio.contracts.enums import (
    ContextBlockSource,
    Credibility,
    RegistryKind,
    RegistryOwner,
)
from pmstudio.contracts.interfaces.harness import HarnessPort
from pmstudio.contracts.models.project import Project, ProjectConfig
from pmstudio.contracts.models.registry import RegistryEntry
from pmstudio.contracts.skeleton.board import ContextBlock
from pmstudio.contracts.skeleton.events import Event
from pmstudio.harness.trimming import BudgetResolver, Trimmer, estimate_tokens
from pmstudio.registry.entries import InMemoryRegistry
from pmstudio.registry.seeds import seed
from pmstudio.storage.db import Database
from pmstudio.storage.ledger import Ledger

AT = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
SEEDS = Path(__file__).resolve().parents[2] / "seeds"
SMALL_MODEL = "reg_model_small"
SMALL_WINDOW = 100
RESERVE = 10
OVERHEAD = 10
# 小窗口模型的预算：100 − 10 − 10 = 80
SMALL_BUDGET = SMALL_WINDOW - RESERVE - OVERHEAD


class FixedClock:
    def now(self) -> datetime:
        return AT


class _Bus:
    async def publish(self, event: Event, *, producer: object) -> None:
        raise AssertionError("灌种子不该发事件")


@pytest.fixture
def env(tmp_path: Path):
    db = Database.open(tmp_path / "pmstudio.sqlite3")
    ledger = Ledger(db, TimestampIdGenerator())
    registry = InMemoryRegistry(_Bus(), FixedClock())  # type: ignore[arg-type]
    asyncio.run(seed(registry, SEEDS))
    asyncio.run(
        registry.register(
            RegistryEntry(
                id=SMALL_MODEL,
                kind=RegistryKind.MODEL,
                name="小窗口模型（测试用）",
                owner=RegistryOwner.PRESET,
                content={"context_window_tokens": SMALL_WINDOW},
            )
        )
    )
    ledger.create_project(
        Project(
            project_id="prj_1",
            name="PM Studio",
            template_id="reg_tpl_initial",
            config=ProjectConfig(
                output_reserve_tokens=RESERVE, system_overhead_tokens=OVERHEAD
            ),
            created_at=AT,
        )
    )
    yield db, ledger, registry
    db.close()


def _resolver(registry, ledger) -> BudgetResolver:
    return BudgetResolver(registry=registry, ledger=ledger)


def _blocks() -> tuple[ContextBlock, ...]:
    """按**优先级从低到高**给，故意和裁剪顺序相反，用来证明排序真的发生了。

    每个块的 token 数都是整数，便于手算：宽字符 1 个 = 1 token。
    """
    return (
        ContextBlock(
            source=ContextBlockSource.MATERIAL,
            ref="mat_1",
            content="丙" * 40,  # 40 tokens
            priority=40,
            credibility=Credibility.LOW,
        ),
        ContextBlock(
            source=ContextBlockSource.DOCUMENT,
            ref="blk_1",
            content="乙" * 30,  # 30 tokens
            priority=80,
        ),
        ContextBlock(
            source=ContextBlockSource.USER_INPUT,
            ref="rnd_1",
            content="甲" * 20,  # 20 tokens
            priority=100,
        ),
    )


def test_signature_matches_the_port() -> None:
    """§5.3 的 `trim(blocks, project_id, model_ref)`——**调用方不传预算**（L6 自己算）。"""
    assert list(inspect.signature(HarnessPort.trim).parameters) == list(
        inspect.signature(Trimmer.trim).parameters
    )


def test_estimate_tokens_is_documented_and_deterministic() -> None:
    """估算口径是显式的：宽字符 1 字 1 token，其余 4 字符 1 token（偏保守）。"""
    assert estimate_tokens("") == 0
    assert estimate_tokens("中文四个字") == 5
    assert estimate_tokens("x" * 4) == 1
    assert estimate_tokens("x" * 5) == 2
    assert estimate_tokens("中文") == estimate_tokens("中文")  # 可复现


def test_budget_is_window_minus_both_reserves(env) -> None:
    _, ledger, registry = env
    resolver = _resolver(registry, ledger)

    assert asyncio.run(resolver.budget_for("prj_1", SMALL_MODEL)) == SMALL_BUDGET
    # 默认模型是种子里的 65536：65536 − 10 − 10
    assert asyncio.run(resolver.budget_for("prj_1", "reg_model_default")) == 65536 - RESERVE - OVERHEAD


def test_everything_that_fits_is_kept_in_priority_order(env) -> None:
    _, ledger, registry = env
    trimmer = Trimmer(_resolver(registry, ledger))

    result = asyncio.run(trimmer.trim(_blocks(), "prj_1", "reg_model_default"))

    assert result.dropped == ()
    assert [block.ref for block in result.blocks] == ["rnd_1", "blk_1", "mat_1"]


def test_over_budget_drops_the_lowest_priority_first(env) -> None:
    _, ledger, registry = env
    trimmer = Trimmer(_resolver(registry, ledger))

    result = asyncio.run(trimmer.trim(_blocks(), "prj_1", SMALL_MODEL))

    assert [block.ref for block in result.blocks] == ["rnd_1", "blk_1"]  # 20 + 30 ≤ 80
    assert [block.ref for block in result.dropped] == ["mat_1"]  # 再加 40 就超了


def test_ties_are_broken_the_same_way_every_time(env) -> None:
    """同优先级按 `(source, ref)` —— 复用 `order_context_blocks`，不另立一套排序。"""
    _, ledger, registry = env
    trimmer = Trimmer(_resolver(registry, ledger))
    blocks = (
        ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_2", content="乙", priority=80),
        ContextBlock(source=ContextBlockSource.DOCUMENT, ref="blk_1", content="甲", priority=80),
    )

    first = asyncio.run(trimmer.trim(blocks, "prj_1", SMALL_MODEL))
    second = asyncio.run(trimmer.trim(blocks, "prj_1", SMALL_MODEL))

    assert [block.ref for block in first.blocks] == ["blk_1", "blk_2"]
    assert [block.ref for block in first.blocks] == [block.ref for block in second.blocks]


def test_the_first_block_survives_even_when_it_alone_exceeds_the_budget(env) -> None:
    """裁到空上下文比超一点预算更糟——复述只能靠猜（I14）。"""
    db, ledger, registry = env
    tiny = RegistryEntry(
        id="reg_model_tiny",
        kind=RegistryKind.MODEL,
        name="极小窗口模型（测试用）",
        owner=RegistryOwner.PRESET,
        content={"context_window_tokens": 25},  # 25 − 10 − 10 = 5
    )
    asyncio.run(registry.register(tiny))
    trimmer = Trimmer(_resolver(registry, ledger))

    result = asyncio.run(trimmer.trim(_blocks(), "prj_1", "reg_model_tiny"))

    assert [block.ref for block in result.blocks] == ["rnd_1"]  # 20 tokens > 5，仍然留下
    assert [block.ref for block in result.dropped] == ["blk_1", "mat_1"]


def test_unknown_model_or_project_is_refused(env) -> None:
    _, ledger, registry = env
    resolver = _resolver(registry, ledger)

    with pytest.raises(ContractViolation):
        asyncio.run(resolver.budget_for("prj_1", "reg_model_nope"))
    with pytest.raises(ContractViolation):
        asyncio.run(resolver.budget_for("prj_nope", SMALL_MODEL))


def test_trimming_does_not_rewrite_content(env) -> None:
    _, ledger, registry = env
    trimmer = Trimmer(_resolver(registry, ledger))
    blocks = _blocks()

    result = asyncio.run(trimmer.trim(blocks, "prj_1", SMALL_MODEL))

    kept = {block.ref: block for block in result.blocks}
    dropped = {block.ref: block for block in result.dropped}
    assert kept["rnd_1"] == next(block for block in blocks if block.ref == "rnd_1")
    assert dropped["mat_1"] == next(block for block in blocks if block.ref == "mat_1")
