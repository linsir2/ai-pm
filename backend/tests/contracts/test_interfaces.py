"""层间接口：方法集、async、弱类型、读写句柄分离。"""

import inspect
from typing import Any, get_type_hints

import pytest

from pmstudio.contracts.interfaces.communication import Board, BoardWriter, EventBus
from pmstudio.contracts.interfaces.harness import HarnessPort
from pmstudio.contracts.interfaces.memory import MemoryPort
from pmstudio.contracts.interfaces.orchestration import OrchestrationPort
from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.interfaces.tools import AppServicesPort, CapabilityToolsPort

EXPECTED_METHODS: dict[type, set[str]] = {
    Board: {"read"},
    BoardWriter: {"open_round", "write", "drop_round"},
    EventBus: {"publish", "subscribe"},
    OrchestrationPort: {
        "start_round",
        "submit_cards",
        "run_discussion",
        "summarize_discussion",
        "select_ideas",
        "stop_round",
    },
    MemoryPort: {
        "assemble_context",
        "retrieve",
        "read_brief",
        "update_brief",
        "append_message",
        "history_context",
        "record_candidates",
        "invalidate",
    },
    CapabilityToolsPort: {"search_web", "ingest"},
    AppServicesPort: {"create_project", "write_document"},
    HarnessPort: {"complete", "can_use", "can_enter", "trim", "record_trace"},
    RegistryPort: {"resolve", "list", "register", "update_status", "remove"},
}


def _public_methods(protocol: type) -> set[str]:
    return {name for name, value in vars(protocol).items() if not name.startswith("_") and callable(value)}


@pytest.mark.parametrize(
    ("protocol", "expected"),
    list(EXPECTED_METHODS.items()),
    ids=[protocol.__name__ for protocol in EXPECTED_METHODS],
)
def test_method_set_matches_contract(protocol: type, expected: set[str]) -> None:
    assert _public_methods(protocol) == expected


@pytest.mark.parametrize("protocol", list(EXPECTED_METHODS), ids=lambda p: p.__name__)
def test_every_port_operation_is_async(protocol: type) -> None:
    """P3：IO 端口全 async，纯计算同步。"""
    for name in _public_methods(protocol):
        assert inspect.iscoroutinefunction(getattr(protocol, name)), name


@pytest.mark.parametrize("protocol", list(EXPECTED_METHODS), ids=lambda p: p.__name__)
def test_no_unapproved_weak_types(protocol: type) -> None:
    """入参和返回都得说得出形状——`Any` 会让这道接缝静默失效。"""
    for name in _public_methods(protocol):
        hints = get_type_hints(getattr(protocol, name))
        for annotation in hints.values():
            assert annotation is not Any
            assert annotation is not object
            assert "Any" not in str(annotation)


def test_board_reads_and_writes_are_separate_handles() -> None:
    """I19：写句柄只发给编排层，类型上就不给它 read 混在一起的机会。"""
    assert not hasattr(Board, "write")
    assert not hasattr(BoardWriter, "read")


def test_publish_requires_a_producer_identity() -> None:
    """窄发布：谁发的事件要能说清，白名单才有执行点。"""
    parameter = inspect.signature(EventBus.publish).parameters["producer"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is inspect.Parameter.empty


def test_capability_tools_and_app_services_are_separate() -> None:
    """能力工具进注册中心、受权限管；应用函数不进（directory.md §3.1）。"""
    assert {"search_web", "ingest"} == _public_methods(CapabilityToolsPort)
    assert {"create_project", "write_document"} == _public_methods(AppServicesPort)
    assert "export" not in _public_methods(AppServicesPort)


def test_trim_does_not_take_a_budget() -> None:
    """预算由 L6 自己算，调用方不传。"""
    names = set(inspect.signature(HarnessPort.trim).parameters)
    assert names == {"self", "blocks", "project_id", "model_ref"}


class _BoardDouble:
    async def read(self, region: object) -> object:  # pragma: no cover - 只做形状替身
        raise NotImplementedError


class _MemoryDouble:
    async def assemble_context(self, round_region: object) -> object:  # pragma: no cover
        raise NotImplementedError

    async def retrieve(self, project_id: str, query: str) -> tuple[()]:  # pragma: no cover
        raise NotImplementedError

    async def read_brief(self, project_id: str) -> str:  # pragma: no cover
        raise NotImplementedError

    async def update_brief(self, project_id: str, lines: object) -> str:  # pragma: no cover
        raise NotImplementedError

    async def append_message(self, message: object) -> None:  # pragma: no cover
        raise NotImplementedError

    async def history_context(self, project_id: str, round_id: str) -> tuple[()]:  # pragma: no cover
        raise NotImplementedError

    async def record_candidates(self, memories: object) -> None:  # pragma: no cover
        raise NotImplementedError

    async def invalidate(self, citations: object) -> None:  # pragma: no cover
        raise NotImplementedError


def test_ports_are_runtime_checkable_for_test_doubles() -> None:
    """各层可以拿替身单独开发：只要形状对，就是这一层的实现。"""
    assert isinstance(_BoardDouble(), Board)
    assert isinstance(_MemoryDouble(), MemoryPort)
    assert not isinstance(_BoardDouble(), BoardWriter)
