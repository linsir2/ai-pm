"""装配根：交出去的是接口，不是实现；启动顺序钉死。"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pmstudio.bootstrap.runtime import build_runtime, build_runtime_sync
from pmstudio.common.errors import SingleInstanceViolation
from pmstudio.contracts.enums import (
    EventType,
    ProducerIdentity,
    RegionName,
    RegistryKind,
    RegistryOwner,
    RoundEntry,
    RoundPhase,
)
from pmstudio.contracts.interfaces.communication import Board
from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.interfaces.storage import BoardStorePort, EventLogPort
from pmstudio.contracts.models.registry import TemplateBody
from pmstudio.contracts.models.results import CreateProjectResult
from pmstudio.contracts.models.scope import Scope
from pmstudio.contracts.skeleton.board import RoundRegion
from pmstudio.contracts.skeleton.events import Event, RoundUpdatedPayload

NOW = datetime(2026, 9, 15, 10, 0, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


class CounterIds:
    """可注入的标识生成器：测试里 id 不该是随机的。"""

    def __init__(self) -> None:
        self.count = 0

    def new_id(self, prefix: str) -> str:
        self.count += 1
        return f"{prefix}_test_{self.count}"


@pytest.fixture
def runtime(tmp_path: Path):
    built = build_runtime_sync(tmp_path / "pmstudio.sqlite3")
    yield built
    built.close()


def test_runtime_parts_satisfy_their_ports(runtime: object) -> None:
    assert isinstance(runtime.event_log, EventLogPort)
    assert isinstance(runtime.board, Board)
    assert isinstance(runtime.registry, RegistryPort)
    assert runtime.ledger is not None


def test_runtime_seeds_the_registry_at_startup(runtime: object) -> None:
    """R1.1：模板 schema 归注册中心（M27），种子每次启动灌进去（DECISIONS v0.5 / §14）。"""
    entry = asyncio.run(runtime.registry.resolve(RegistryKind.TEMPLATE, "reg_tpl_initial"))

    assert entry.owner is RegistryOwner.PRESET
    assert len(TemplateBody.model_validate(entry.content).fields) == 9


def test_runtime_hands_out_the_project_service(runtime: object) -> None:
    """建项目是应用函数：调用方拿服务对象，不自己拼 Ledger 与注册中心。"""
    result = asyncio.run(runtime.project_service.create_project("reg_tpl_initial", "PM Studio"))

    assert isinstance(result, CreateProjectResult)
    assert runtime.ledger.read_project(result.project_id) is not None
    assert len(runtime.ledger.read_blocks(result.doc_id)) == 9


def _created_round(runtime: object, project_id: str) -> RoundRegion:
    return RoundRegion(
        round_id="rnd_1",
        project_id=project_id,
        entry=RoundEntry.MAIN,
        user_input="细化一下功能清单",
        scope=Scope(selected_fields=("功能清单",)),
        phase=RoundPhase.ASSEMBLING,
    )


def test_runtime_hands_out_the_context_assembler(runtime: object) -> None:
    """M13：调用方拿它跑"这一轮该带什么"。"""
    created = asyncio.run(runtime.project_service.create_project("reg_tpl_initial", "PM Studio"))

    result = asyncio.run(
        runtime.context_assembler.assemble_context(_created_round(runtime, created.project_id))
    )

    assert len(result.blocks) == 10  # 9 个文档块 + 1 个本轮输入
    assert len(result.base_versions) == 9


def test_runtime_hands_out_the_trimmer(runtime: object) -> None:
    """M24：预算从模型条目与项目配置里算，调用方不传。"""
    created = asyncio.run(runtime.project_service.create_project("reg_tpl_initial", "PM Studio"))
    assembled = asyncio.run(
        runtime.context_assembler.assemble_context(_created_round(runtime, created.project_id))
    )

    trimmed = asyncio.run(
        runtime.trimmer.trim(assembled.blocks, created.project_id, "reg_model_default")
    )

    assert trimmed.dropped == ()  # 默认模型窗口足够大
    assert len(trimmed.blocks) == 10


def test_boards_store_is_wired_under_the_hood(runtime: object) -> None:
    assert isinstance(runtime.board._blackboard._store, BoardStorePort)


def test_runtime_exposes_no_write_handle(runtime: object) -> None:
    """I19：写句柄只给编排层，装配这一步不许把它散出去。"""
    for forbidden in ("write", "open_round", "drop_round"):
        assert not hasattr(runtime, forbidden)
        assert not hasattr(runtime.board, forbidden)


def test_the_wired_bus_really_logs_events(runtime: object) -> None:
    async def scenario() -> None:
        await runtime.event_bus.publish(
            Event(
                type=EventType.ROUND_UPDATED,
                payload=RoundUpdatedPayload(
                    round_id="rnd_1",
                    project_id="prj_1",
                    entry=RoundEntry.MAIN,
                    phase=RoundPhase.ASSEMBLING,
                ),
                at=NOW,
            ),
            producer=ProducerIdentity.ORCHESTRATION,
        )

    asyncio.run(scenario())
    assert [row.type for row in runtime.event_log.read_by_round("rnd_1")] == [EventType.ROUND_UPDATED]


def test_the_wired_board_starts_empty(runtime: object) -> None:
    assert asyncio.run(runtime.board.read(RegionName.ROUND)) is None


def test_clock_and_ids_can_be_injected(tmp_path: Path) -> None:
    ids = CounterIds()
    built = build_runtime_sync(tmp_path / "pmstudio.sqlite3", clock=FixedClock(), ids=ids)
    try:
        assert built.clock.now() == NOW
        assert built.ids is ids
    finally:
        built.close()


def test_close_is_idempotent_and_releases_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "pmstudio.sqlite3"
    built = asyncio.run(build_runtime(path))
    built.close()
    built.close()
    again = asyncio.run(build_runtime(path))
    again.close()


def test_second_runtime_on_the_same_database_is_refused(tmp_path: Path, runtime: object) -> None:
    with pytest.raises(SingleInstanceViolation):
        asyncio.run(build_runtime(tmp_path / "pmstudio.sqlite3"))
