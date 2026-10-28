"""结构性防线的纯函数实现。守卫自己能被测，才不会退化成永远通过。"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "pmstudio"
DOMAIN = SRC / PACKAGE / "domain"
CONTEXTS = ("authoring", "memory", "provenance", "round", "exploration", "discussion")
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
#: `harness` 是最内层，所以它反过来不许依赖这些层里的任何一个（依赖只能朝内）。
FORBIDDEN_HARNESS_LAYERS = ("domain", "application", "infrastructure", "api", "bootstrap")

#: 产品词汇：这些名字属于产品文档，不属于运行时控制平面。
#: **精确匹配**——所以 `MAX_SEAT_ROUNDS` 这种把产品概念藏在复合常量名里的写法它抓不住，
#: 那道盲区由 `test_layers.py` 与 `tests/architecture/test_resource_paths.py` 的分层检查补。
PRODUCT_VOCABULARY = frozenset(
    {
        "ClaimsPacket",
        "SeatPacket",
        "IntegrationPacket",
        "DispositionsPacket",
        "MemoryPacket",
        "SectionId",
        "FeatureSlug",
        "Target",
        "Round",
        "ProjectMemory",
        "DroppedItem",
        "Proposal",
        "seat_id",
        "round_id",
        "proposal_id",
        "section_id",
        "feature_slug",
        "eid",
    }
)

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



# —— no-sqlite 守卫（T10）——

#: 只在运行时/测试源码里扫描的 SQLite 运行时名字。**拆成两段拼**：
#: 守卫自己也在被扫的树里，写成完整字面量会让它抓住自己。
SQLITE_IMPORT_PATTERNS: tuple[str, ...] = (
    "sqlite" + "3",
    "sqlite" + "_vec",
    "sqlite" + "-vec",
)

SQLITE_SQL_PATTERNS: tuple[str, ...] = (
    *(pattern.casefold() for pattern in SQLITE_IMPORT_PATTERNS),
    "vec" + "0",
    "fts" + "5",
    "pra" + "gma",
    "insert" + r"\s+or\b",
    r"\brow" + r"id\b",
    "pmstudio_" + "db",
)

#: ETL 是唯一允许碰 SQLite 的地方：一次性迁移脚本与它的测试。
SQLITE_WHITELIST = frozenset(
    {
        "scripts/" + "migrate_sqlite_to_pg.py",
        "tests/" + "test_etl_cutover.py",
    }
)

#: manifest 只查依赖名：`.toml` / `.lock` 不是 SQL 源码，硬套 SQL 模式会误伤。
#: 同样拆两段拼——守卫测试文件也在被扫的树里。
SQLITE_MANIFEST_PATTERNS: tuple[str, ...] = (
    "sqlite" + "-vec",
    "sqlite" + "_vec",
)

_SQL_PATTERN = re.compile(
    "|".join(f"(?:{pattern})" for pattern in SQLITE_SQL_PATTERNS),
    re.IGNORECASE,
)


def sqlite_manifest_offenders(source: str) -> set[str]:
    """依赖 manifest 里重新引入 SQLite 向量包时返回命中项（大小写不敏感）。"""

    lowered = source.casefold()
    return {pattern for pattern in SQLITE_MANIFEST_PATTERNS if pattern in lowered}


def sqlite_imports(source: str) -> set[str]:
    """AST 找 `import sqlite3` / `from sqlite_vec import …` 这类 import。"""

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            lowered = module.casefold()
            for pattern in SQLITE_IMPORT_PATTERNS:
                if lowered == pattern or lowered.startswith(pattern + "."):
                    found.add(module)
    return found


def _docstring_constants(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def sqlite_sql_literals(source: str) -> dict[str, list[str]]:
    """找非 docstring 字符串里的 SQLite 运行时痕迹；注释不进 AST，天然不扫。"""

    tree = ast.parse(source)
    docstrings = _docstring_constants(tree)
    found: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in docstrings:
            continue
        for match in _SQL_PATTERN.finditer(node.value):
            found.setdefault(match.group(0).casefold(), []).append(node.value)
    return found


def _sql_file_without_comments(text: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in text.splitlines())


def sqlite_offenders(root: Path) -> dict[str, list[str]]:
    """扫源码树：`.py` 走 AST，`.sql` 去掉 `--` 注释后按文本找。

    返回 `相对路径 -> 命中列表`；ETL 脚本/测试与 `__pycache__` 跳过。
    """

    offenders: dict[str, list[str]] = {}
    excluded = {".venv", "__pycache__", "data", ".pytest_cache", ".ruff_cache"}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in excluded for part in relative.parts):
            continue
        relative_text = relative.as_posix()
        if relative_text in SQLITE_WHITELIST:
            continue
        if path.suffix == ".py":
            source = path.read_text(encoding="utf-8")
            hits = sorted(sqlite_imports(source)) + sorted(sqlite_sql_literals(source))
        elif path.suffix == ".sql":
            text = _sql_file_without_comments(path.read_text(encoding="utf-8"))
            hits = sorted({match.group(0) for match in _SQL_PATTERN.finditer(text)})
        else:
            continue
        if hits:
            offenders[relative_text] = sorted(set(hits))
    return offenders


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


def _is_uow_context(expr: ast.expr) -> bool:
    """`with uow:` 与 `with self._uow:` 都算——生产事务几乎全是后者。"""

    if isinstance(expr, ast.Name):
        return expr.id == "uow"
    if isinstance(expr, ast.Attribute):
        return expr.attr in {"uow", "_uow"}
    return False


def await_inside_uow(source: str) -> list[int]:
    """返回"在名字叫 uow 的 with 块里出现 await / async for / async with"的行号。

    这是"模型调用一律在事务外"的机械版本：SQLite 单连接能在事件循环里安全使用，
    前提正是没有任何事务跨越 await。
    """

    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.With):
            continue
        if not any(_is_uow_context(item.context_expr) for item in node.items):
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


# ── harness 专用的两条判据（2026-10-04 抽出）─────────────────────────────────
#
# **为什么抽出来**：`test_harness_never_imports_an_application_layer` 之前把判据写成
# `f"{PACKAGE}.{layer}" in imported`——集合成员判断是**精确相等**，而 `imported` 里
# 装的是完整点分名 `"pmstudio.application.ports"`。于是那个判据**永远为假**，
# 这条守卫从落地起就没触发过，却一直报绿。
#
# 报绿的假守卫比没有守卫更坏：它把"没检查"伪装成"检查过"。而它能潜伏这么久，
# 是因为"守卫自己的判据有没有效"从来没被测过——`tests/architecture/guard_coverage.py`
# 现在补上了这一层（用合成违规输入，不去改 `src/`）。


def harness_layer_violations(source: str) -> set[str]:
    """`harness` 源码 import 了哪些**被禁的层**（应用 / 领域 / 基础设施 / api / 装配）。

    判据是 `imported_layers()` 的**前缀匹配**，不是字符串成员判断——这一点由
    `guard_coverage.py::test_imported_layers_matches_on_prefix_not_equality` 钉死。
    """

    imported = imported_layers(source)
    return {layer for layer in FORBIDDEN_HARNESS_LAYERS if layer in imported}


def code_identifiers(source: str) -> set[str]:
    """代码里出现的标识符：变量 / 参数 / 属性 / 定义名。**不含字符串与注释**。

    为什么不含字符串：注释与 docstring 里经常要**解释**那个词是什么
    （"round_id 只是关联键"），算进去的话守卫会对着正确的解释报违规。
    """

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:
            found.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
    return found


def product_vocabulary_hits(source: str, *, allowed: frozenset[str] = frozenset()) -> set[str]:
    """这份 harness 源码里出现的**产品词汇**标识符，扣掉显式欠账。

    `allowed` 是**按文件**生效的欠账白名单，由调用方（守卫文件）按当前文件收窄——
    一张全局白名单等于把欠账扩散到全层，而那道守卫唯一的职责就是回答
    "harness 有没有开始长业务词汇"。
    """

    return (code_identifiers(source) & PRODUCT_VOCABULARY) - allowed
