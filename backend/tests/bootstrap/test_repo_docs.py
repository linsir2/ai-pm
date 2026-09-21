"""仓库表面（README / pyproject）必须与实现对得上。

进度段和依赖声明是最容易烂的两处：改了 `missing.py` 忘了改 README，或者 README 里
写了一条其实跑不起来的命令。**判据只解析结构化行**（`- 已实现（N 个模块）：M…` 与
pyproject 的依赖表），不做模糊匹配——README 的散文怎么写都行。

`missing.py` 的清单由 `test_missing_modules.py` 对着代码核；这里只管另一头：
**README 说的和清单是不是同一件事**。
"""

import re
import tomllib
from pathlib import Path

from pmstudio.bootstrap.missing import IMPLEMENTED_MODULES, MISSING_MODULES

# 仓库根（backend/tests/bootstrap/ 往上三级）
REPO_ROOT = Path(__file__).resolve().parents[3]
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "backend" / "pyproject.toml"

MODULE_ID = re.compile(r"\bM\d+\b")
IMPLEMENTED_LINE = re.compile(r"^- 已实现（(\d+) 个模块）：(.+)$", re.MULTILINE)
MISSING_LINE = re.compile(r"^- 未实现（(\d+) 个模块）：(.+)$", re.MULTILINE)

# README 的「怎么跑」承诺过的工具：必须能在 pyproject 里找到声明，否则那条命令是假的。
DOCUMENTED_DEV_TOOLS = ("pytest", "pytest-asyncio", "ruff")


def _readme_text() -> str:
    assert README.exists(), f"找不到 README：{README}"
    return README.read_text(encoding="utf-8")


def _modules_on(line: str) -> frozenset[str]:
    return frozenset(MODULE_ID.findall(line))


def _declared_requirements() -> set[str]:
    """pyproject 里声明过的包名（主依赖 + dev 组 + optional extra），小写、去版本号。"""
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    raw: list[str] = list(data["project"].get("dependencies", ()))
    for group in data.get("dependency-groups", {}).values():
        raw.extend(item for item in group if isinstance(item, str))
    for extra in data["project"].get("optional-dependencies", {}).values():
        raw.extend(extra)

    return {re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0].strip().lower() for requirement in raw}


def test_readme_progress_matches_the_module_tables() -> None:
    """README 的进度段与 `missing.py` 逐号一致，括号里的数字也要对。"""
    text = _readme_text()

    implemented = IMPLEMENTED_LINE.search(text)
    missing = MISSING_LINE.search(text)
    assert implemented, "README 少了 `- 已实现（N 个模块）：M…` 这一行"
    assert missing, "README 少了 `- 未实现（N 个模块）：M…` 这一行"

    implemented_ids = _modules_on(implemented.group(2))
    missing_ids = _modules_on(missing.group(2))

    assert implemented_ids == frozenset(IMPLEMENTED_MODULES), (
        "README 的已实现模块与 missing.py 不一致："
        f"README 多写 {sorted(implemented_ids - frozenset(IMPLEMENTED_MODULES))}、"
        f"少写 {sorted(frozenset(IMPLEMENTED_MODULES) - implemented_ids)}"
    )
    assert missing_ids == frozenset(MISSING_MODULES), (
        "README 的未实现模块与 missing.py 不一致："
        f"README 多写 {sorted(missing_ids - frozenset(MISSING_MODULES))}、"
        f"少写 {sorted(frozenset(MISSING_MODULES) - missing_ids)}"
    )

    assert int(implemented.group(1)) == len(IMPLEMENTED_MODULES), "README 括号里的数字与清单对不上"
    assert int(missing.group(1)) == len(MISSING_MODULES), "README 括号里的数字与清单对不上"


def test_documented_dev_tools_are_declared_in_pyproject() -> None:
    """「怎么跑」里写的工具必须真的声明过——否则那条命令是跑不起来的假承诺。"""
    text = _readme_text()
    declared = _declared_requirements()

    for tool in DOCUMENTED_DEV_TOOLS:
        assert tool in text, f"README 已经不提 {tool} 了？那这条用例该跟着改（工具：{tool}）"
        assert tool in declared, (
            f"README 让用户跑 {tool}，但 pyproject.toml 没声明它——"
            "加进 [dependency-groups] 的 dev 里，`uv run` 才认得"
        )
