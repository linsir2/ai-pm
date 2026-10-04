from tests.architecture.guards import (
    ALLOWED_LAYER_IMPORTS,
    SRC,
    imported_layers,
    iter_sources,
    layer_of,
    runtime_modules,
    unregistered_packages,
)


def test_guard_detects_layer_dependency():
    source = "from pmstudio.infrastructure.sqlite.repositories import SqliteDocumentRepository\n"
    assert imported_layers(source) == {"infrastructure"}


def test_guard_skips_type_checking_imports():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from pmstudio.bootstrap.container import Container\n"
    )
    assert imported_layers(source) == set()


def test_guard_ignores_outward_stdlib_and_third_party():
    source = "import sqlite3\nfrom fastapi import FastAPI\n"
    assert imported_layers(source) == set()


def test_layers_only_depend_inward():
    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(SRC):
        layer = layer_of(path)
        if layer is None:
            continue
        bad = sorted(imported_layers(source) - ALLOWED_LAYER_IMPORTS[layer])
        if bad:
            offenders[path.relative_to(SRC).as_posix()] = bad
    assert offenders == {}


def test_guard_flags_unregistered_top_level_package(tmp_path):
    """没在 `LAYER_ROOTS` 登记的顶层包 = 那条分层守卫对它**静默跳过**。

    这是 2026-10-04 挖出的真洞：`layer_of()` 认不出的包返回 `None`，
    `test_layers_only_depend_inward` 见 `None` 就 `continue`——
    新加一层如果不登记，它 import `pmstudio.application` 也没人拦，而且**不报错**。
    """

    rogue = tmp_path / "pmstudio" / "rogue"
    rogue.mkdir(parents=True)
    (rogue / "mod.py").write_text(
        "from pmstudio.application.use_cases import StartRound\n", encoding="utf-8"
    )
    assert unregistered_packages(tmp_path) == {"rogue"}


def test_src_has_no_unregistered_package():
    """每加一层都要登记，否则上面那条静默跳过就会生效。"""

    assert unregistered_packages() == frozenset()



def test_agents_never_read_the_document_directly():
    """智能体只许吃 `application/context.py` 渲染出来的文本（计划 §3.13）。

    否则"必读上下文"就散在各处，将来 `PROJECT.md` 接进来时一定会漏读一半。
    """

    agents = SRC / "pmstudio" / "application" / "agents"
    offenders = {
        path.name: sorted(
            module for module in runtime_modules(source) if ".authoring" in module
        )
        for path, source in iter_sources(agents)
        if any(".authoring" in module for module in runtime_modules(source))
    }
    assert offenders == {}
