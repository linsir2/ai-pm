import ast

from tests.architecture.guards import (
    DOMAIN,
    external_imports,
    foreign_context_imports,
    iter_sources,
)


def test_guard_detects_external_import():
    assert external_imports("import sqlite3\n") == {"sqlite3"}


def test_guard_allows_stdlib_and_own_package():
    source = "import ast\nfrom pmstudio.domain.shared.ids import EvidenceId\n"
    assert external_imports(source) == set()


def test_guard_detects_foreign_context():
    source = "from pmstudio.domain.round.round import Round\n"
    assert foreign_context_imports(source, own="authoring") == {"round"}


def test_domain_has_no_external_imports():
    offenders = {
        str(path): sorted(mods)
        for path, source in iter_sources(DOMAIN)
        if (mods := external_imports(source))
    }
    assert offenders == {}


def test_domain_contexts_do_not_import_each_other():
    offenders: dict[str, list[str]] = {}
    for path, source in iter_sources(DOMAIN):
        rel = path.relative_to(DOMAIN)
        own = rel.parts[0] if rel.parts[0] in ("authoring", "provenance", "round") else None
        if own is None:
            continue
        foreign = foreign_context_imports(source, own=own)
        if foreign:
            offenders[str(rel)] = sorted(foreign)
    assert offenders == {}


def test_domain_uses_absolute_imports():
    """相对导入会让上下文守卫失效，所以 domain 内一律绝对导入。"""
    offenders = []
    for path, source in iter_sources(DOMAIN):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.level:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
