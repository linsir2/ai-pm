"""契约清点：16 个跨层契约都在（14 个在 `models/`，C12 / C13 在 `skeleton/`），C4 不在。

契约名下有哪些模型只有一个出处：`pmstudio/contracts/frozen.py` 的冻结表（R0.6）。
"现实里有没有漏网的模型"从另一头核对，在 `tests/contracts/test_freeze.py`。
"""

import importlib
import pkgutil

from pydantic import BaseModel

import pmstudio.contracts.models as models_package
from pmstudio.contracts.frozen import FROZEN_CONTRACTS
from pmstudio.contracts.skeleton import board as board_module
from pmstudio.contracts.skeleton import events as events_module

# C1–C17 里除掉 C4（层内），剩下 16 个；其中 C12 / C13 是骨架，住在 skeleton/。
SKELETON_CONTRACTS = {"C12", "C13"}
CONTRACT_FAMILIES: dict[str, set[str]] = {
    contract.contract_id: set(contract.models)
    for contract in FROZEN_CONTRACTS
    if contract.contract_id != "C4" and contract.contract_id not in SKELETON_CONTRACTS
}

# 层内契约不许出现在公共契约层（directory.md §3.1）。
LAYER_INTERNAL_NAMES = {"AgentPacket", "Claim", "Packet"}

SKELETON_NAMES = {"RoundRegion", "ContextRegion", "ContextBlock", "ConfirmedRegion", "Event", "EventLogEntry"}


def _model_names() -> set[str]:
    names: set[str] = set()
    for module_info in pkgutil.iter_modules(models_package.__path__):
        module = importlib.import_module(f"{models_package.__name__}.{module_info.name}")
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__.startswith("pmstudio.contracts")
            ):
                names.add(value.__name__)
    return names


def test_all_cross_layer_contract_models_are_present() -> None:
    assert len(CONTRACT_FAMILIES) == 14
    names = _model_names()
    missing = {name for family in CONTRACT_FAMILIES.values() for name in family} - names
    assert missing == set()


def test_skeleton_provides_c12_and_c13() -> None:
    """黑板五区块与八个事件是契约，只是形态是"骨架"。"""
    skeleton = set(vars(board_module)) | set(vars(events_module))
    assert skeleton >= SKELETON_NAMES


def test_orchestration_internal_contract_never_leaks_into_contracts() -> None:
    """C4 是编排层的内部通用语，进了公共目录就再也回不去。"""
    assert _model_names() & LAYER_INTERNAL_NAMES == set()


def test_contract_fields_have_no_unapproved_weak_types() -> None:
    """每个字段都要说得出形状。弱类型只允许出现在批准过的占位处（`claims` 区块），
    而那是类型别名、不是字段——所以模型字段里一个都不许有。"""
    for module_info in pkgutil.iter_modules(models_package.__path__):
        module = importlib.import_module(f"{models_package.__name__}.{module_info.name}")
        for value in vars(module).values():
            if not (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__.startswith("pmstudio.contracts")
            ):
                continue
            for name, field in value.model_fields.items():
                annotation = str(field.annotation)
                assert "Any" not in annotation, f"{value.__name__}.{name}"
                assert annotation != "<class 'object'>", f"{value.__name__}.{name}"
