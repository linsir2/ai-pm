"""13 章表只能被"standard 的构造者"读，**解读层一律经适配器**（`02` §11.0.69）。

为什么要有这条守卫：切片 2 把就绪度/汇编/导出/提示词都迁到了 `TemplateAdapter`，
但 `domain/shared/sections.py` 还在那儿，谁都可以 `SECTION_BY_ID[sid]` 查一下——
那是一条**绕过模板的后门**：自定义模板里那一章的标题/角色/判据会被 standard 顶掉，
而且不响任何错（查到了就有值，查不到才抛）。

所以：原材料（`SECTIONS` / `SECTION_SLOTS` / `SlotSpec` / `slot_block_id`）照旧可 import
（`template.py` 要拿它们造 standard，`/schema` 要吐静态目录），**查找表与便捷函数不行**。

守卫自己会被测：`tests/architecture/test_guard_coverage.py` 扫的就是这类守卫的有效性。
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

#: 13 章的**查找表与便捷函数**——按章节 id 反查 standard 的规格。
#: 这些是"standard 的一个具体 id 是什么"，引擎读它就等于把 standard 当真相源。
FORBIDDEN = {
    "SECTION_BY_ID",
    "SECTION_IDS",
    "SECTION_TITLES",
    "FEATURE_HOME",
    "OPEN_QUESTION_SECTION",
    "section_spec",
    "section_slot",
}


def _imported_names(path: Path) -> set[str]:
    """这个文件从 `domain.shared.sections` 导入了哪些名字。"""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "pmstudio.domain.shared.sections":
            names |= {alias.name for alias in node.names}
    return names


def test_no_module_reads_the_thirteen_chapter_lookup_tables():
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "sections.py":  # 定义处自己当然可以
            continue
        bad = _imported_names(path) & FORBIDDEN
        if bad:
            offenders.append(f"{path.relative_to(SRC)}: {sorted(bad)}")
    assert offenders == [], (
        "这些模块绕过了模板直接查 13 章表（引擎读文档自己的模板，见 "
        "`domain/shared/template_adapter.py`）：" + "；".join(offenders)
    )


def test_the_tables_still_exist_for_the_standard_builder():
    """守卫不许删原料：`template.py` 造 standard、`/schema` 吐静态目录都要用。

    有人把 `SECTIONS` / `SECTION_SLOTS` 一并删掉时，这条会先红——那时是设计变更，
    不是"顺手清理"。
    """

    from pmstudio.domain.shared.sections import SECTION_SLOTS, SECTIONS
    from pmstudio.domain.shared.template import standard_template

    assert len(SECTIONS) == 13
    assert SECTION_SLOTS
    assert len(standard_template().sections) == 13
