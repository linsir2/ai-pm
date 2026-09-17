"""缺失清单必须与实现对得上：列出来的都没做，做了的都没列。"""

import importlib.util
import re
from pathlib import Path

from pmstudio.bootstrap.missing import IMPLEMENTED_MODULES, MISSING_MODULES

# backend/（tests/bootstrap/ 往上两级）
BACKEND = Path(__file__).resolve().parents[2]

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
}

# 已经实现的模块，各自的落点。清单有两个方向："说没做的要真没做"（上面那张表）与
# "说做了的要有落点"（这张表）——少了后一半，把 M27 移出清单就能骗过测试。
IMPLEMENTED_MODULE_PACKAGES = {
    "M11": "pmstudio.communication",
    "M12": "pmstudio.communication",
    "M27": "pmstudio.registry",
}

# 包里可以存在、但**不属于任何缺失模块**的东西：应用函数不占 M 号（directory.md §3.1 把"建项目"
# 放在 L5 的 `tools/services/`，它不是 M1–M30 里的模块）。值指向唯一的证据文件。
#
# 这条登记的**局限**要说清楚：它只把例外点出来，不能证明同层的 M18 / M19 / M20 / M21 还没做。
# 等 M20（文档写入）落地时，这里要收紧成"逐模块的实现文件"判据。
NON_MODULE_IMPLEMENTATIONS = {
    "pmstudio.tools": "pmstudio/tools/services/project_service.py",
}


def test_every_missing_module_really_has_no_implementation() -> None:
    """清单说没做，目录里就不该有它——没有桩、没有空壳（P8）。"""
    for module_number, package in MISSING_MODULE_PACKAGES.items():
        if module_number not in MISSING_MODULES:
            continue
        if package in NON_MODULE_IMPLEMENTATIONS:
            continue  # 这个包里只有非模块的实现，缺失的模块本身还没做（见上面那张表）
        assert importlib.util.find_spec(package) is None, (
            f"{module_number} 还列在缺失清单里，但 {package} 已经存在——该更新清单了"
        )


def test_non_module_implementations_are_registered_and_real() -> None:
    """例外不能变成一句过期的话：登记的包与证据文件都得真的在。"""
    for package, evidence in NON_MODULE_IMPLEMENTATIONS.items():
        assert importlib.util.find_spec(package) is not None, (
            f"{package} 登记了非模块实现，但包不在"
        )
        assert (BACKEND / evidence).exists(), f"{evidence} 不在——这条登记该删了"


def test_implemented_modules_are_not_listed_as_missing() -> None:
    assert IMPLEMENTED_MODULES & set(MISSING_MODULES) == set()


def test_implemented_modules_really_exist() -> None:
    """另一头：说做了的，得有落点。"""
    assert set(IMPLEMENTED_MODULE_PACKAGES) == set(IMPLEMENTED_MODULES)
    for module_number, package in IMPLEMENTED_MODULE_PACKAGES.items():
        assert importlib.util.find_spec(package) is not None, (
            f"{module_number} 说已实现，但 {package} 不在"
        )


def test_the_list_covers_all_thirty_modules() -> None:
    """清单 + 已实现 = 30 个模块编号，不重不漏。"""
    assert IMPLEMENTED_MODULES | set(MISSING_MODULES) == ALL_MODULE_NUMBERS
    assert set(MISSING_MODULES) == ALL_MODULE_NUMBERS - IMPLEMENTED_MODULES


def test_module_numbers_are_well_formed() -> None:
    for number in [*IMPLEMENTED_MODULES, *MISSING_MODULES]:
        assert re.fullmatch(r"M\d+", number), number
