"""缺失清单必须与实现对得上：列出来的都没做，做了的都没列。"""

import importlib.util
import re

from pmstudio.bootstrap.missing import IMPLEMENTED_MODULES, MISSING_MODULES

# PRD 附录 A 的模块编号索引（30 个）。
ALL_MODULE_NUMBERS = frozenset(f"M{index}" for index in [*range(1, 31)])

MISSING_MODULE_PACKAGES = {
    "M6": "pmstudio.orchestration",
    "M7": "pmstudio.orchestration",
    "M8": "pmstudio.orchestration",
    "M9": "pmstudio.orchestration",
    "M10": "pmstudio.orchestration",
    "M13": "pmstudio.memory",
    "M14": "pmstudio.memory",
    "M15": "pmstudio.memory",
    "M16": "pmstudio.memory",
    "M17": "pmstudio.memory",
    "M18": "pmstudio.tools",
    "M19": "pmstudio.tools",
    "M20": "pmstudio.tools",
    "M21": "pmstudio.tools",
    "M22": "pmstudio.harness",
    "M23": "pmstudio.harness",
    "M24": "pmstudio.harness",
    "M25": "pmstudio.harness",
    "M26": "pmstudio.harness",
    "M27": "pmstudio.registry",
}


def test_every_missing_module_really_has_no_implementation() -> None:
    """清单说没做，目录里就不该有它——没有桩、没有空壳（P8）。"""
    for module_number, package in MISSING_MODULE_PACKAGES.items():
        if module_number not in MISSING_MODULES:
            continue
        assert importlib.util.find_spec(package) is None, (
            f"{module_number} 还列在缺失清单里，但 {package} 已经存在——该更新清单了"
        )


def test_implemented_modules_are_not_listed_as_missing() -> None:
    assert IMPLEMENTED_MODULES & set(MISSING_MODULES) == set()


def test_the_list_covers_all_thirty_modules() -> None:
    """清单 + 已实现 = 30 个模块编号，不重不漏。"""
    assert IMPLEMENTED_MODULES | set(MISSING_MODULES) == ALL_MODULE_NUMBERS
    assert set(MISSING_MODULES) == ALL_MODULE_NUMBERS - IMPLEMENTED_MODULES


def test_module_numbers_are_well_formed() -> None:
    for number in [*IMPLEMENTED_MODULES, *MISSING_MODULES]:
        assert re.fullmatch(r"M\d+", number), number
