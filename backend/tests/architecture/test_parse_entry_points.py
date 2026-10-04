"""解析入口的**数量**本身就是一条不变式：多一个没人调的入口就是空壳。

`AGENTS.md` 明令"未实现的模块不建桩、不建空壳"。这批重构里，"实现了但没有调用者"
是同一类病——所以它必须被机器看见，而不是靠下次谁记得看一眼。

**这条断言为什么不是恒真的**：它两头都卡住——

- 多一个入口（例如有人把 `parse_strict` 加回来）→ `parse_entry_points()` 变大 → 红；
- 少一个入口（有人删了 `load_shell`）→ 变小 → 红；
- 少一个**调用者**（引擎搬进 harness 后没人用了）→ 调用点集合为空 → 红。

历史上真有前两种：2026-10-04 审计发现生产恒走宽松档，而严格档与截断档**只有测试在调**；
严格档还会把溢出量写进 `details`，与 `use_cases` 自己合成的重装指引重了两处。
"""

import ast

from tests.architecture.guards import PACKAGE, SRC

NORMALIZE = SRC / PACKAGE / "harness" / "model" / "normalize.py"

#: 引擎对外暴露的解析入口，恰好这三个。
EXPECTED_ENTRY_POINTS = {"load_shell", "parse_lenient", "parse_lenient_from"}


def parse_entry_points() -> set[str]:
    """`normalize` 里所有"从文本进"或"从载荷进"的公开入口。"""

    tree = ast.parse(NORMALIZE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def production_callers(function: str) -> list[str]:
    """`src/` 里真正调用它的文件（测试不算——测试调用是"想让它存在"，不是"它被需要"）。"""

    callers: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if "harness" not in path.parts and path.name != "normalize.py":
            pass
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == function:
                    callers.append(path.relative_to(SRC).as_posix())
    return callers


def test_the_engine_exposes_exactly_three_entry_points() -> None:
    assert parse_entry_points() == EXPECTED_ENTRY_POINTS, (
        "解析入口的集合变了：多出来的多半是没有调用者的空壳，"
        "少掉的可能是有人正在用的——改这里之前先问'谁调它'"
    )


def test_every_entry_point_has_at_least_one_production_caller() -> None:
    """逐个入口查：只有测试调用的入口就是空壳。

    `parse_lenient` 与 `parse_lenient_from` 共用同一个调用者（`schemas` 的薄封装与
    `structured.call_structured`），这里查的是"至少一个"，不做一一对应。
    """

    for entry in sorted(EXPECTED_ENTRY_POINTS):
        assert production_callers(entry), f"{entry} 在 src/ 里零调用——它是空壳"


def test_normalize_only_depends_on_stdlib_and_pydantic() -> None:
    """引擎不许认识产品：只允许标准库 + `pydantic`（与 `test_harness_imports` 同一口径）。

    注意不能用 `guards.external_imports`——那是 **domain** 的窄 stdlib 白名单
    （`sqlite3` / `json` 出现在 domain 里算设计错误），而 harness 是运行时层，
    `json` / `pydantic` 是它的正当工具。
    """

    import sys

    from tests.architecture.guards import top_level_imports

    # `pmstudio` 自身放行（引擎要抛 `harness.errors.PacketParseError`）；
    # 真正的禁令在 `test_harness_imports.py`：harness 不许反向依赖 application/domain。
    outside = top_level_imports(NORMALIZE.read_text(encoding="utf-8")) - (
        set(sys.stdlib_module_names) | {"pydantic", "pmstudio"}
    )
    assert outside == set()
