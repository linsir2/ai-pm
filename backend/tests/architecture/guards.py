"""结构性防线的纯函数实现。守卫自己能被测，才不会退化成永远通过。"""

import ast
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
DOMAIN = SRC / "pmstudio" / "domain"
CONTEXTS = ("authoring", "provenance", "round")
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
