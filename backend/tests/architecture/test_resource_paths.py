"""资源目录（提示词 `.md` / 工具声明 `.toml`）的路径**只有一处算**。

## 为什么这条守卫必须存在（2026-10-04 · M5 的教训）

搬目录**不产生任何 import 错误**：`from … import X` 在文件还在原处时照样通过，
`ast` 守卫也抓不到（它只看 import，不看文件存不存在）。炸的时机是**读 `.md` / `.toml`
的那一刻**——而当时有 **5 处各自重算同一路径**：

- `bootstrap/container.py`（装配层拼 `PROMPTS_DIR` / `TOOLS_DIR`）
- `tests/infrastructure/test_llm.py`
- `tests/application/test_analyzer.py`
- `tests/infrastructure/tools/test_catalog.py`
- `tests/infrastructure/seats/test_seats_catalog.py`（席位目录，见下面那条欠账）

搬完只有"碰巧用到"的那一处会红，其余四处**静默地指向一个不存在的目录**。
一个返回空列表的提示词库不会报错，它只会让每一轮模型调用都拿着空提示词——
`Template("").substitute()` 是合法的。那种 bug 能在真跑里安静地跑很久。

所以：路径常量定义在**它所指向的资源旁边**（`harness/prompt.py` 的 `PROMPTS_DIR`、
`harness/tools/registry.py` 的 `DEFINITIONS_DIR`），调用方 import，不许拼。
"""

import ast
from pathlib import Path

from tests.architecture.guards import iter_sources

#: 资源目录与其"读它的模块"。路径必须等于该模块所在目录加上这个相对路径。
RESOURCE_DIRS: dict[str, tuple[str, str]] = {
    "prompts": ("harness/prompt.py", "prompts"),
    "tools/definitions": ("harness/tools/registry.py", "definitions"),
    "seats/definitions": ("infrastructure/seats/catalog.py", "definitions"),
}


def test_each_resource_directory_sits_next_to_the_module_that_reads_it():
    """路径 = 模块所在目录 + 相对路径。**不许由调用方拼**。

    这条红了就说明有人把常量搬回了装配层，或者资源搬走了而常量没跟上。
    """

    package = Path(__file__).resolve().parents[2] / "src" / "pmstudio"

    for name, (module, relative) in RESOURCE_DIRS.items():
        anchor = package / module
        assert anchor.is_file(), f"{name} 的锚点模块不存在：{anchor}"

        actual = anchor.parent / relative
        assert actual.is_dir(), (
            f"{name} 目录不存在：{actual}。搬资源时漏了数据文件？"
            "（注意：这类问题不会产生 import 错误，只在读文件时炸）"
        )


def test_the_prompts_directory_is_not_empty():
    """空提示词目录 = 每一轮都拿着空提示词去调模型，而**不会报错**。"""

    package = Path(__file__).resolve().parents[2] / "src" / "pmstudio"
    prompts = sorted(path.name for path in (package / "harness" / "prompts").glob("*.md"))

    assert prompts, "提示词目录空了——FilePromptLibrary 会返回空串，一切看起来正常"
    assert prompts == [
        "analyzer.md",
        "integrator.md",
        "memory_curator.md",
        "opinion_dispositions.md",
        "seat_review.md",
    ], "提示词清单变了，确认一下是有意为之"


def test_the_tool_definitions_directory_is_not_empty():
    """工具声明空了 = `available()` 恒返回空 = 复述卡上工具栏永远不出现。"""

    package = Path(__file__).resolve().parents[2] / "src" / "pmstudio"
    definitions = sorted(
        path.name for path in (package / "harness" / "tools" / "definitions").glob("*.toml")
    )

    assert definitions, "工具声明目录空了——目录返回空元组，工具栏永远不出现"
    assert definitions == ["attachment.toml", "web_search.toml"], "工具清单变了，确认一下"


