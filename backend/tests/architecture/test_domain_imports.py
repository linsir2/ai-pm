import ast

from tests.architecture.guards import (
    CONTEXTS,
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
        # 主体集合**取自守卫自己的 CONTEXTS**，不再在测试里抄一份：抄的那份从 M8 起
        # 漏了 exploration（2026-10-05 修），于是 `exploration → import round`
        # 这种反向违规从来没被扫过——守卫只当"目标集"用，单向失效。
        own = rel.parts[0] if rel.parts[0] in CONTEXTS else None
        if own is None:
            continue
        foreign = foreign_context_imports(source, own=own)
        if foreign:
            offenders[str(rel)] = sorted(foreign)
    assert offenders == {}


def test_the_isolation_guard_covers_every_context_as_a_subject():
    """判据自身的覆盖：每个上下文**都要被当主体扫到**，否则它违规没人抓。

    为什么用探针而不是只断 CONTEXTS 集合：集合里有 `discussion` 只能说明它是
    "目标"，不能说明它作为主体被扫过（这正是 M8 漏掉的那一半）。
    """

    for context in CONTEXTS:
        probe = "from pmstudio.domain.round.round import Round\n"
        expected = {"round"} if context != "round" else set()
        assert foreign_context_imports(probe, own=context) == expected

    # 真扫描里，`domain/<上下文>/` 下的文件必须能取到 `own`——抄错目录名就会静默失效
    seen: set[str] = set()
    for path, _source in iter_sources(DOMAIN):
        rel = path.relative_to(DOMAIN)
        if rel.parts[0] in CONTEXTS:
            seen.add(rel.parts[0])
    assert seen == set(CONTEXTS)


def test_domain_uses_absolute_imports():
    """相对导入会让上下文守卫失效，所以 domain 内一律绝对导入。"""
    offenders = []
    for path, source in iter_sources(DOMAIN):
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom) and node.level:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
