"""结构性防线的纯函数实现。守卫自己能被测，才不会退化成永远通过。"""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "pmstudio"
DOMAIN = SRC / PACKAGE / "domain"
CONTEXTS = ("authoring", "memory", "provenance", "round")
FORBIDDEN_STEMS = ("fake", "mock", "stub", "dummy")

# 白名单，而不是"所有标准库"：sqlite3 / json / http 这些同样是标准库，
# 但它们属于基础设施，出现在 domain 里就是设计错误（见 05-DDD架构.md §8）。
ALLOWED_STDLIB = frozenset(
    {
        "__future__",
        "abc",
        "ast",
        "collections",
        "copy",
        "dataclasses",
        "datetime",
        "enum",
        "functools",
        "itertools",
        "math",
        "re",
        "types",
        "typing",
        "unicodedata",
    }
)


def iter_sources(root: Path) -> Iterator[tuple[Path, str]]:
    for path in sorted(root.rglob("*.py")):
        yield path, path.read_text(encoding="utf-8")


def top_level_imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


def external_imports(source: str) -> set[str]:
    allowed = ALLOWED_STDLIB | {"pmstudio"}
    return {name for name in top_level_imports(source) if name not in allowed}


def foreign_context_imports(source: str, *, own: str) -> set[str]:
    foreign: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules = [node.module]
        for module in modules:
            parts = module.split(".")
            if len(parts) >= 3 and parts[:2] == ["pmstudio", "domain"]:
                if parts[2] in CONTEXTS and parts[2] != own:
                    foreign.add(parts[2])
    return foreign


def uses_symbol(source: str, symbol: str) -> bool:
    """只认"使用点"（导入 / 名字 / 注解 / 属性），不认定义点（class 语句本身）。"""

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and any(alias.name == symbol for alias in node.names):
            return True
        if isinstance(node, ast.Import) and any(alias.name == symbol for alias in node.names):
            return True
        if isinstance(node, ast.Name) and node.id == symbol:
            return True
        if isinstance(node, ast.Attribute) and node.attr == symbol:
            return True
    return False


def forbidden_names(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        candidates: list[str] = []
        if isinstance(node, ast.Name):
            candidates.append(node.id)
        elif isinstance(node, ast.Attribute):
            candidates.append(node.attr)
        elif isinstance(node, ast.arg):
            candidates.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.append(node.name)
            candidates.extend(arg.arg for arg in node.args.args)
        elif isinstance(node, ast.ClassDef):
            candidates.append(node.name)
        for name in candidates:
            if any(stem in name.lower() for stem in FORBIDDEN_STEMS):
                found.add(name)
    return found


# —— 分层与事务边界（P2） ——

LAYER_ROOTS: dict[str, str] = {
    "harness": PACKAGE + ".harness",
    "domain": PACKAGE + ".domain",
    "application": PACKAGE + ".application",
    "infrastructure": PACKAGE + ".infrastructure",
    "api": PACKAGE + ".api",
    "bootstrap": PACKAGE + ".bootstrap",
}

# 依赖只许朝内指。api 运行期不 import bootstrap（Container 只在 TYPE_CHECKING 里出现，
# 否则 app.py 与 container.py 会成环）。bootstrap 是组装点，谁都可以调。
# harness 是最内层：它只许依赖自己（外加白名单里的 pydantic，见 test_harness_imports.py），
# 因此**没有** domain → harness 这条边——domain 不许碰运行时控制平面。
ALLOWED_LAYER_IMPORTS: dict[str, frozenset[str]] = {
    "harness": frozenset({"harness"}),
    "domain": frozenset({"domain"}),
    "application": frozenset({"application", "domain", "harness"}),
    "infrastructure": frozenset({"infrastructure", "application", "domain", "harness"}),
    "api": frozenset({"api", "application", "domain", "harness"}),
    "bootstrap": frozenset(
        {"bootstrap", "api", "application", "infrastructure", "domain", "harness"}
    ),
}


def layer_of(path: Path, *, root: Path = SRC) -> str | None:
    """这份源码属于哪一层；不在已登记的层里就返回 None（比如 `__init__.py`）。

    注意：返回 None 不代表"检查过了"，只代表"没归任何层"。`unregistered_packages`
    负责把"没归类"这件事变成一次显式失败——否则新加一层就会从这里静默溜过去。
    """

    parts = path.relative_to(root).parts
    if len(parts) < 3 or parts[0] != PACKAGE:
        return None
    return parts[1] if parts[1] in LAYER_ROOTS else None


def unregistered_packages(root: Path = SRC) -> frozenset[str]:
    """`pmstudio/` 下不在 `LAYER_ROOTS` 里的顶层包。

    为什么要有这条：`layer_of()` 认不出的包返回 `None`，而
    `test_layers_only_depend_inward` 见 `None` 就 `continue`——于是新加一层
    如果忘了登记，它 import 任何层都不会被拦，而且**不报错**（2026-10-04 挖出的洞）。
    有了这条，"多一层"必须同时回答"它属于哪层、许不许依赖谁"。
    """

    package = root / PACKAGE
    if not package.is_dir():
        return frozenset()
    return frozenset(
        child.name
        for child in package.iterdir()
        if child.name not in LAYER_ROOTS
        and not child.name.startswith("__")
        and (child.is_dir() or child.suffix == ".py")
    )



def _type_checking_spans(tree: ast.AST) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if not is_type_checking:
            continue
        last = max(
            (getattr(inner, "end_lineno", None) or inner.lineno) for inner in node.body
        )
        spans.append((node.lineno, last))
    return spans


def _inside(line: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= line <= end for start, end in spans)


def runtime_modules(source: str) -> set[str]:
    """运行期真正会执行的 import 的完整模块名（`if TYPE_CHECKING:` 里的不算）。"""

    tree = ast.parse(source)
    spans = _type_checking_spans(tree)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if not _inside(node.lineno, spans))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if not _inside(node.lineno, spans):
                modules.add(node.module)
    return modules


def imported_layers(source: str) -> set[str]:
    layers: set[str] = set()
    for module in runtime_modules(source):
        for layer, root in LAYER_ROOTS.items():
            if module == root or module.startswith(root + "."):
                layers.add(layer)
    return layers


def await_inside_uow(source: str) -> list[int]:
    """返回"在名字叫 uow 的 with 块里出现 await / async for / async with"的行号。

    这是"模型调用一律在事务外"的机械版本：SQLite 单连接能在事件循环里安全使用，
    前提正是没有任何事务跨越 await。
    """

    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.With):
            continue
        if not any(
            isinstance(item.context_expr, ast.Name) and item.context_expr.id == "uow"
            for item in node.items
        ):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Await, ast.AsyncWith, ast.AsyncFor)):
                found.append(inner.lineno)
    return sorted(found)


def await_inside_uow_offenders(root: Path = SRC) -> dict[str, list[int]]:
    """扫**整个 src**（不是只有 `application/`）："模型调用一律在事务外"对每一层都成立。"""

    return {
        path.relative_to(root).as_posix(): lines
        for path, source in iter_sources(root)
        if (lines := await_inside_uow(source))
    }