def test_no_module_recomputes_a_resource_path_by_hand():
    """**没有任何模块该自己拼资源目录**——除了三处被点名允许的。

    为什么扫源码里的字符串字面量：路径拼错的形式五花八门
    （`parents[2] / "src" / ...`、`Path("a") / "b"`、`os.path.join`），
    但它们都逃不掉一个特征：**源码里出现了资源目录名的字符串**。
    命中的三处是：
    - 本文件自己（`RESOURCE_DIRS` 就是那张表）
    - `harness/prompt.py` 与 `harness/tools/registry.py`（常量的定义处，`__file__` 拼接）
    """

    package = Path(__file__).resolve().parents[2] / "src" / "pmstudio"
    # 键相对 `src/`（与下面算出来的 `relative` 同底）。
    allowed = {
        "pmstudio/harness/prompt.py",  # PROMPTS_DIR 的定义处，它就该拼这一处
        "pmstudio/harness/tools/registry.py",  # DEFINITIONS_DIR 的定义处
    }

    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(package.parent):
        relative = path.relative_to(package.parent).as_posix()
        if relative in allowed:
            continue
        hits = [name for name in RESOURCE_DIRS if f'"{name}"' in source]
        if hits:
            offenders[relative] = hits

    assert offenders == {}, (
        f"这些模块在源码里硬写了资源目录名：{offenders}。"
        "路径应当从 harness 那边 import（PROMPTS_DIR / DEFINITIONS_DIR）"
    )


def test_the_prompt_and_tool_modules_stay_in_harness():
    """这两块属于**运行时控制平面**，不在 `infrastructure`。

    它们原先的位置：`prompt_library` 在 `infrastructure/llm/` 却实现着 harness 的协议
    （`model/contracts.py` 的 `PromptLibrary`），`ToolDefinition` 在 `application/ports.py`
    却只是"一个工具有哪些字段"。M5 纠正了这两处错位。
    """

    package = Path(__file__).resolve().parents[2] / "src" / "pmstudio"

    assert (package / "harness" / "prompt.py").is_file()
    assert (package / "harness" / "tools" / "registry.py").is_file()
    # 旧位置不许留 re-export：留了就等于有两个入口，搬目录的坑会原样回来
    for gone in (
        package / "infrastructure" / "llm" / "prompt_library.py",
        package / "infrastructure" / "tools" / "catalog.py",
    ):
        assert not gone.exists(), f"{gone} 还在：旧路径必须删干净，不留兼容层"


#: 只有 `harness` 自己与 `bootstrap`（装配）可以点名的**实现与路径**。
#: 分清"实现"与"协议"是这条守卫的全部意义：
#: `application` 用 `ToolCatalog` / `ToolDefinition`（协议与数据类）是**正当的**——
#: 它依赖契约，不依赖"工具怎么从 TOML 读"。而 `TomlToolCatalog`（唯一实现）与
#: 两个路径常量一旦被 application 点名，就等于它知道了装配细节。
IMPLEMENTATION_SYMBOLS = frozenset(
    {"TomlToolCatalog", "DEFINITIONS_DIR", "FilePromptLibrary", "PROMPTS_DIR"}
)


def test_the_implementation_and_the_paths_stay_in_harness_and_bootstrap():
    """`TomlToolCatalog` / `FilePromptLibrary` / 两个路径常量：只有 harness 与装配层能点名。

    上一版这条守卫问的是"谁 import 了 `harness.tools.registry`"，那是**问错了**：
    `use_cases.py` import 它完全正当——它要的是 `ToolCatalog` 协议。
    按模块划线会把正确的依赖判成违规，那样的守卫只会被加豁免名单，然后逐渐腐烂。
    按**符号**划线才问到了真问题：谁越过了协议直接摸实现。
    """

    package = Path(__file__).resolve().parents[2] / "src" / "pmstudio"
    offenders: dict[str, list[str]] = {}

    for path, source in iter_sources(package.parent):
        relative = path.relative_to(package.parent).as_posix()
        if relative.startswith(("pmstudio/harness/", "pmstudio/bootstrap/")):
            continue
        hits = sorted(_imported_names(source) & IMPLEMENTATION_SYMBOLS)
        if hits:
            offenders[relative] = hits

    assert offenders == {}, (
        f"这些模块点名了实现或资源路径：{offenders}。"
        "application 只该用协议（ToolCatalog / ToolDefinition），实现由 bootstrap 注入"
    )


def _imported_names(source: str) -> set[str]:
    """这份源码 `from X import a, b` 引进来的那些名字。

    **为什么用 AST 而不是扫字符串**：裸子串会把注释与 docstring 也算进来——
    `memory_curator.py:55` 的注释里就写着「少给一个都会炸（`FilePromptLibrary` 的
    既定行为）」，扫字符串会把它判成违规，而那是个**说明**不是依赖。
    （这一版守卫第一遍就是这么错的，靠它自己红了一次才发现。）
    """

    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names
