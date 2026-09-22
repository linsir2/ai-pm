from tests.architecture.guards import SRC, iter_sources, uses_symbol

WRITE_CAPABILITY = "DocumentWriter"
# 白名单从一开始就是空的：每加一个都必须改这个常量，评审时一眼可见。
ALLOWED: set[str] = {"pmstudio/domain/services/committer.py"}


def test_guard_flags_import_site():
    source = "from pmstudio.domain.ports import DocumentWriter\n"
    assert uses_symbol(source, WRITE_CAPABILITY) is True


def test_guard_ignores_definition_site():
    source = "class DocumentWriter(Protocol):\n    ...\n"
    assert uses_symbol(source, WRITE_CAPABILITY) is False


def test_only_committer_can_use_the_document_write_capability():
    users = {
        path.relative_to(SRC).as_posix()
        for path, source in iter_sources(SRC)
        if uses_symbol(source, WRITE_CAPABILITY)
    }
    assert users == ALLOWED
