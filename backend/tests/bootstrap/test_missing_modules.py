"""缺失清单必须与实现对得上：列出来的都没做，做了的都没列。

**判据在 R1.2 收紧了一次**：

- R0 按**包**判（`pmstudio.memory` 不存在 → M14–M17 都没做），这在"整层没开工"时够用；
- R1.2 起按**模块的落点文件**判。因为 L4 的 M13 与 L6 的 M24 已经落地，包存在不再等于整层做完——
  继续按包判会把 M14–M17 / M22 / M23 / M25 / M26 一起冤判成"已实现"。
"""

import importlib.util
import re
from pathlib import Path

from pmstudio.bootstrap.missing import IMPLEMENTED_MODULES, MISSING_MODULES

# backend/（tests/bootstrap/ 往上两级）
BACKEND = Path(__file__).resolve().parents[2]

# PRD 附录 A 的模块编号索引（30 个）。
ALL_MODULE_NUMBERS = frozenset(f"M{index}" for index in [*range(1, 31)])

# 前端模块：它们的家在前端（D1），后端没有落点，所以不在这两张表里。
FRONTEND_MODULES = frozenset({"M1", "M2", "M3", "M4", "M5"})

# 已实现 → 落点文件必须存在。
IMPLEMENTED_MODULE_FILES = {
    "M8": "pmstudio/orchestration/consensus.py",
    "M9": "pmstudio/orchestration/cards.py",
    "M11": "pmstudio/communication/board.py",
    "M12": "pmstudio/communication/event_bus.py",
    "M13": "pmstudio/memory/context_assembler.py",
    "M20": "pmstudio/tools/services/document_writer.py",
    "M22": "pmstudio/harness/model_gateway.py",
    "M24": "pmstudio/harness/trimming.py",
    "M26": "pmstudio/harness/retry.py",
    "M27": "pmstudio/registry/entries.py",
    "M28": "pmstudio/orchestration/drafter.py",
}

# 还没做 → **计划落点**不许存在。表里写的名字就是"将来放哪儿"的承诺（语义名，D5）。
MISSING_MODULE_FILES = {
    "M6": "pmstudio/orchestration/roles.py",
    "M7": "pmstudio/orchestration/discussion.py",
    "M10": "pmstudio/orchestration/tool_selection.py",
    "M14": "pmstudio/memory/brief.py",
    "M15": "pmstudio/memory/conversation.py",
    "M16": "pmstudio/memory/retrieval.py",
    "M17": "pmstudio/memory/feedback.py",
    "M18": "pmstudio/tools/capabilities/web_search.py",
    "M19": "pmstudio/tools/capabilities/ingest.py",
    "M21": "pmstudio/tools/services/export.py",
    "M23": "pmstudio/harness/permissions.py",
    "M25": "pmstudio/harness/tracing.py",
    "M29": "pmstudio/orchestration/idea_synthesis.py",
    "M30": "pmstudio/orchestration/promotion.py",
}

# 包里可以存在、但**不属于任何模块**的东西：应用函数不占 M 号（directory.md §3.1 把"建项目"
# 放在 L5 的 `tools/services/`，它不是 M1–M30 里的模块）。值指向唯一的证据文件。
NON_MODULE_IMPLEMENTATIONS = {
    "pmstudio.tools": "pmstudio/tools/services/project_service.py",
}


def test_every_missing_module_has_no_implementation_file() -> None:
    """清单说没做，那个落点文件就不该存在——没有桩、没有空壳（P8）。"""
    for module_number, path in MISSING_MODULE_FILES.items():
        assert module_number in MISSING_MODULES, f"{module_number} 已经落地了，该从这张表里挪走"
        assert not (BACKEND / path).exists(), (
            f"{module_number} 还列在缺失清单里，但 {path} 已经存在——该更新清单了"
        )


def test_every_implemented_module_has_its_file() -> None:
    """另一头：说做了的，得有落点。少了这一半，把模块移出清单就能骗过测试。"""
    for module_number, path in IMPLEMENTED_MODULE_FILES.items():
        assert module_number in IMPLEMENTED_MODULES, f"{module_number} 还在缺失清单里？"
        assert (BACKEND / path).exists(), f"{module_number} 说已实现，但 {path} 不在"


def test_the_two_tables_cover_every_backend_module() -> None:
    """30 = 前端 5 ＋ 后端已实现 ＋ 后端没做，不重不漏；两张表与 `missing.py` 保持一致。"""
    covered = FRONTEND_MODULES | set(IMPLEMENTED_MODULE_FILES) | set(MISSING_MODULE_FILES)
    assert covered == ALL_MODULE_NUMBERS
    assert set(IMPLEMENTED_MODULE_FILES) == set(IMPLEMENTED_MODULES)
    assert set(MISSING_MODULE_FILES) == set(MISSING_MODULES) - FRONTEND_MODULES
    assert set(MISSING_MODULES) >= FRONTEND_MODULES


def test_non_module_implementations_are_registered_and_real() -> None:
    """例外不能变成一句过期的话：登记的包与证据文件都得真的在。"""
    for package, evidence in NON_MODULE_IMPLEMENTATIONS.items():
        assert importlib.util.find_spec(package) is not None, (
            f"{package} 登记了非模块实现，但包不在"
        )
        assert (BACKEND / evidence).exists(), f"{evidence} 不在——这条登记该删了"


def test_implemented_modules_are_not_listed_as_missing() -> None:
    assert IMPLEMENTED_MODULES & set(MISSING_MODULES) == set()


def test_the_list_covers_all_thirty_modules() -> None:
    """清单 + 已实现 = 30 个模块编号，不重不漏。"""
    assert IMPLEMENTED_MODULES | set(MISSING_MODULES) == ALL_MODULE_NUMBERS
    assert set(MISSING_MODULES) == ALL_MODULE_NUMBERS - IMPLEMENTED_MODULES


def test_module_numbers_are_well_formed() -> None:
    for number in [*IMPLEMENTED_MODULES, *MISSING_MODULES]:
        assert re.fullmatch(r"M\d+", number), number
