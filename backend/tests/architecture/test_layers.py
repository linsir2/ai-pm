from tests.architecture.guards import (
    ALLOWED_LAYER_IMPORTS,
    SRC,
    imported_layers,
    iter_sources,
    layer_of,
    runtime_modules,
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
