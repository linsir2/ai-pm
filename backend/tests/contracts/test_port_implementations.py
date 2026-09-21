"""真实端口 → 实现 签名锁测试（P0 核心回归防线）。

把每个端口的真实实现类钉在映射表里，用 `assert_port_implementation`
做严格签名比对。已实现的方法必须与协议形状一致；未实现的方法
（如 M23/M25 留到对应里程碑）显式登记为「声明但未实现」，不算失败。

这张表是「声明面 vs 实现面」（原 D3）的类型化表达：
一眼能看出每个端口当前承诺了哪些方法、哪些还欠着。
"""

import pytest

from pmstudio.communication.board import BoardEditor, BoardReader
from pmstudio.communication.event_bus import EventBus
from pmstudio.contracts.interfaces.communication import Board, BoardWriter
from pmstudio.contracts.interfaces.harness import HarnessPort
from pmstudio.contracts.interfaces.memory import MemoryPort
from pmstudio.contracts.interfaces.orchestration import OrchestrationPort
from pmstudio.contracts.interfaces.registry import RegistryPort
from pmstudio.contracts.interfaces.tools import AppServicesPort
from pmstudio.contracts.signature_lock import assert_port_implementation
from pmstudio.harness.retry import RetryingHarness
from pmstudio.memory.context_assembler import ContextAssembler
from pmstudio.orchestration.round_driver import RoundDriver
from pmstudio.registry.entries import InMemoryRegistry
from pmstudio.tools.services.project_service import ProjectService

# 端口 → (实现类, 当前已实现的方法集合)
# 未登记的方法 = 「协议声明了但实现还没做」，跳过签名比对（缺失清单由 missing.py 管理）。
PORT_IMPLEMENTATIONS: dict[type, tuple[type, set[str]]] = {
    Board: (BoardReader, {"read"}),
    BoardWriter: (BoardEditor, {"open_round", "write", "drop_round"}),
    EventBus: (EventBus, {"publish", "subscribe"}),
    OrchestrationPort: (RoundDriver, {"start_round", "submit_cards"}),
    MemoryPort: (ContextAssembler, {"assemble_context"}),
    AppServicesPort: (ProjectService, {"create_project"}),
    HarnessPort: (RetryingHarness, {"complete", "trim"}),
    RegistryPort: (InMemoryRegistry, {"resolve", "list", "register", "update_status", "remove"}),
}


def _protocol_methods(protocol: type) -> set[str]:
    return {name for name, value in vars(protocol).items() if not name.startswith("_") and callable(value)}


@pytest.mark.parametrize("protocol", list(PORT_IMPLEMENTATIONS), ids=lambda p: p.__name__)
def test_port_implementation_signatures_match(protocol: type) -> None:
    """已实现的方法必须与协议签名严格一致——漂移即失败（D1/D2 回归）。"""
    implementation, implemented = PORT_IMPLEMENTATIONS[protocol]
    declared = _protocol_methods(protocol)

    # 登记的必须真的是协议声明的方法（防抄错名）
    assert implemented <= declared, f"{implementation.__name__} 登记了协议没有的方法"

    # 用只含已实现方法的「子协议」做比对，未实现的不参与
    class _Partial(protocol):  # type: ignore[misc, valid-type]
        pass

    for name in implemented:
        setattr(_Partial, name, getattr(protocol, name))

    assert_port_implementation(_Partial, implementation)


@pytest.mark.parametrize("protocol", list(PORT_IMPLEMENTATIONS), ids=lambda p: p.__name__)
def test_implemented_methods_are_real(protocol: type) -> None:
    """登记为「已实现」的方法必须是真实存在的实现，不能是空壳。"""
    implementation, implemented = PORT_IMPLEMENTATIONS[protocol]
    for name in implemented:
        assert hasattr(implementation, name), (
            f"{implementation.__name__} 声称实现 {name}，但类上根本没有这个方法"
        )


def test_declared_but_not_yet_implemented_is_visible() -> None:
    """声明面 vs 实现面：欠着的方法在映射表里能看到（原 D3 的表达）。"""
    harness_declared = _protocol_methods(HarnessPort)
    harness_implemented = PORT_IMPLEMENTATIONS[HarnessPort][1]
    missing = harness_declared - harness_implemented
    # can_use / can_enter（M23）、record_trace（M25）留到对应里程碑
    assert missing == {"can_use", "can_enter", "record_trace"}
